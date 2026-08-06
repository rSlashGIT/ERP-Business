"""
Phase 7 — Multi-SKU SupplyChainNetwork.

Topology (per SKU, identical to Phase 2 single-SKU):
    supplier (infinite) --5d--> warehouse --2d--> stores A, B, C

Each SKU runs its own 1-supplier-1-warehouse-3-store sub-network with
its own demand series, prices, action map, and (s, S) policy. The
warehouse for each SKU is logically distinct (per-SKU stocking) but
shares physical capacity with all other SKUs — that is the only
multi-SKU coupling, modelled as a soft overflow penalty.

Parameter layout per SKU (10-vec):
    [0:8]   ParameterizedSSPolicy 8-vec, replicated across stores A/B/C
    [8]     raw warehouse-s control (transformed to actual s threshold)
    [9]     raw warehouse-S control (transformed to actual S threshold)

Total parameters: N_SKUS × 10 = 300 dims (with N_SKUS=30, the Phase 7
default). Single-SKU code in multi_echelon.py is NOT modified.

Per-SKU scaling (so the same theta semantics work across volume regimes):
    action_map[i] = mean_demand * ACTION_DAYS[i]    (i in 0..7)
    init_inv_store     = mean_demand * 14           (~2 weeks of supply)
    init_inv_warehouse = mean_demand * 60           (~2 months of supply)
    inv_norm_in_state  = max(100, mean_demand * 25)
    demand_norm_state  = max(20,  mean_demand * 4.5)

Costs are absolute dollars and shared across SKUs:
    HOLDING_COST_FACTOR    = 0.02 / unit / day
    STOCKOUT_PENALTY       = 2.00 / unit (store)
    WAREHOUSE_BACKLOG_PEN  = 0.30 / unit (warehouse)
    OVERFLOW_PENALTY       = 0.10 / unit / day above MAX_WAREHOUSE_CAPACITY
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from hybrid_policy import ParameterizedSSPolicy
from multi_echelon import (
    HOLDING_COST_FACTOR,
    STOCKOUT_PENALTY,
    WAREHOUSE_BACKLOG_PEN,
    LEAD_SUPPLIER_TO_WAREHOUSE,
    LEAD_WAREHOUSE_TO_STORE,
    transform_warehouse_params,
    NodeMetrics,
    STORE_SHARE,
    _store_multiplier,
    split_demand,
)


# ───────────────── multi-SKU constants ─────────────────

STORE_IDS = ('A', 'B', 'C')

# Action units expressed in days-of-mean-demand. Shared across SKUs;
# the per-SKU action_map multiplies these by the SKU's mean demand.
ACTION_DAYS = (0.0, 1.0, 3.0, 7.0, 14.0, 21.0, 30.0, 45.0)
N_ACTIONS   = len(ACTION_DAYS)

# Default Phase 7 capacity. Calibrated so that 30 SKUs at their default
# warehouse anchor (60 * mean_demand_each) sum well below the cap; the
# soft penalty fires only when CMA-ES drives the policy into a regime
# that ignores capacity (which is what we want it to feel).
MAX_WAREHOUSE_CAPACITY_DEFAULT = 10_000.0
OVERFLOW_PENALTY               = 0.10  # $/unit/day over cap

# Per-SKU parameters per CMA-ES vector slot.
PARAMS_PER_SKU = 10  # 8 store policy + 2 warehouse raw

# Service-level floor used by the multi-SKU per-(sku,store) constraint.
SL_FLOOR        = 0.80
SL_PENALTY_GAIN = 1_000_000.0    # per constraint per (gap)^2 unit


# ───────────────── data loading ─────────────────

def load_multi_sku_data(
    csv_path: str = 'data/processed/m5_multi_sku.csv',
    summary_path: str = 'data/processed/m5_multi_sku_summary.json',
) -> Dict:
    """Load the long-format multi-SKU CSV into per-SKU records.

    Returns dict {
        'sku_ids':        ordered list of sku ids,
        'demand_by_sku':  {sku_id: np.ndarray of demand},
        'price_by_sku':   {sku_id: np.ndarray of price (parallel)},
        'mean_by_sku':    {sku_id: float mean demand},
        'unit_cost_by_sku': {sku_id: float (60% of mean price)},
        'min_days':       int (minimum series length across SKUs),
        'summary':        dict from summary JSON,
    }
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    if not os.path.exists(summary_path):
        raise FileNotFoundError(summary_path)
    with open(summary_path) as f:
        summary = json.load(f)

    df = pd.read_csv(csv_path)
    sku_ids: List[str] = list(summary['selected_skus'])
    demand_by_sku: Dict[str, np.ndarray] = {}
    price_by_sku:  Dict[str, np.ndarray] = {}
    mean_by_sku:   Dict[str, float] = {}
    unit_cost_by_sku: Dict[str, float] = {}
    min_days = None

    for sid in sku_ids:
        sub = df[df['sku_id'] == sid].sort_values('day')
        if sub.empty:
            raise RuntimeError(f"SKU {sid} not found in {csv_path}")
        d = sub['demand'].to_numpy(dtype=np.float64)
        p = sub['price'].to_numpy(dtype=np.float64)
        demand_by_sku[sid] = d
        price_by_sku[sid]  = p
        mean_by_sku[sid]   = float(d.mean())
        # Retail-standard unit cost: 60% of mean selling price (matches
        # the Phase 2 calibration in multi_echelon.py).
        unit_cost_by_sku[sid] = 0.60 * float(p.mean())
        if min_days is None or len(d) < min_days:
            min_days = len(d)

    return {
        'sku_ids':          sku_ids,
        'demand_by_sku':    demand_by_sku,
        'price_by_sku':     price_by_sku,
        'mean_by_sku':      mean_by_sku,
        'unit_cost_by_sku': unit_cost_by_sku,
        'min_days':         int(min_days) if min_days is not None else 0,
        'summary':          summary,
    }


# ───────────────── per-SKU configuration ─────────────────

@dataclass
class SKUConfig:
    sku_id: str
    mean_demand: float
    unit_price: float
    unit_cost: float
    action_units: np.ndarray         # length N_ACTIONS, indexed 0..7
    init_inv_store: float
    init_inv_warehouse: float
    inv_norm: float
    demand_norm: float

    @staticmethod
    def from_stats(sku_id: str, mean_demand: float, mean_price: float) -> 'SKUConfig':
        m = max(0.5, float(mean_demand))   # floor mean to avoid degenerate scaling
        action_units = np.asarray([m * d for d in ACTION_DAYS], dtype=np.float64)
        return SKUConfig(
            sku_id=sku_id,
            mean_demand=float(mean_demand),
            unit_price=float(mean_price),
            unit_cost=0.60 * float(mean_price),
            action_units=action_units,
            init_inv_store=m * 14.0,
            init_inv_warehouse=m * 60.0,
            inv_norm=max(100.0, m * 25.0),
            demand_norm=max(20.0, m * 4.5),
        )


# ───────────────── per-SKU node ─────────────────

class _SKUStoreNode:
    """Store-side bookkeeping for a single (sku, store) pair."""
    __slots__ = (
        'sku_id', 'store_id', 'inventory', 'lead_time',
        'unit_price', 'unit_cost', 'inv_norm', 'demand_norm',
        'pending_deliveries', 'demand_window', 'metrics', 'policy',
    )

    def __init__(self, sku_id: str, store_id: str, init_inv: float,
                 lead_time: int, unit_price: float, unit_cost: float,
                 inv_norm: float, demand_norm: float,
                 policy: ParameterizedSSPolicy):
        self.sku_id     = sku_id
        self.store_id   = store_id
        self.inventory  = float(init_inv)
        self.lead_time  = int(lead_time)
        self.unit_price = float(unit_price)
        self.unit_cost  = float(unit_cost)
        self.inv_norm   = float(inv_norm)
        self.demand_norm = float(demand_norm)
        self.pending_deliveries: Dict[int, float] = {}
        self.demand_window = deque(maxlen=10)
        self.metrics = NodeMetrics()
        self.policy = policy

    def receive_deliveries(self, day: int) -> float:
        qty = self.pending_deliveries.pop(day, 0.0)
        self.inventory += qty
        return qty

    def realize_demand(self, demand: float) -> Dict[str, float]:
        sales = min(self.inventory, demand)
        unmet = max(0.0, demand - sales)
        self.inventory -= sales
        revenue = sales * self.unit_price
        stockout_cost = unmet * STOCKOUT_PENALTY
        self.metrics.revenue += revenue
        self.metrics.stockout_cost += stockout_cost
        self.metrics.sales_units += sales
        self.metrics.demand_units += demand
        if unmet > 1e-9:
            self.metrics.stockout_events += 1
        self.demand_window.append(demand)
        return {'sales': sales, 'unmet': unmet,
                'revenue': revenue, 'stockout_cost': stockout_cost}

    def state_dict(self) -> Dict[str, float]:
        if len(self.demand_window) >= 2:
            mean = float(np.mean(self.demand_window))
            std  = float(np.std(self.demand_window))
            cv = std / (mean + 1e-6)
        else:
            mean, cv = 0.0, 0.0
        return {
            'inventory_level': float(np.clip(self.inventory / self.inv_norm, 0.0, 3.0)),
            'demand_forecast': float(np.clip(mean / self.demand_norm, 0.0, 2.0)),
            'demand_std':      float(np.clip((mean / self.demand_norm) * cv, 0.0, 2.0)),
            'lead_time':       float(self.lead_time),
        }

    def record_holding(self) -> float:
        cost = max(0.0, self.inventory) * HOLDING_COST_FACTOR
        self.metrics.holding_cost += cost
        return cost

    def record_procurement(self, units: float) -> float:
        cost = units * self.unit_cost
        self.metrics.procurement_cost += cost
        return cost


class _SKUWarehouseNode:
    """Warehouse-side bookkeeping for a single SKU. Capacity itself is
    enforced jointly across SKUs by MultiSKUNetwork."""
    __slots__ = ('sku_id', 'inventory', 'lead_time', 'unit_cost',
                 'pending_deliveries', 'metrics', 's_lower', 's_upper')

    def __init__(self, sku_id: str, init_inv: float, lead_time: int,
                 unit_cost: float, s_lower: float, s_upper: float):
        self.sku_id = sku_id
        self.inventory = float(init_inv)
        self.lead_time = int(lead_time)
        self.unit_cost = float(unit_cost)
        self.pending_deliveries: Dict[int, float] = {}
        self.metrics = NodeMetrics()
        self.s_lower = float(s_lower)
        self.s_upper = float(s_upper)

    def receive_deliveries(self, day: int) -> float:
        qty = self.pending_deliveries.pop(day, 0.0)
        self.inventory += qty
        return qty

    def ship_to(self, store: _SKUStoreNode, requested: float, arrival_day: int) -> float:
        shipped = min(self.inventory, requested)
        self.inventory -= shipped
        if shipped > 0:
            store.pending_deliveries[arrival_day] = (
                store.pending_deliveries.get(arrival_day, 0.0) + shipped
            )
        return shipped

    def record_holding(self) -> float:
        cost = max(0.0, self.inventory) * HOLDING_COST_FACTOR
        self.metrics.holding_cost += cost
        return cost

    def record_procurement(self, units: float) -> float:
        cost = units * self.unit_cost
        self.metrics.procurement_cost += cost
        return cost


# ───────────────── theta layout helpers ─────────────────

def split_multi_theta(theta: np.ndarray, n_skus: int) -> List[Dict]:
    """Split a flat (n_skus * 10)-vector into per-SKU parts.

    Returns list of {'store_theta': 8-vec, 'warehouse_s': float,
                     'warehouse_S': float} entries, in SKU order.
    """
    arr = np.asarray(theta, dtype=np.float64)
    expected = n_skus * PARAMS_PER_SKU
    if arr.shape != (expected,):
        raise ValueError(f"theta shape {arr.shape} != ({expected},)")
    out: List[Dict] = []
    for i in range(n_skus):
        base = i * PARAMS_PER_SKU
        store_theta = arr[base:base + 8].copy()
        wh = transform_warehouse_params(float(arr[base + 8]),
                                        float(arr[base + 9]))
        out.append({
            'store_theta': store_theta,
            'warehouse_s': wh['warehouse_s'],
            'warehouse_S': wh['warehouse_S'],
        })
    return out


def default_multi_theta(n_skus: int) -> np.ndarray:
    """Stack DEFAULT_THETA + (0, 0) raw warehouse n_skus times."""
    one = np.concatenate([
        ParameterizedSSPolicy.DEFAULT_THETA.copy(),
        np.zeros(2, dtype=np.float64),
    ])
    return np.tile(one, n_skus)


# ───────────────── main network ─────────────────

class MultiSKUNetwork:
    """N_SKUS independent supply chains sharing a single warehouse
    capacity envelope."""

    STORE_IDS = STORE_IDS

    def __init__(
        self,
        sku_configs: List[SKUConfig],
        thetas: Dict[str, np.ndarray],          # {sku_id: 8-vec store policy}
        warehouse_thresholds: Dict[str, Dict],  # {sku_id: {'s': float, 'S': float}}
        demand_by_sku: Dict[str, np.ndarray],
        start_day: int = 0,
        noise_std: float = 0.05,
        seed: int = 42,
        max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
        overflow_penalty: float = OVERFLOW_PENALTY,
        disruption_fn: Optional[Callable[[int], Dict]] = None,
    ):
        self.sku_configs = list(sku_configs)
        self.sku_ids = [c.sku_id for c in sku_configs]
        self.thetas = {sid: np.asarray(thetas[sid], dtype=np.float64)
                       for sid in self.sku_ids}
        self.warehouse_thresholds = {sid: dict(warehouse_thresholds[sid])
                                     for sid in self.sku_ids}
        self.demand_by_sku = {sid: np.asarray(demand_by_sku[sid],
                                              dtype=np.float64)
                              for sid in self.sku_ids}
        self.start_day = int(start_day)
        self.noise_std = float(noise_std)
        self.seed = int(seed)
        self.max_warehouse_capacity = float(max_warehouse_capacity)
        self.overflow_penalty = float(overflow_penalty)
        self._disruption_fn = disruption_fn
        self._current_overrides: Dict = {}

        self._rng = np.random.default_rng(self.seed)

        # Build per-SKU sub-networks.
        self.warehouses: Dict[str, _SKUWarehouseNode] = {}
        # stores keyed (sku_id, store_id)
        self.stores: Dict[str, Dict[str, _SKUStoreNode]] = {}
        for cfg in self.sku_configs:
            sid = cfg.sku_id
            wh = _SKUWarehouseNode(
                sku_id=sid,
                init_inv=cfg.init_inv_warehouse,
                lead_time=LEAD_SUPPLIER_TO_WAREHOUSE,
                unit_cost=cfg.unit_cost,
                s_lower=self.warehouse_thresholds[sid]['warehouse_s'],
                s_upper=self.warehouse_thresholds[sid]['warehouse_S'],
            )
            self.warehouses[sid] = wh

            store_dict: Dict[str, _SKUStoreNode] = {}
            for store_id in STORE_IDS:
                policy = ParameterizedSSPolicy(self.thetas[sid])
                store_dict[store_id] = _SKUStoreNode(
                    sku_id=sid,
                    store_id=store_id,
                    init_inv=cfg.init_inv_store,
                    lead_time=LEAD_WAREHOUSE_TO_STORE,
                    unit_price=cfg.unit_price,
                    unit_cost=cfg.unit_cost,
                    inv_norm=cfg.inv_norm,
                    demand_norm=cfg.demand_norm,
                    policy=policy,
                )
            self.stores[sid] = store_dict

        self.current_day = 0
        self.warehouse_backlog_total = 0.0
        self.overflow_cost_total = 0.0
        self._daily_records: List[dict] = []

    # ── demand helpers ──

    def _base_demand(self, sku_id: str, day: int) -> float:
        series = self.demand_by_sku[sku_id]
        idx = (self.start_day + day) % len(series)
        return float(series[idx])

    def _store_demand(self, sku_id: str, store_id: str, day: int) -> float:
        noise = float(self._rng.normal(0.0, self.noise_std))
        mult = float(self._current_overrides.get('demand_multiplier', 1.0))
        return split_demand(self._base_demand(sku_id, day) * mult,
                            store_id, day, noise)

    def _refresh_overrides(self, day: int) -> Dict:
        if self._disruption_fn is None:
            self._current_overrides = {}
        else:
            ov = self._disruption_fn(day) or {}
            self._current_overrides = dict(ov)
        return self._current_overrides

    # ── action mapping ──

    def _action_units(self, cfg: SKUConfig, action_idx: int) -> float:
        action_idx = int(np.clip(action_idx, 0, N_ACTIONS - 1))
        return float(cfg.action_units[action_idx])

    # ── per-day step ──

    def step(self) -> dict:
        day = self.current_day
        ov = self._refresh_overrides(day)
        supplier_lead  = int(ov.get('supplier_lead',  LEAD_SUPPLIER_TO_WAREHOUSE))
        warehouse_lead = int(ov.get('warehouse_lead', LEAD_WAREHOUSE_TO_STORE))

        # Per-SKU dynamics.
        per_sku: Dict[str, dict] = {}
        for cfg in self.sku_configs:
            sid = cfg.sku_id
            wh  = self.warehouses[sid]
            stores = self.stores[sid]

            # 1) Deliveries arrive.
            wh.receive_deliveries(day)
            for st in stores.values():
                st.receive_deliveries(day)

            # 2) Stores observe customer demand.
            demand_info: Dict[str, dict] = {}
            for store_id, st in stores.items():
                d = self._store_demand(sid, store_id, day)
                demand_info[store_id] = st.realize_demand(d)

            # 3) Stores decide orders.
            store_orders: Dict[str, float] = {}
            for store_id, st in stores.items():
                action_idx = st.policy.act(st.state_dict())
                store_orders[store_id] = self._action_units(cfg, action_idx)

            # 4) Warehouse ships to stores.
            store_shipped: Dict[str, float] = {}
            arrival_store = day + warehouse_lead
            unmet_orders = 0.0
            for store_id, st in stores.items():
                req = store_orders[store_id]
                shipped = wh.ship_to(st, req, arrival_store)
                store_shipped[store_id] = shipped
                unmet_orders += (req - shipped)
                st.record_procurement(shipped)
                if req > 0:
                    st.metrics.orders_placed += 1
            self.warehouse_backlog_total += unmet_orders
            wh.metrics.stockout_cost += unmet_orders * WAREHOUSE_BACKLOG_PEN
            if unmet_orders > 1e-9:
                wh.metrics.stockout_events += 1

            # 5) Warehouse decides own order via (s, S).
            wh_ordered = 0.0
            if wh.inventory < wh.s_lower:
                wh_ordered = wh.s_upper - wh.inventory
                arrival_wh = day + supplier_lead
                wh.pending_deliveries[arrival_wh] = (
                    wh.pending_deliveries.get(arrival_wh, 0.0) + wh_ordered
                )
                wh.record_procurement(wh_ordered)
                wh.metrics.orders_placed += 1

            # 6) Holding cost at every stocking node.
            wh.record_holding()
            for st in stores.values():
                st.record_holding()

            # Per-SKU daily profit for trace.
            daily_store_profit = {}
            for store_id, st in stores.items():
                rev = demand_info[store_id]['revenue']
                stk = demand_info[store_id]['stockout_cost']
                proc = store_shipped[store_id] * st.unit_cost
                hold = st.inventory * HOLDING_COST_FACTOR
                p = rev - stk - proc - hold
                daily_store_profit[store_id] = p
                st.metrics.profit_per_day.append(p)
            wh_proc_today = wh_ordered * wh.unit_cost
            wh_hold_today = wh.inventory * HOLDING_COST_FACTOR
            wh_stockout_today = unmet_orders * WAREHOUSE_BACKLOG_PEN
            wh_daily_profit = -(wh_proc_today + wh_hold_today + wh_stockout_today)
            wh.metrics.profit_per_day.append(wh_daily_profit)

            per_sku[sid] = {
                'wh_inventory_end': wh.inventory,
                'store_inventory_end': {k: stores[k].inventory for k in STORE_IDS},
                'store_demand': {k: (demand_info[k]['sales']
                                     + demand_info[k]['unmet'])
                                 for k in STORE_IDS},
                'store_unmet':  {k: demand_info[k]['unmet']  for k in STORE_IDS},
                'store_orders': dict(store_orders),
                'store_shipped': dict(store_shipped),
                'unmet_store_orders_total': unmet_orders,
                'warehouse_order': wh_ordered,
                'daily_store_profit': daily_store_profit,
                'daily_warehouse_profit': wh_daily_profit,
            }

        # 7) Joint warehouse-capacity overflow penalty (end of day).
        total_wh_inv = sum(self.warehouses[s].inventory for s in self.sku_ids)
        overflow = max(0.0, total_wh_inv - self.max_warehouse_capacity)
        overflow_cost = overflow * self.overflow_penalty
        self.overflow_cost_total += overflow_cost

        day_record = {
            'day': day,
            'total_warehouse_inventory': total_wh_inv,
            'warehouse_overflow_units':  overflow,
            'warehouse_overflow_cost':   overflow_cost,
            'per_sku':                   per_sku,
        }
        # Network daily profit = sum of per-SKU daily profits − overflow cost.
        per_sku_daily = sum(
            r['daily_warehouse_profit'] + sum(r['daily_store_profit'].values())
            for r in per_sku.values()
        )
        day_record['daily_network_profit'] = per_sku_daily - overflow_cost
        self._daily_records.append(day_record)

        self.current_day += 1
        return day_record

    # ── full-episode driver ──

    def simulate(self, n_steps: int = 90) -> Dict:
        for _ in range(n_steps):
            self.step()
        return self.summary()

    def summary(self) -> Dict:
        per_sku_summary: Dict[str, Dict] = {}
        network_profit = 0.0
        total_demand = 0.0
        total_sales = 0.0

        for cfg in self.sku_configs:
            sid = cfg.sku_id
            wh = self.warehouses[sid]
            stores = self.stores[sid]
            store_profit_sum = sum(s.metrics.profit for s in stores.values())
            wh_profit = wh.metrics.profit
            sku_profit = store_profit_sum + wh_profit

            sku_demand = sum(s.metrics.demand_units for s in stores.values())
            sku_sales = sum(s.metrics.sales_units for s in stores.values())
            sku_sl = (sku_sales / sku_demand) if sku_demand > 1e-9 else 1.0

            store_sl = {}
            for store_id in STORE_IDS:
                d = stores[store_id].metrics.demand_units
                s_ = stores[store_id].metrics.sales_units
                store_sl[store_id] = (s_ / d) if d > 1e-9 else 1.0

            per_sku_summary[sid] = {
                'sku_total_profit':    sku_profit,
                'warehouse_profit':    wh_profit,
                'store_profits':       {k: stores[k].metrics.profit for k in STORE_IDS},
                'sku_service_level':   sku_sl,
                'store_service_level': store_sl,
                'sku_demand':          sku_demand,
                'sku_sales':           sku_sales,
                'mean_demand':         cfg.mean_demand,
                'unit_price':          cfg.unit_price,
            }
            network_profit += sku_profit
            total_demand += sku_demand
            total_sales += sku_sales

        # Subtract joint overflow cost from network profit.
        network_profit -= self.overflow_cost_total
        network_sl = (total_sales / total_demand) if total_demand > 1e-9 else 1.0

        return {
            'network_total_profit':   network_profit,
            'service_level':          network_sl,
            'total_demand':           total_demand,
            'total_sales':            total_sales,
            'overflow_cost_total':    self.overflow_cost_total,
            'warehouse_backlog_total': self.warehouse_backlog_total,
            'per_sku':                per_sku_summary,
        }

    @property
    def daily_records(self) -> List[dict]:
        return list(self._daily_records)


# ───────────────── builder helpers ─────────────────

def build_network(
    multi_data: Dict,
    theta: np.ndarray,
    start_day: int = 0,
    seed: int = 42,
    noise_std: float = 0.05,
    max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
    overflow_penalty: float = OVERFLOW_PENALTY,
    disruption_fn: Optional[Callable[[int], Dict]] = None,
) -> MultiSKUNetwork:
    """Convenience: build a MultiSKUNetwork from load_multi_sku_data() output
    and a flat theta vector of length n_skus * PARAMS_PER_SKU."""
    sku_ids = multi_data['sku_ids']
    n_skus = len(sku_ids)
    parts = split_multi_theta(theta, n_skus)
    sku_configs: List[SKUConfig] = []
    thetas: Dict[str, np.ndarray] = {}
    warehouse_thresholds: Dict[str, Dict] = {}
    for i, sid in enumerate(sku_ids):
        mean_d = float(multi_data['mean_by_sku'][sid])
        mean_p = float(np.mean(multi_data['price_by_sku'][sid]))
        cfg = SKUConfig.from_stats(sid, mean_d, mean_p)
        sku_configs.append(cfg)
        thetas[sid] = parts[i]['store_theta']
        warehouse_thresholds[sid] = {
            'warehouse_s': parts[i]['warehouse_s'],
            'warehouse_S': parts[i]['warehouse_S'],
        }
    return MultiSKUNetwork(
        sku_configs=sku_configs,
        thetas=thetas,
        warehouse_thresholds=warehouse_thresholds,
        demand_by_sku=multi_data['demand_by_sku'],
        start_day=start_day,
        noise_std=noise_std,
        seed=seed,
        max_warehouse_capacity=max_warehouse_capacity,
        overflow_penalty=overflow_penalty,
        disruption_fn=disruption_fn,
    )


# ───────────────── CLI smoke test ─────────────────

if __name__ == '__main__':
    md = load_multi_sku_data()
    n = len(md['sku_ids'])
    theta = default_multi_theta(n)
    net = build_network(md, theta, seed=42)
    summary = net.simulate(n_steps=90)
    print(f"n_skus               : {n}")
    print(f"theta dim            : {theta.size}")
    print(f"network_total_profit : ${summary['network_total_profit']:+,.0f}")
    print(f"network_service_level: {summary['service_level']:.1%}")
    print(f"overflow_cost_total  : ${summary['overflow_cost_total']:,.0f}")
    print(f"backlog_total        : {summary['warehouse_backlog_total']:,.0f} units")
    # Top/bottom 3 SKUs by profit.
    items = sorted(summary['per_sku'].items(),
                   key=lambda kv: kv[1]['sku_total_profit'])
    print("\nWorst 3 SKUs by profit:")
    for sid, s in items[:3]:
        print(f"  {sid:<20} profit=${s['sku_total_profit']:+,.0f}  "
              f"SL={s['sku_service_level']:.1%}  mean={s['mean_demand']:.2f}")
    print("\nBest 3 SKUs by profit:")
    for sid, s in items[-3:]:
        print(f"  {sid:<20} profit=${s['sku_total_profit']:+,.0f}  "
              f"SL={s['sku_service_level']:.1%}  mean={s['mean_demand']:.2f}")
