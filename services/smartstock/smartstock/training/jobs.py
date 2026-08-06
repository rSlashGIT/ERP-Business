"""Async fit-job registry and the ERP-payload -> simulator adapter."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..contracts import JobHandle, JobStatus, OptimizeRequest, SkuNodeState
from ..core import policy as P
from ..core.network import NetworkConfig, SkuData
from ..core.recommend import PolicyStore
from ..core.segmentation import SegmentIndex, build_stats
from .fit import FitConfig, evaluate, fit_policy

logger = logging.getLogger(__name__)

MIN_DAYS_FOR_FIT = 120


class JobRegistry:
    """In-memory job store with TTL eviction.

    In a multi-replica deployment this must be backed by Redis instead — the
    contract is unchanged, only `_jobs` moves. Single-replica is the intended
    topology for the fit service because CMA-ES is CPU-bound and you want one
    fit per catalogue, not N.
    """

    def __init__(self, ttl_seconds: int = 86_400) -> None:
        self._jobs: Dict[str, JobHandle] = {}
        self._created: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.ttl = ttl_seconds

    def create(self, job_id: str) -> JobHandle:
        with self._lock:
            self._evict()
            job = JobHandle(
                job_id=job_id,
                status=JobStatus.PENDING,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
            self._jobs[job_id] = job
            self._created[job_id] = time.time()
            return job

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)

    def get(self, job_id: str) -> Optional[JobHandle]:
        with self._lock:
            return self._jobs.get(job_id)

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
            )

    def _evict(self) -> None:
        now = time.time()
        stale = [k for k, t in self._created.items() if now - t > self.ttl]
        for k in stale:
            self._jobs.pop(k, None)
            self._created.pop(k, None)


def build_sku_data(
    items: Sequence[SkuNodeState],
    default_lead_days: float = 7.0,
    default_lead_cv: float = 0.35,
) -> tuple:
    """Adapt an ERP payload into (SkuData, SegmentIndex).

    Only items carrying enough demand history to fit against are used. Items
    are aggregated to SKU level: fitting is about POLICY SHAPE per segment, and
    node-level splits are handled inside the simulator by store_share.
    """
    by_sku: Dict[str, List[float]] = {}
    cost: Dict[str, float] = {}
    price: Dict[str, float] = {}
    lt_mean: Dict[str, float] = {}
    lt_std: Dict[str, float] = {}

    for it in items:
        hist = [float(x) for x in (it.demand_history or []) if x is not None]
        if len(hist) < MIN_DAYS_FOR_FIT:
            continue
        prev = by_sku.get(it.sku_id)
        if prev is None or len(hist) > len(prev):
            by_sku[it.sku_id] = hist
        cost[it.sku_id] = max(float(it.unit_cost or 0.0), cost.get(it.sku_id, 0.0))
        price[it.sku_id] = max(float(it.unit_price or 0.0), price.get(it.sku_id, 0.0))

        obs = [float(o) for o in (it.lead_time_observations or [])]
        contract = (it.supplier.contract_lead_days if it.supplier else None) or default_lead_days
        cv = (it.supplier.contract_lead_cv if it.supplier else None) or default_lead_cv
        if obs:
            lt_mean[it.sku_id] = float(np.mean(obs))
            lt_std[it.sku_id] = float(np.std(obs, ddof=1)) if len(obs) > 1 else cv * contract
        else:
            lt_mean[it.sku_id] = float(contract)
            lt_std[it.sku_id] = float(cv * contract)

    if not by_sku:
        raise ValueError(
            f"no SKU has at least {MIN_DAYS_FOR_FIT} days of demand history; cannot fit"
        )

    sku_ids = sorted(by_sku)
    n_days = min(len(by_sku[s]) for s in sku_ids)
    demand = np.stack([np.asarray(by_sku[s][-n_days:], dtype=np.float64) for s in sku_ids])

    stats = build_stats({s: demand[i] for i, s in enumerate(sku_ids)})
    index = SegmentIndex(stats)

    unit_price = np.array([price.get(s, 1.0) or 1.0 for s in sku_ids])
    unit_cost = np.array([cost.get(s, 0.0) or unit_price[i] * 0.6 for i, s in enumerate(sku_ids)])

    data = SkuData(
        sku_ids=sku_ids,
        demand=demand,
        unit_cost=unit_cost,
        unit_price=unit_price,
        lt_dc_mean=np.array([lt_mean[s] for s in sku_ids]),
        lt_dc_std=np.array([lt_std[s] for s in sku_ids]),
        lt_store_mean=np.full(len(sku_ids), 2.0),
        lt_store_std=np.full(len(sku_ids), 0.5),
        segment_idx=index.index_array(sku_ids),
    )
    return data, index


def run_fit_job(
    registry: JobRegistry, job_id: str, req: OptimizeRequest, store: PolicyStore
) -> None:
    """Background CMA-ES fit. Never raises — failures land on the job handle."""
    registry.update(
        job_id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc).isoformat(),
        message="preparing data",
    )
    try:
        data, index = build_sku_data(req.items)
        cfg = NetworkConfig(
            holding_rate_per_day=req.holding_rate_per_day,
            stockout_multiple=req.stockout_multiple,
            order_fixed_cost=req.order_fixed_cost,
            service_floor=req.service_level_target,
        )
        n_days = data.demand.shape[1]
        horizon = min(180, max(60, n_days // 4))
        train_hi = int(n_days * 0.6)
        fc = FitConfig(
            max_generations=req.max_generations,
            seed=req.seed,
            sku_batch_size=req.sku_batch_size,
            train_window=(0, max(1, train_hi - horizon)),
            test_window=(train_hi, max(train_hi + 1, n_days - horizon - 1)),
            horizon=horizon,
        )
        total = max(1, req.max_generations)

        def progress(gen: int, best: float) -> None:
            registry.update(
                job_id,
                progress=round(min(1.0, gen / total), 3),
                message=f"generation {gen}/{total}, best fitness {best:,.0f}",
            )

        result = fit_policy(cfg, data, index.segments, fc, progress=progress)
        store.load(
            {seg: result.raw_theta[i].tolist() for i, seg in enumerate(result.segments)},
            version=f"fit-{job_id}",
        )
        registry.update(
            job_id,
            status=JobStatus.SUCCEEDED,
            finished_at=datetime.now(timezone.utc).isoformat(),
            progress=1.0,
            message="policy adopted",
            result=result.to_dict(),
        )
        logger.info(
            "fit %s done: %d segments, test cost %.0f vs classical %.0f",
            job_id, len(result.segments),
            result.test_metrics["total_cost"],
            result.baseline_metrics["classical_ss"]["total_cost"],
        )
    except Exception as exc:
        logger.exception("fit job %s failed", job_id)
        registry.update(
            job_id,
            status=JobStatus.FAILED,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=f"{type(exc).__name__}: {exc}",
            message="fit failed; previous policy retained",
        )


def simulate_candidate(body: Dict[str, Any], store: PolicyStore) -> Dict[str, Any]:
    """Evaluate a candidate parameter set against supplied history. What-if only."""
    raw_items = body.get("items") or []
    if not raw_items:
        raise ValueError("items must not be empty")
    items = [SkuNodeState(**it) if isinstance(it, dict) else it for it in raw_items]
    data, index = build_sku_data(items)

    cand = body.get("params")
    if cand:
        theta = np.stack([
            np.asarray(cand.get(seg, P.DEFAULT_RAW), dtype=np.float64)
            for seg in index.segments
        ])
    else:
        theta = np.stack([store.get(seg) for seg in index.segments])

    cfg = NetworkConfig(
        horizon=min(180, data.demand.shape[1] - 1),
        service_floor=float(body.get("service_level_target", 0.95)),
    )
    start = max(0, data.demand.shape[1] - cfg.horizon - 1)
    return {
        "segments": index.segments,
        "sku_count": data.n_sku,
        "metrics": evaluate(theta, cfg, data, start, seed=999),
        "params": {seg: P.describe(theta[i]) for i, seg in enumerate(index.segments)},
    }
