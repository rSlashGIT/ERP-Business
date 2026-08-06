"""
Multi-echelon supply chain network (Phase 2).

Topology:
    supplier (infinite) --5d--> warehouse (classical s=1000, S=3000)
                                    --2d--> store A (50% M5 demand, weekday-heavy)
                                    --2d--> store B (30% M5 demand, sine pattern)
                                    --2d--> store C (20% M5 demand, weekend-heavy)

Each store runs a ParameterizedSSPolicy with its own 8-parameter theta
(24 parameters total across the network). The warehouse uses a fixed
classical (s,S) policy and is NOT optimized.

All prices/costs are drawn from the M5 dataset; demand is split per
store with small Gaussian noise so the stores are not perfectly
correlated.

Do not import this module before reading hybrid_policy.py - it depends on
the existing ParameterizedSSPolicy class without modifying it.
"""

from __future__ import annotations

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


# ───────────────── constants ─────────────────

# Cost constants calibrated for clean M5 data (FOODS_3_090 @ CA_1)
# Selling price: $1.00-$1.60 (mean $1.38)
# Unit cost: ~$0.83 (60% of price, retail standard margin)
# Holding: $0.02/u/day = ~1.5% of unit cost daily, retail standard
# Stockout: $2.00/u = lost margin + goodwill
# Warehouse backlog: $0.30/u = lower than store stockout
# Previous z-scored values (0.05/8.00/0.50) deprecated.
HOLDING_COST_FACTOR   = 0.02   # $/unit/day held
STOCKOUT_PENALTY      = 2.00   # $/unit of unmet customer demand (store)
WAREHOUSE_BACKLOG_PEN = 0.30   # $/unit of unmet store order (warehouse-side)

# Action-index -> units, identical to SupplyChainEnv.action_map so the
# parameterized policy keeps the same scale it was trained on.
ACTION_MAP = {0: 0, 1: 50, 2: 150, 3: 300, 4: 500, 5: 750, 6: 1000, 7: 1500}

# Warehouse default (s,S). Used as fallback when SupplyChainNetwork is
# constructed without explicit warehouse_s_lower/upper, and as the
# anchor for the theta26 raw->actual transform (see
# transform_warehouse_params). Phase 2.8 makes these CMA-ES-learnable
# rather than fixed.
WAREHOUSE_S_LOWER = 1000.0
WAREHOUSE_S_UPPER = 3000.0

# Lead times.
LEAD_SUPPLIER_TO_WAREHOUSE = 5
LEAD_WAREHOUSE_TO_STORE    = 2

# Initial inventory per node.
INIT_INV_WAREHOUSE = 2000.0
INIT_INV_STORE     = 200.0

# Default M5 price / unit cost for FOODS_3_090 @ CA_1 (clean data).
# Mean price $1.38; unit cost = 60% of price = $0.83.
DEFAULT_PRICE     = 1.38
DEFAULT_UNIT_COST = 0.83


# ───────────────── demand splitting ─────────────────

def _weekday_multiplier(day: int, weekday_mult: float, weekend_mult: float) -> float:
    # day 0..4 = Mon..Fri; 5..6 = weekend. Arbitrary origin is fine;
    # only the pattern matters for this simulation.
    return weekday_mult if (day % 7) < 5 else weekend_mult


def _store_multiplier(store_id: str, day: int) -> float:
    if store_id == 'A':
        return _weekday_multiplier(day, 1.2, 0.7)
    if store_id == 'B':
        return 1.0 + 0.3 * math.sin(day * math.pi / 7.0)
    if store_id == 'C':
        return _weekday_multiplier(day, 0.7, 1.5)
    raise ValueError(f"Unknown store_id: {store_id}")


STORE_SHARE = {'A': 0.50, 'B': 0.30, 'C': 0.20}


def split_demand(base_demand: float, store_id: str, day: int, noise: float) -> float:
    """Split total M5 demand into per-store demand with a store-specific
    multiplier and a small Gaussian noise drawn externally."""
    raw = base_demand * STORE_SHARE[store_id] * _store_multiplier(store_id, day)
    return max(0.0, raw * (1.0 + noise))


# ───────────────── M5 data loader ─────────────────

def _load_m5_series(
    data_path: str = 'data/processed/m5_clean.csv',
    scaler_path: Optional[str] = None,
) -> np.ndarray:
    """Return real (unscaled) daily demand for the full M5 series.

    Reads the clean integer-demand CSV produced by
    src/preprocess_m5_raw.py (FOODS_3_090 @ CA_1, 1,941 days, mean
    ≈66/day). The 'demand' column is already in real units — no scaler
    needed. Returned as float64 so the downstream network math (noise
    multiplication, per-store split with fractional shares) stays in a
    single dtype; all values round-trip from the source integers.

    `scaler_path` is retained in the signature for backward compatibility
    with older callers (disruption_engine, train_network_cmaes,
    analyze_segmentation) that pass the legacy .pkl path. It is ignored.
    """
    if not os.path.exists(data_path):
        raise FileNotFoundError(data_path)

    df = pd.read_csv(data_path)
    if 'demand' not in df.columns:
        raise ValueError(
            f"{data_path} has no 'demand' column — expected clean M5 CSV "
            f"from src/preprocess_m5_raw.py"
        )
    return df['demand'].to_numpy(dtype=np.float64)


# ───────────────── Node ─────────────────

@dataclass
class NodeMetrics:
    revenue: float = 0.0
    holding_cost: float = 0.0
    stockout_cost: float = 0.0
    procurement_cost: float = 0.0
    sales_units: float = 0.0
    demand_units: float = 0.0
    stockout_events: int = 0
    orders_placed: int = 0
    profit_per_day: List[float] = field(default_factory=list)

    @property
    def profit(self) -> float:
        return (self.revenue
                - self.holding_cost
                - self.stockout_cost
                - self.procurement_cost)


class Node:
    """Generic supply-chain node. Used for supplier, warehouse and stores."""

    def __init__(
        self,
        node_id: str,
        role: str,                              # 'supplier' | 'warehouse' | 'store'
        initial_inventory: float,
        lead_time_to_upstream: int,
        policy: Optional[ParameterizedSSPolicy] = None,
        demand_fn: Optional[Callable[[int], float]] = None,
        capacity: float = float('inf'),
        unit_price: float = DEFAULT_PRICE,
        unit_cost: float = DEFAULT_UNIT_COST,
    ):
        self.node_id = node_id
        self.role = role
        self.inventory = float(initial_inventory)
        self.lead_time = int(lead_time_to_upstream)
        self.policy = policy
        self.demand_fn = demand_fn
        self.capacity = capacity
        self.unit_price = unit_price
        self.unit_cost = unit_cost
        self.upstream: Optional[Node] = None

        # scheduled deliveries: arrival_day -> qty_units
        self.pending_deliveries: Dict[int, float] = {}
        self.demand_window = deque(maxlen=10)
        self.metrics = NodeMetrics()

    # ── inventory / orders ────────────────────────────────────────────────

    def receive_deliveries(self, day: int) -> float:
        qty = self.pending_deliveries.pop(day, 0.0)
        self.inventory += qty
        return qty

    def realize_demand(self, demand: float) -> Dict[str, float]:
        """Store-level customer demand realization."""
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
        return {'sales': sales, 'unmet': unmet, 'revenue': revenue,
                'stockout_cost': stockout_cost}

    def ship_to(self, other: 'Node', requested: float, arrival_day: int) -> float:
        """Ship up to `requested` units to `other`, scheduled to arrive on
        `arrival_day`. Returns units actually shipped."""
        shipped = min(self.inventory, requested)
        self.inventory -= shipped
        if shipped > 0:
            other.pending_deliveries[arrival_day] = (
                other.pending_deliveries.get(arrival_day, 0.0) + shipped
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

    # ── store-policy helpers ──────────────────────────────────────────────

    def store_state(self) -> Dict[str, float]:
        """Build a normalized state dict compatible with ParameterizedSSPolicy."""
        if len(self.demand_window) >= 2:
            mean = float(np.mean(self.demand_window))
            std = float(np.std(self.demand_window))
            cv = std / (mean + 1e-6)
        else:
            mean, cv = 0.0, 0.0
        return {
            'inventory_level': float(np.clip(self.inventory / 1500.0, 0.0, 3.0)),
            'demand_forecast': float(np.clip(mean / 300.0, 0.0, 2.0)),
            'demand_std':      float(np.clip((mean / 300.0) * cv, 0.0, 2.0)),
            'lead_time':       float(self.lead_time),
        }


# ───────────────── SupplyChainNetwork ─────────────────

class SupplyChainNetwork:
    """1 supplier -> 1 warehouse -> 3 stores, driven by M5 demand."""

    STORE_IDS = ('A', 'B', 'C')

    def __init__(
        self,
        thetas: Optional[Dict[str, np.ndarray]] = None,
        demand_series: Optional[np.ndarray] = None,
        start_day: int = 0,
        noise_std: float = 0.05,
        seed: int = 42,
        data_path: str = 'data/processed/m5_clean.csv',
        scaler_path: Optional[str] = None,
        disruption_fn: Optional[Callable[[int], Dict]] = None,
        warehouse_s_lower: Optional[float] = None,
        warehouse_s_upper: Optional[float] = None,
    ):
        self.seed = int(seed)
        self.noise_std = float(noise_std)
        self.start_day = int(start_day)

        # Warehouse (s, S) thresholds. Default to the module-level
        # constants (legacy 1000/3000) when not supplied; CMA-ES Phase 2.8
        # passes learnable values via the theta26 transform.
        self.warehouse_s_lower = (float(warehouse_s_lower)
                                  if warehouse_s_lower is not None
                                  else WAREHOUSE_S_LOWER)
        self.warehouse_s_upper = (float(warehouse_s_upper)
                                  if warehouse_s_upper is not None
                                  else WAREHOUSE_S_UPPER)
        if self.warehouse_s_upper <= self.warehouse_s_lower:
            raise ValueError(
                f"warehouse_s_upper ({self.warehouse_s_upper}) must be "
                f"strictly greater than warehouse_s_lower "
                f"({self.warehouse_s_lower})"
            )

        # Optional per-day disruption hook. Called once at the start of
        # every step() with the current day index, expected to return a
        # dict that may contain any subset of:
        #   supplier_lead:    int override for supplier->warehouse lead
        #   warehouse_lead:   int override for warehouse->store lead
        #   demand_multiplier: float scaling factor on base demand
        # Missing keys fall back to the module-level defaults. A None
        # disruption_fn preserves the undisrupted behaviour exactly.
        self._disruption_fn = disruption_fn
        self._current_overrides: Dict = {}

        if demand_series is None:
            demand_series = _load_m5_series(data_path, scaler_path)
        self.demand_series = np.asarray(demand_series, dtype=np.float64)

        # thetas: default to DEFAULT_THETA for each store if not supplied.
        if thetas is None:
            thetas = {sid: ParameterizedSSPolicy.DEFAULT_THETA.copy()
                      for sid in self.STORE_IDS}
        self.thetas = {sid: np.asarray(thetas[sid], dtype=np.float64)
                       for sid in self.STORE_IDS}

        # Build nodes.
        self.supplier = Node(
            node_id='supplier',
            role='supplier',
            initial_inventory=float('inf'),
            lead_time_to_upstream=0,
            capacity=float('inf'),
        )
        self.warehouse = Node(
            node_id='warehouse',
            role='warehouse',
            initial_inventory=INIT_INV_WAREHOUSE,
            lead_time_to_upstream=LEAD_SUPPLIER_TO_WAREHOUSE,
        )
        self.warehouse.upstream = self.supplier

        self.stores: Dict[str, Node] = {}
        for sid in self.STORE_IDS:
            s = Node(
                node_id=f'store_{sid}',
                role='store',
                initial_inventory=INIT_INV_STORE,
                lead_time_to_upstream=LEAD_WAREHOUSE_TO_STORE,
                policy=ParameterizedSSPolicy(self.thetas[sid]),
                demand_fn=(lambda d, sid=sid: self._store_demand(d, sid)),
            )
            s.upstream = self.warehouse
            self.stores[sid] = s

        # RNG used for demand noise only. Deterministic given seed.
        self._rng = np.random.default_rng(self.seed)

        self.current_day = 0
        self.warehouse_backlog = 0.0   # cumulative unmet store orders
        self._daily_records: List[dict] = []

    # ── demand helpers ────────────────────────────────────────────────────

    def _base_demand(self, day: int) -> float:
        idx = (self.start_day + day) % len(self.demand_series)
        return float(self.demand_series[idx])

    def _store_demand(self, day: int, store_id: str) -> float:
        noise = float(self._rng.normal(0.0, self.noise_std))
        mult = float(self._current_overrides.get('demand_multiplier', 1.0))
        return split_demand(self._base_demand(day) * mult, store_id, day, noise)

    def _refresh_overrides(self, day: int) -> Dict:
        """Call the disruption hook (if any) and cache the overrides for
        the current day so helpers like _store_demand see the same values
        the step() loop does."""
        if self._disruption_fn is None:
            self._current_overrides = {}
        else:
            ov = self._disruption_fn(day) or {}
            self._current_overrides = dict(ov)
        return self._current_overrides

    # ── per-day step ──────────────────────────────────────────────────────

    def step(self) -> dict:
        day = self.current_day
        ov = self._refresh_overrides(day)
        supplier_lead  = int(ov.get('supplier_lead',  LEAD_SUPPLIER_TO_WAREHOUSE))
        warehouse_lead = int(ov.get('warehouse_lead', LEAD_WAREHOUSE_TO_STORE))

        # 1) Deliveries arrive at every node.
        wh_received = self.warehouse.receive_deliveries(day)
        store_received = {sid: self.stores[sid].receive_deliveries(day)
                          for sid in self.STORE_IDS}

        # 2) Stores observe customer demand.
        demand_info: Dict[str, dict] = {}
        for sid in self.STORE_IDS:
            d = self.stores[sid].demand_fn(day)
            demand_info[sid] = self.stores[sid].realize_demand(d)

        # 3) Stores decide orders via their parameterized policy.
        store_orders: Dict[str, float] = {}
        for sid in self.STORE_IDS:
            st = self.stores[sid]
            action_idx = st.policy.act(st.store_state())
            qty = float(ACTION_MAP[int(action_idx)])
            store_orders[sid] = qty

        # 4) Warehouse ships to stores (sequential, any shortfall -> backlog).
        store_shipped: Dict[str, float] = {}
        arrival_day_store = day + warehouse_lead
        unmet_store_orders = 0.0
        for sid in self.STORE_IDS:
            req = store_orders[sid]
            shipped = self.warehouse.ship_to(
                self.stores[sid], req, arrival_day_store,
            )
            store_shipped[sid] = shipped
            unmet = req - shipped
            unmet_store_orders += unmet
            # Cost to the *store* of the received shipment.
            self.stores[sid].record_procurement(shipped)
            if req > 0:
                self.stores[sid].metrics.orders_placed += 1

        self.warehouse_backlog += unmet_store_orders
        self.warehouse.metrics.stockout_cost += (
            unmet_store_orders * WAREHOUSE_BACKLOG_PEN
        )
        if unmet_store_orders > 1e-9:
            self.warehouse.metrics.stockout_events += 1

        # 5) Warehouse decides own order via (s,S). Phase 2.8: thresholds
        # are instance attributes, set either to the WAREHOUSE_S_* defaults
        # or to learnable values from the theta26 transform.
        wh_ordered = 0.0
        if self.warehouse.inventory < self.warehouse_s_lower:
            wh_ordered = self.warehouse_s_upper - self.warehouse.inventory
            # Schedule delivery (supplier has infinite capacity).
            arrival_wh = day + supplier_lead
            self.warehouse.pending_deliveries[arrival_wh] = (
                self.warehouse.pending_deliveries.get(arrival_wh, 0.0) + wh_ordered
            )
            self.warehouse.record_procurement(wh_ordered)
            self.warehouse.metrics.orders_placed += 1

        # 6) End-of-day holding cost at each stocking node.
        self.warehouse.record_holding()
        for sid in self.STORE_IDS:
            self.stores[sid].record_holding()

        # 7) Compute per-node daily profit and network totals.
        store_profits = {
            sid: (demand_info[sid]['revenue']
                  - demand_info[sid]['stockout_cost']
                  - self.stores[sid].inventory * 0.0   # already recorded in holding
                  - 0.0)
            for sid in self.STORE_IDS
        }
        # Daily profit (not cumulative); use delta of metrics.
        # We instead append today's contributions from the operations above.
        day_record = {
            'day': day,
            'base_demand': self._base_demand(day),
            'store_demand': {sid: demand_info[sid]['sales'] + demand_info[sid]['unmet']
                             for sid in self.STORE_IDS},
            'store_sales':  {sid: demand_info[sid]['sales']  for sid in self.STORE_IDS},
            'store_unmet':  {sid: demand_info[sid]['unmet']  for sid in self.STORE_IDS},
            'store_orders': dict(store_orders),
            'store_shipped': dict(store_shipped),
            'warehouse_order': wh_ordered,
            'warehouse_inventory_end': self.warehouse.inventory,
            'store_inventory_end': {sid: self.stores[sid].inventory
                                    for sid in self.STORE_IDS},
            'unmet_store_orders_total': unmet_store_orders,
        }

        # Per-day store profit (today-only components).
        daily_store_profit = {}
        for sid in self.STORE_IDS:
            rev = demand_info[sid]['revenue']
            stk = demand_info[sid]['stockout_cost']
            proc = store_shipped[sid] * self.stores[sid].unit_cost
            hold = self.stores[sid].inventory * HOLDING_COST_FACTOR
            p = rev - stk - proc - hold
            daily_store_profit[sid] = p
            self.stores[sid].metrics.profit_per_day.append(p)

        wh_proc_today = wh_ordered * self.warehouse.unit_cost
        wh_hold_today = self.warehouse.inventory * HOLDING_COST_FACTOR
        wh_stockout_today = unmet_store_orders * WAREHOUSE_BACKLOG_PEN
        wh_daily_profit = -(wh_proc_today + wh_hold_today + wh_stockout_today)
        self.warehouse.metrics.profit_per_day.append(wh_daily_profit)

        day_record['daily_store_profit'] = daily_store_profit
        day_record['daily_warehouse_profit'] = wh_daily_profit
        day_record['daily_network_profit'] = (
            wh_daily_profit + sum(daily_store_profit.values())
        )
        self._daily_records.append(day_record)

        self.current_day += 1
        return day_record

    # ── full-episode driver ───────────────────────────────────────────────

    def simulate(self, n_steps: int = 90) -> Dict[str, float]:
        for _ in range(n_steps):
            self.step()
        return self.summary()

    def summary(self) -> Dict[str, float]:
        store_profit_sum = sum(self.stores[sid].metrics.profit
                               for sid in self.STORE_IDS)
        wh_profit = self.warehouse.metrics.profit
        network_profit = store_profit_sum + wh_profit

        total_demand = sum(self.stores[sid].metrics.demand_units
                           for sid in self.STORE_IDS)
        total_sales = sum(self.stores[sid].metrics.sales_units
                          for sid in self.STORE_IDS)
        service_level = (total_sales / total_demand) if total_demand > 0 else 1.0

        return {
            'network_total_profit': network_profit,
            'warehouse_profit': wh_profit,
            'store_profits': {sid: self.stores[sid].metrics.profit
                              for sid in self.STORE_IDS},
            'service_level': service_level,
            'total_demand': total_demand,
            'total_sales': total_sales,
            'store_stockout_events': {
                sid: self.stores[sid].metrics.stockout_events
                for sid in self.STORE_IDS
            },
            'warehouse_stockout_events': self.warehouse.metrics.stockout_events,
            'warehouse_backlog_total': self.warehouse_backlog,
            'per_node_holding_costs': {
                'warehouse': self.warehouse.metrics.holding_cost,
                **{f'store_{sid}': self.stores[sid].metrics.holding_cost
                   for sid in self.STORE_IDS},
            },
            'per_node_stockout_costs': {
                'warehouse': self.warehouse.metrics.stockout_cost,
                **{f'store_{sid}': self.stores[sid].metrics.stockout_cost
                   for sid in self.STORE_IDS},
            },
            'per_node_procurement_costs': {
                'warehouse': self.warehouse.metrics.procurement_cost,
                **{f'store_{sid}': self.stores[sid].metrics.procurement_cost
                   for sid in self.STORE_IDS},
            },
        }

    # ── accessors for tests / trace ──────────────────────────────────────

    @property
    def daily_records(self) -> List[dict]:
        return list(self._daily_records)


# ───────────────── theta helpers ─────────────────

def split_theta24(theta24: np.ndarray) -> Dict[str, np.ndarray]:
    """Split a 24-vector into per-store 8-vectors {A, B, C}."""
    arr = np.asarray(theta24, dtype=np.float64)
    if arr.shape != (24,):
        raise ValueError(f"theta24 must be shape (24,), got {arr.shape}")
    return {
        'A': arr[0:8].copy(),
        'B': arr[8:16].copy(),
        'C': arr[16:24].copy(),
    }


def default_theta24() -> np.ndarray:
    return np.concatenate([ParameterizedSSPolicy.DEFAULT_THETA.copy()] * 3)


# ───────────────── theta26 helpers (Phase 2.8 — joint warehouse opt) ─────────────────

# Anchors and slopes for the theta26 raw->actual transform on the
# warehouse (s,S) thresholds. CMA-ES samples in unbounded real space,
# so we map raw values via:
#     s = max(WH_S_FLOOR,        theta[24] * WH_S_SLOPE + WH_S_ANCHOR)
#     S = max(s + WH_GAP_FLOOR,  theta[25] * WH_S_BIG_SLOPE + WH_S_BIG_ANCHOR)
# Anchors of (400, 1000) are deliberately well below the legacy fixed
# (1000, 3000) regime: the over-stocking that drives the −$35k baseline
# loss is largely warehouse holding cost, so we want the search to
# default into a leaner regime and learn its way out only if margin
# rewards it.
WH_S_ANCHOR     = 400.0
WH_S_SLOPE      = 50.0
WH_S_FLOOR      = 50.0
WH_S_BIG_ANCHOR = 1000.0
WH_S_BIG_SLOPE  = 100.0
WH_GAP_FLOOR    = 100.0


def transform_warehouse_params(raw_s: float, raw_S: float) -> Dict[str, float]:
    """Map raw CMA-ES values (real-valued, unbounded) to actual
    warehouse (s, S) thresholds. The transform is monotone in both raw
    inputs, smooth except at the floor clamps, and produces s≈400,
    S≈1000 at raw=(0,0) — the chosen Phase 2.8 default regime.
    The S threshold is also constrained to be at least s+WH_GAP_FLOOR
    so the warehouse never has S<=s (which would order zero units)."""
    s = max(WH_S_FLOOR,
            float(raw_s) * WH_S_SLOPE + WH_S_ANCHOR)
    S = max(s + WH_GAP_FLOOR,
            float(raw_S) * WH_S_BIG_SLOPE + WH_S_BIG_ANCHOR)
    return {'warehouse_s': s, 'warehouse_S': S}


def split_theta26(theta26: np.ndarray) -> Dict[str, np.ndarray]:
    """Split a 26-vector into per-store 8-vectors plus transformed
    warehouse (s, S) thresholds.

    Layout:
        [0:8]   store A theta
        [8:16]  store B theta
        [16:24] store C theta
        [24]    raw warehouse-s control (transformed via
                transform_warehouse_params)
        [25]    raw warehouse-S control (transformed via
                transform_warehouse_params)

    Returns:
        {'A': 8-vec, 'B': 8-vec, 'C': 8-vec,
         'warehouse_s': float, 'warehouse_S': float}
    """
    arr = np.asarray(theta26, dtype=np.float64)
    if arr.shape != (26,):
        raise ValueError(f"theta26 must be shape (26,), got {arr.shape}")
    wh = transform_warehouse_params(float(arr[24]), float(arr[25]))
    return {
        'A': arr[0:8].copy(),
        'B': arr[8:16].copy(),
        'C': arr[16:24].copy(),
        'warehouse_s': wh['warehouse_s'],
        'warehouse_S': wh['warehouse_S'],
    }


def default_theta26() -> np.ndarray:
    """Return the 26-vector default policy.

    The first 24 entries come from default_theta24() (per-store
    DEFAULT_THETA replicated to A/B/C). The last 2 entries are raw
    warehouse controls; both default to 0.0 so that
    transform_warehouse_params maps them to (s=400, S=1000) — leaner
    than the legacy fixed (1000, 3000) but still feasible at default
    store-policy demand.
    """
    return np.concatenate([default_theta24(), np.array([0.0, 0.0],
                                                       dtype=np.float64)])


# ───────────────── CLI smoke test ─────────────────

if __name__ == '__main__':
    net = SupplyChainNetwork(seed=42)
    summary = net.simulate(n_steps=90)
    print(f"network_total_profit : ${summary['network_total_profit']:+,.0f}")
    print(f"service_level        : {summary['service_level']:.1%}")
    print(f"warehouse_profit     : ${summary['warehouse_profit']:+,.0f}")
    for sid, p in summary['store_profits'].items():
        print(f"  store_{sid} profit   : ${p:+,.0f}")
    print(f"warehouse_backlog    : {summary['warehouse_backlog_total']:,.0f} units")
