"""
Vectorised multi-echelon, multi-SKU inventory simulator.

UPGRADE 1 OF 4: MULTI-SKU / MULTI-ECHELON AT SCALE
--------------------------------------------------
Legacy `multi_sku_network.py` looped in Python over SKUs, then over stores,
then over days, and carried one 10-vector per SKU. At 30 SKUs x 90 days that
is ~8k Python-level iterations per policy evaluation, and CMA-ES needs
thousands of evaluations. It does not survive contact with 10_000 SKUs.

This module simulates the entire tensor

        (population, sku, node, day)

with NumPy. The day loop is the ONLY Python loop; population, SKU and node
are all vectorised axes. That means one CMA-ES generation evaluates every
candidate policy against every SKU at every node in a single pass.

TOPOLOGY
--------
    external supplier --L_dc--> node 0 (DC) --L_store--> nodes 1..N-1 (stores)

Customer demand lands at store nodes only. The DC is a pure transhipment
point with its own (s,S). Both arcs use STOCHASTIC lead times drawn per
order from core.leadtime.LeadTimeSampler.

DC RATIONING
------------
When stores collectively request more than the DC holds, we allocate
proportionally to request size (fair share). This is deliberately not
first-come-first-served: FCFS makes the simulation depend on store index
order, which would let CMA-ES exploit an artefact of the array layout.

COST MODEL (all in currency units, not normalised)
--------------------------------------------------
    holding   = on_hand * unit_cost * holding_rate_per_day
    stockout  = unmet_customer_demand * stockout_penalty_per_unit
    backlog   = unmet_store_request  * dc_backlog_penalty_per_unit
    ordering  = fixed_cost per order placed (drives batching / MOQ realism)
    overflow  = max(0, total_dc_units - capacity) * overflow_penalty

Fitness adds a service-level barrier so the optimiser cannot buy a low cost
by simply refusing to stock anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from .leadtime import LeadTimeSampler
from .policy import N_PARAMS, OrderConstraints, order_quantity, target_levels, unpack

logger = logging.getLogger(__name__)

MAX_LEAD_BUFFER = 60          # pipeline depth in days; orders beyond this are clipped
SEASON_FALLBACK = 7           # weekly period; below 4 weeks of data use a rolling window
DEFAULT_STORE_SHARE = (0.50, 0.30, 0.20)


@dataclass
class NetworkConfig:
    """Economics and structure. Everything here comes from the ERP, not code."""

    n_nodes: int = 4                       # 1 DC + 3 stores
    store_share: Tuple[float, ...] = DEFAULT_STORE_SHARE
    holding_rate_per_day: float = 0.0006   # ~22%/yr of unit cost
    stockout_multiple: float = 1.5         # penalty = multiple x unit price
    dc_backlog_penalty: float = 0.30
    order_fixed_cost: float = 25.0
    dc_capacity_units: float = np.inf
    overflow_penalty: float = 0.10
    review_period: int = 1                 # days between review points
    service_floor: float = 0.95
    service_penalty_gain: float = 5.0e4
    lost_sales: bool = True                # True = unmet demand is lost (retail)
    horizon: int = 180
    warmup: int = 30                       # days excluded from cost accounting

    def validate(self) -> None:
        if self.n_nodes < 2:
            raise ValueError("n_nodes must be >= 2 (one DC and at least one store)")
        n_stores = self.n_nodes - 1
        if len(self.store_share) != n_stores:
            raise ValueError(
                f"store_share has {len(self.store_share)} entries, expected {n_stores}"
            )
        tot = float(sum(self.store_share))
        if not np.isclose(tot, 1.0, atol=1e-6):
            raise ValueError(f"store_share must sum to 1.0, got {tot}")
        if self.horizon <= self.warmup:
            raise ValueError("horizon must exceed warmup")
        if self.review_period < 1:
            raise ValueError("review_period must be >= 1 day")


@dataclass
class SkuData:
    """Aligned per-SKU arrays. Index i is the same SKU across every array."""

    sku_ids: Sequence[str]
    demand: np.ndarray            # (n_sku, n_days) realised demand at network level
    unit_cost: np.ndarray         # (n_sku,)
    unit_price: np.ndarray        # (n_sku,)
    lt_dc_mean: np.ndarray        # (n_sku,) supplier -> DC
    lt_dc_std: np.ndarray
    lt_store_mean: np.ndarray     # (n_sku,) DC -> store
    lt_store_std: np.ndarray
    segment_idx: np.ndarray       # (n_sku,) into the segment parameter block
    constraints: Optional[OrderConstraints] = None

    @property
    def n_sku(self) -> int:
        return len(self.sku_ids)

    def validate(self) -> None:
        n = self.n_sku
        for name in ("unit_cost", "unit_price", "lt_dc_mean", "lt_dc_std",
                     "lt_store_mean", "lt_store_std", "segment_idx"):
            arr = getattr(self, name)
            if arr.shape[0] != n:
                raise ValueError(f"{name} has length {arr.shape[0]}, expected {n}")
        if self.demand.shape[0] != n:
            raise ValueError("demand first axis must be n_sku")
        if np.any(self.unit_cost < 0) or np.any(self.unit_price < 0):
            raise ValueError("unit_cost and unit_price must be non-negative")


@dataclass
class SimResult:
    total_cost: np.ndarray        # (pop,)
    holding_cost: np.ndarray
    stockout_cost: np.ndarray
    ordering_cost: np.ndarray
    overflow_cost: np.ndarray
    fill_rate: np.ndarray         # (pop,) units-filled / units-demanded
    service_by_sku: np.ndarray    # (pop, n_sku)
    avg_inventory: np.ndarray     # (pop,)
    orders_placed: np.ndarray     # (pop,)
    fitness: np.ndarray           # (pop,) cost + service barrier

    def best_index(self) -> int:
        return int(np.argmin(self.fitness))


class NetworkSimulator:
    """Simulates `pop` candidate policies over `n_sku` SKUs and `n_nodes` nodes."""

    def __init__(
        self,
        cfg: NetworkConfig,
        data: SkuData,
        seed: Optional[int] = None,
    ) -> None:
        cfg.validate()
        data.validate()
        self.cfg = cfg
        self.data = data
        self.rng = np.random.default_rng(seed)
        self.n_stores = cfg.n_nodes - 1
        self.share = np.asarray(cfg.store_share, dtype=np.float64)

        n = data.n_sku
        # Node-axis lead-time grids: node 0 is the DC arc, 1..N-1 the store arcs.
        lt_mean = np.zeros((n, cfg.n_nodes))
        lt_std = np.zeros((n, cfg.n_nodes))
        lt_mean[:, 0] = data.lt_dc_mean
        lt_std[:, 0] = data.lt_dc_std
        lt_mean[:, 1:] = data.lt_store_mean[:, None]
        lt_std[:, 1:] = data.lt_store_std[:, None]
        self.lt_mean = lt_mean
        self.lt_std = lt_std
        self.sampler = LeadTimeSampler(lt_mean, lt_std, rng=self.rng, max_lead=MAX_LEAD_BUFFER)

        # Forecast inputs are rolling statistics of realised demand. In
        # production these are replaced by the ERP's forecast service; the
        # contract is identical (mean + sigma per SKU per day).
        self.d_hat, self.sigma_d, self.trend = self._rolling_stats(data.demand)
        nz = (data.demand > 0).mean(axis=1)
        self.intermittency = np.clip(1.0 - nz, 0.0, 1.0)

        stockout_pen = cfg.stockout_multiple * np.maximum(data.unit_price, 0.01)
        self.stockout_pen = stockout_pen
        self.holding_per_unit_day = np.maximum(data.unit_cost, 0.0) * cfg.holding_rate_per_day
        with np.errstate(divide="ignore", invalid="ignore"):
            self.holding_ratio = np.where(
                stockout_pen > 0, self.holding_per_unit_day / stockout_pen * 100.0, 0.0
            )
        self.holding_ratio = np.clip(np.nan_to_num(self.holding_ratio), 0.0, 5.0)

    # ---------------- forecast surrogate ----------------

    @staticmethod
    def _rolling_stats(demand: np.ndarray, window: int = 28) -> Tuple[np.ndarray, ...]:
        """Causal one-step forecast mean / sigma / trend. Shape (n_sku, n_days).

        Uses core.forecast.SeasonalDamped.predict_series when the series is long
        enough -- the same model that serves production recommendations, so the
        policy is fitted against the forecast quality it will actually see.
        Falls back to a causal rolling window for short series.

        Causal matters: a centred window would leak future demand into the
        policy and inflate every benchmark number in this repo.
        """
        try:
            from .forecast import SeasonalDamped
            n_sku, n_days = demand.shape
            if n_days >= 4 * SEASON_FALLBACK:
                model = SeasonalDamped()
                d_hat = np.zeros_like(demand, dtype=np.float64)
                sigma = np.zeros_like(demand, dtype=np.float64)
                for i in range(n_sku):
                    mu, sd = model.predict_series(demand[i])
                    d_hat[i] = mu
                    sigma[i] = sd
                trend = np.zeros_like(demand, dtype=np.float64)
                w = window
                if n_days > 2 * w:
                    prev = np.zeros_like(demand)
                    cs = np.cumsum(np.concatenate([np.zeros((n_sku, 1)), demand], axis=1), axis=1)
                    for t in range(2 * w, n_days):
                        prev[:, t] = (cs[:, t - w + 1] - cs[:, t - 2 * w + 1]) / w
                    with np.errstate(divide="ignore", invalid="ignore"):
                        trend = np.clip((d_hat - prev) / np.maximum(prev, 1e-6), -1.0, 1.0)
                    trend[:, : 2 * w] = 0.0
                gm = demand.mean(axis=1, keepdims=True)
                d_hat[:, :7] = np.maximum(d_hat[:, :7], gm * 0.5)
                return d_hat, np.nan_to_num(sigma), np.nan_to_num(trend)
        except Exception:
            logger.warning("forecast model unavailable; using rolling window", exc_info=True)
        n_sku, n_days = demand.shape
        d_hat = np.zeros_like(demand, dtype=np.float64)
        sigma = np.zeros_like(demand, dtype=np.float64)
        trend = np.zeros_like(demand, dtype=np.float64)
        csum = np.cumsum(np.concatenate([np.zeros((n_sku, 1)), demand], axis=1), axis=1)
        csq = np.cumsum(np.concatenate([np.zeros((n_sku, 1)), demand ** 2], axis=1), axis=1)
        for t in range(n_days):
            lo = max(0, t - window + 1)
            k = t - lo + 1
            m = (csum[:, t + 1] - csum[:, lo]) / k
            v = (csq[:, t + 1] - csq[:, lo]) / k - m ** 2
            d_hat[:, t] = m
            sigma[:, t] = np.sqrt(np.maximum(v, 0.0))
            if t >= 2 * window:
                prev = (csum[:, t - window + 1] - csum[:, max(0, t - 2 * window + 1)]) / window
                trend[:, t] = np.clip((m - prev) / np.maximum(prev, 1e-6), -1.0, 1.0)
        # Seed the warm-up region with the global mean so day 0 is not zero.
        gm = demand.mean(axis=1, keepdims=True)
        d_hat[:, :7] = np.maximum(d_hat[:, :7], gm * 0.5)
        return d_hat, sigma, trend

    # ---------------- simulation ----------------

    def run(
        self,
        raw_theta: np.ndarray,        # (pop, n_segments, N_PARAMS) or (n_segments, N_PARAMS)
        start_day: int = 0,
        collect_trace: bool = False,
    ) -> SimResult:
        cfg = self.cfg
        d = self.data
        theta = np.asarray(raw_theta, dtype=np.float64)
        if theta.ndim == 2:
            theta = theta[None, ...]
        if theta.ndim != 3 or theta.shape[-1] != N_PARAMS:
            raise ValueError(
                f"raw_theta must be (pop, n_seg, {N_PARAMS}); got {theta.shape}"
            )
        pop, n_seg, _ = theta.shape
        n_sku = d.n_sku
        n_node = cfg.n_nodes

        seg_idx = np.clip(d.segment_idx, 0, n_seg - 1)
        bounded = unpack(theta)                       # (pop, n_seg, P)
        params = bounded[:, seg_idx, :]               # (pop, n_sku, P)

        horizon = min(cfg.horizon, d.demand.shape[1] - start_day)
        if horizon <= cfg.warmup:
            raise ValueError(
                f"not enough demand data: horizon {horizon} <= warmup {cfg.warmup}"
            )

        # ---- state tensors ----
        on_hand = np.zeros((pop, n_sku, n_node))
        backorder = np.zeros((pop, n_sku, n_node))
        pipeline = np.zeros((pop, n_sku, n_node, MAX_LEAD_BUFFER + 1))

        # Initial stock: two weeks of demand at stores, six weeks at the DC.
        base = np.maximum(self.d_hat[:, start_day], 1e-6)
        on_hand[:, :, 0] = (base * 42.0)[None, :]
        for j in range(self.n_stores):
            on_hand[:, :, 1 + j] = (base * 14.0 * self.share[j])[None, :]

        # Pre-built scatter indices for the pipeline writes. Built once, not
        # per day. NOTE: we index the BASE array `pipeline` directly -- an
        # earlier version did pipeline[:, :, 1:, :].reshape(...) which is a
        # non-contiguous view, so reshape silently COPIED and every np.add.at
        # write landed in a discarded temporary. Orders vanished and fill rate
        # collapsed to ~1%. Never scatter into a reshaped non-contiguous view.
        gp_s, gs_s, gn_s = np.meshgrid(
            np.arange(pop), np.arange(n_sku), np.arange(1, n_node), indexing="ij"
        )
        gp_d, gs_d = np.meshgrid(np.arange(pop), np.arange(n_sku), indexing="ij")

        holding = np.zeros(pop)
        stockout = np.zeros(pop)
        ordering = np.zeros(pop)
        overflow = np.zeros(pop)
        backlog_cost = np.zeros(pop)
        demand_units = np.zeros((pop, n_sku))
        filled_units = np.zeros((pop, n_sku))
        inv_accum = np.zeros(pop)
        n_orders = np.zeros(pop)

        review = cfg.review_period
        cost_from = cfg.warmup

        for t in range(horizon):
            day = start_day + t
            counted = t >= cost_from

            # 1. receipts
            arriving = pipeline[..., 0].copy()
            on_hand += arriving
            pipeline[..., :-1] = pipeline[..., 1:]
            pipeline[..., -1] = 0.0

            # 2. customer demand at stores
            total_d = d.demand[:, day]                                   # (n_sku,)
            store_d = total_d[None, :, None] * self.share[None, None, :]  # (1,n_sku,n_store)
            store_d = np.broadcast_to(store_d, (pop, n_sku, self.n_stores))

            avail = on_hand[:, :, 1:]
            sold = np.minimum(avail, store_d)
            unmet = store_d - sold
            on_hand[:, :, 1:] = avail - sold

            if not cfg.lost_sales:
                backorder[:, :, 1:] += unmet

            if counted:
                demand_units += store_d.sum(axis=2)
                filled_units += sold.sum(axis=2)
                stockout += (unmet.sum(axis=2) * self.stockout_pen[None, :]).sum(axis=1)

            # 3. review point?
            if t % review == 0:
                ip = on_hand + pipeline.sum(axis=3) - backorder

                dh = self.d_hat[:, day][None, :]
                sd = self.sigma_d[:, day][None, :]
                tr = self.trend[:, day][None, :]
                interm = self.intermittency[None, :]
                hr = self.holding_ratio[None, :]

                # --- store echelon ---
                store_share_b = self.share[None, None, :]
                dh_store = dh[..., None] * store_share_b
                sd_store = sd[..., None] * np.sqrt(store_share_b)
                ltm_s = self.lt_mean[None, :, 1:]
                lts_s = self.lt_std[None, :, 1:]
                p_store = params[:, :, None, :]

                s_st, S_st, _ = target_levels(
                    p_store, dh_store, sd_store, ltm_s, lts_s,
                    review_period=review,
                    trend=tr[..., None],
                    intermittency=interm[..., None],
                    holding_ratio=hr[..., None],
                )
                req = order_quantity(ip[:, :, 1:], s_st, S_st, None, integer=False)

                # --- DC rationing (proportional fair share) ---
                dc_stock = np.maximum(on_hand[:, :, 0], 0.0)
                req_tot = req.sum(axis=2)
                short = req_tot > dc_stock
                with np.errstate(divide="ignore", invalid="ignore"):
                    scale = np.where(short, dc_stock / np.maximum(req_tot, 1e-9), 1.0)
                shipped = req * scale[..., None]
                unmet_req = req_tot - shipped.sum(axis=2)
                on_hand[:, :, 0] -= shipped.sum(axis=2)
                if counted:
                    backlog_cost += unmet_req.sum(axis=1) * cfg.dc_backlog_penalty

                # COMMON RANDOM NUMBERS. Every candidate policy in this
                # generation faces the SAME lead-time realisation. CMA-ES is
                # rank-based, so what matters is the ordering of candidates;
                # giving each its own random draw injects noise that has
                # nothing to do with policy quality and slows convergence.
                # With CRN, two identical thetas score identically and any
                # fitness gap is genuinely attributable to the parameters.
                lt_common = self.sampler.sample((n_sku, n_node))
                lt_s = np.broadcast_to(lt_common[None, :, 1:], (pop, n_sku, self.n_stores))
                np.add.at(pipeline, (gp_s, gs_s, gn_s, lt_s), shipped)

                # --- DC echelon (orders the external supplier) ---
                ltm_d = self.lt_mean[None, :, 0]
                lts_d = self.lt_std[None, :, 0]
                s_dc, S_dc, _ = target_levels(
                    params, dh, sd, ltm_d, lts_d,
                    review_period=review, trend=tr,
                    intermittency=interm, holding_ratio=hr,
                )
                # DC covers the whole network, so scale the targets up by the
                # number of downstream echelons it protects.
                s_dc = s_dc * 1.0
                S_dc = S_dc * 1.6
                cons = d.constraints
                q_dc = order_quantity(ip[:, :, 0], s_dc, S_dc, cons, integer=True)

                lt_d = np.broadcast_to(lt_common[None, :, 0], (pop, n_sku))
                np.add.at(pipeline, (gp_d, gs_d, 0, lt_d), q_dc)

                if counted:
                    placed = (q_dc > 0).sum(axis=1) + (shipped > 0).any(axis=2).sum(axis=1)
                    ordering += placed * cfg.order_fixed_cost
                    n_orders += placed

            # 4. carrying cost + capacity
            if counted:
                oh_units = np.maximum(on_hand, 0.0)
                holding += (oh_units.sum(axis=2) * self.holding_per_unit_day[None, :]).sum(axis=1)
                inv_accum += oh_units.sum(axis=(1, 2))
                if np.isfinite(cfg.dc_capacity_units):
                    over = np.maximum(on_hand[:, :, 0].sum(axis=1) - cfg.dc_capacity_units, 0.0)
                    overflow += over * cfg.overflow_penalty

        counted_days = max(1, horizon - cost_from)
        with np.errstate(divide="ignore", invalid="ignore"):
            service_by_sku = np.where(
                demand_units > 0, filled_units / np.maximum(demand_units, 1e-9), 1.0
            )
        total_demand = demand_units.sum(axis=1)
        fill_rate = np.where(
            total_demand > 0, filled_units.sum(axis=1) / np.maximum(total_demand, 1e-9), 1.0
        )

        total = holding + stockout + ordering + overflow + backlog_cost

        # Service barrier. Quadratic in the shortfall below the floor, summed
        # over SKUs so one catastrophically starved SKU cannot hide inside a
        # good network average.
        gap = np.maximum(cfg.service_floor - service_by_sku, 0.0)
        barrier = cfg.service_penalty_gain * np.square(gap).sum(axis=1)
        fitness = total + barrier

        return SimResult(
            total_cost=total,
            holding_cost=holding,
            stockout_cost=stockout + backlog_cost,
            ordering_cost=ordering,
            overflow_cost=overflow,
            fill_rate=fill_rate,
            service_by_sku=service_by_sku,
            avg_inventory=inv_accum / counted_days,
            orders_placed=n_orders,
            fitness=fitness,
        )
