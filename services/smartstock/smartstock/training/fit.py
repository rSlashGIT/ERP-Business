"""
Policy fitting: CMA-ES over segment-level parameters.

The objective evaluates an ENTIRE CMA-ES population against a batch of SKUs
in one vectorised simulator call. That is the whole performance story: one
generation of lambda=14 candidates over 30 SKUs x 180 days costs ~0.12s.

SKU SUBSAMPLING
---------------
At 10k+ SKUs the (pop, sku, node, lead_buffer) pipeline tensor stops fitting
in cache and then in RAM. `sku_batch_size` draws a fresh random subset each
generation. This makes the objective stochastic, which CMA-ES tolerates well
(it is a rank-based method — only the ORDERING of candidates matters, and the
ordering is stable under a shared batch because every candidate in a
generation sees the SAME SKUs and the SAME lead-time draws).

Both of those "same" clauses are load-bearing. Re-seeding per candidate would
make the comparison noise-dominated and the fit would not converge.

TRAIN / TEST SPLIT
------------------
Fitting uses `train_window`; reported metrics come from a disjoint
`test_window`. Without this, a policy that memorises the demand realisation
looks excellent and ships garbage.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..core.network import NetworkConfig, NetworkSimulator, SimResult, SkuData
from ..core.policy import DEFAULT_RAW, N_PARAMS, PARAM_NAMES, describe
from ..optim.cmaes import minimize

logger = logging.getLogger(__name__)


@dataclass
class FitConfig:
    max_generations: int = 60
    popsize: Optional[int] = None
    sigma0: float = 0.6
    seed: int = 42
    sku_batch_size: Optional[int] = None     # None = all SKUs every generation
    train_window: Tuple[int, int] = (0, 400)
    test_window: Tuple[int, int] = (400, 700)
    horizon: int = 180
    n_restarts: int = 0                      # IPOP-style restarts with doubled popsize


@dataclass
class FitResult:
    raw_theta: np.ndarray                    # (n_segments, N_PARAMS)
    segments: List[str]
    train_fitness: float
    test_metrics: Dict[str, float]
    baseline_metrics: Dict[str, Dict[str, float]]
    generations: int
    evaluations: int
    wall_seconds: float
    history: List[float]
    stop_reason: str
    params_by_segment: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "raw_theta": self.raw_theta.tolist(),
            "segments": self.segments,
            "train_fitness": float(self.train_fitness),
            "test_metrics": self.test_metrics,
            "baseline_metrics": self.baseline_metrics,
            "generations": self.generations,
            "evaluations": self.evaluations,
            "wall_seconds": round(self.wall_seconds, 3),
            "history": [float(h) for h in self.history],
            "stop_reason": self.stop_reason,
            "params_by_segment": self.params_by_segment,
            "param_names": list(PARAM_NAMES),
        }


def _subset(data: SkuData, idx: np.ndarray) -> SkuData:
    return SkuData(
        sku_ids=[data.sku_ids[i] for i in idx],
        demand=data.demand[idx],
        unit_cost=data.unit_cost[idx],
        unit_price=data.unit_price[idx],
        lt_dc_mean=data.lt_dc_mean[idx],
        lt_dc_std=data.lt_dc_std[idx],
        lt_store_mean=data.lt_store_mean[idx],
        lt_store_std=data.lt_store_std[idx],
        segment_idx=data.segment_idx[idx],
        constraints=None if data.constraints is None else type(data.constraints)(
            moq=data.constraints.moq[idx],
            order_multiple=data.constraints.order_multiple[idx],
            max_order=data.constraints.max_order[idx],
            max_position=data.constraints.max_position[idx],
        ),
    )


def _metrics(res: SimResult, i: int = 0) -> Dict[str, float]:
    return {
        "total_cost": round(float(res.total_cost[i]), 2),
        "holding_cost": round(float(res.holding_cost[i]), 2),
        "stockout_cost": round(float(res.stockout_cost[i]), 2),
        "ordering_cost": round(float(res.ordering_cost[i]), 2),
        "fill_rate": round(float(res.fill_rate[i]), 5),
        "avg_inventory_units": round(float(res.avg_inventory[i]), 1),
        "orders_placed": int(res.orders_placed[i]),
        "worst_sku_service": round(float(res.service_by_sku[i].min()), 5),
        "fitness": round(float(res.fitness[i]), 2),
    }


def evaluate(
    raw_theta: np.ndarray,
    cfg: NetworkConfig,
    data: SkuData,
    start_day: int,
    seed: int = 12345,
) -> Dict[str, float]:
    """Single-policy evaluation on a fixed window with a fixed RNG seed."""
    sim = NetworkSimulator(cfg, data, seed=seed)
    res = sim.run(raw_theta[None, ...], start_day=start_day)
    return _metrics(res, 0)


def baseline_naive(n_segments: int) -> np.ndarray:
    """Order-up-to = 7 days cover, z = 0. What a spreadsheet does."""
    raw = np.array([-6.0, -0.6931, -6.0, -0.6931, -1.7, -6.0, -6.0, -1.6, -6.0, -1.0986])
    return np.tile(raw, (n_segments, 1))


def baseline_classical(n_segments: int) -> np.ndarray:
    """Textbook (s,S): z = 1.645 (95% CSL), 14 days cycle stock, no learning."""
    return np.tile(DEFAULT_RAW, (n_segments, 1))


def fit_policy(
    cfg: NetworkConfig,
    data: SkuData,
    segments: List[str],
    fit_cfg: Optional[FitConfig] = None,
    progress: Optional[Callable[[int, float], None]] = None,
) -> FitResult:
    """Fit segment-level policy parameters and benchmark against baselines."""
    fc = fit_cfg or FitConfig()
    n_seg = len(segments)
    if n_seg == 0:
        raise ValueError("no segments to fit")
    n_sku = data.n_sku
    if n_sku == 0:
        raise ValueError("no SKUs to fit")

    train_cfg = NetworkConfig(**{**cfg.__dict__, "horizon": fc.horizon})
    dim = n_seg * N_PARAMS
    t0 = time.time()
    rng = np.random.default_rng(fc.seed)

    train_lo, train_hi = fc.train_window
    max_start = max(train_lo, min(train_hi, data.demand.shape[1] - fc.horizon - 1))

    gen_counter = {"g": 0}

    def objective(pop_raw: np.ndarray) -> np.ndarray:
        gen_counter["g"] += 1
        g = gen_counter["g"]
        # Same SKU batch + same simulator seed for every candidate in this
        # generation. Required for a meaningful rank comparison.
        if fc.sku_batch_size and fc.sku_batch_size < n_sku:
            idx = rng.choice(n_sku, size=fc.sku_batch_size, replace=False)
            batch = _subset(data, np.sort(idx))
        else:
            batch = data
        start = int(rng.integers(train_lo, max_start + 1)) if max_start > train_lo else train_lo
        sim = NetworkSimulator(train_cfg, batch, seed=fc.seed * 1000 + g)
        theta = pop_raw.reshape(pop_raw.shape[0], n_seg, N_PARAMS)
        try:
            res = sim.run(theta, start_day=start)
        except Exception:
            logger.exception("simulator failed in generation %d; penalising population", g)
            return np.full(pop_raw.shape[0], 1e15)
        return res.fitness

    x0 = np.tile(DEFAULT_RAW, n_seg).astype(np.float64)
    best_x, best_f, hist, gens, evals, stop = None, np.inf, [], 0, 0, ""
    popsize = fc.popsize

    for attempt in range(fc.n_restarts + 1):
        result = minimize(
            objective,
            x0=x0 if attempt == 0 else x0 + rng.normal(0, 1.0, dim),
            sigma0=fc.sigma0,
            max_generations=fc.max_generations,
            popsize=popsize,
            seed=fc.seed + attempt,
            callback=(lambda g, f, x: progress(g, f)) if progress else None,
        )
        gens += result.generations
        evals += result.evaluations
        hist.extend(result.history)
        if result.f_best < best_f:
            best_f, best_x, stop = result.f_best, result.x_best, result.stop_reason
        if popsize:
            popsize *= 2
        elif attempt == 0:
            popsize = (4 + int(np.floor(3 * np.log(dim)))) * 2

    assert best_x is not None
    theta = best_x.reshape(n_seg, N_PARAMS)

    # ---- honest out-of-sample evaluation ----
    test_start = int(np.clip(fc.test_window[0], 0, data.demand.shape[1] - fc.horizon - 1))
    eval_cfg = NetworkConfig(**{**cfg.__dict__, "horizon": fc.horizon})
    test = evaluate(theta, eval_cfg, data, test_start, seed=999)
    base = {
        "naive_7day": evaluate(baseline_naive(n_seg), eval_cfg, data, test_start, seed=999),
        "classical_ss": evaluate(baseline_classical(n_seg), eval_cfg, data, test_start, seed=999),
    }

    return FitResult(
        raw_theta=theta,
        segments=segments,
        train_fitness=float(best_f),
        test_metrics=test,
        baseline_metrics=base,
        generations=gens,
        evaluations=evals,
        wall_seconds=time.time() - t0,
        history=hist,
        stop_reason=stop,
        params_by_segment={s: describe(theta[i]) for i, s in enumerate(segments)},
    )
