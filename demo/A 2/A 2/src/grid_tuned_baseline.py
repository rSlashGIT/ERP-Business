#!/usr/bin/env python3
"""
Phase 4.5 — Grid-tuned classical (s, S) baseline.

Exhaustive grid search over warehouse and store (s, S) thresholds
on the *training* portion of the clean M5 series only. The winning
combination becomes a third policy in the disruption sweep so the
Phase 4 result (CMA-ES dominates default classical 6/6) can be
distinguished between

    H1: CMA-ES finds a better point on the same Pareto frontier
    H2: CMA-ES shifts the frontier itself

A tuned-but-non-robust classical baseline lets us tell which.

Grid (PRD-locked, 1728 combinations):
    warehouse_s   ∈ [50, 100, 200, 300, 400, 500, 700, 1000]
    warehouse_gap ∈ [100, 200, 300, 500, 800, 1500]   (S = s + gap)
    store_s       ∈ [10, 20, 30, 50, 80, 120]         (physical units)
    store_gap     ∈ [10, 20, 40, 70, 120, 200]        (physical units)

The warehouse thresholds are physical units consumed directly via the
SupplyChainNetwork(warehouse_s_lower=..., warehouse_s_upper=...) kwargs
that Phase 2.8 added. The store thresholds are physical units, but the
ParameterizedSSPolicy state is normalized (inventory_level = inv/1500),
so we encode each (store_s, store_S) pair as

    s_base = store_s / 1500.0
    S_base = nearest_action_index(store_S - store_s) + s_base

This makes the trigger condition `inventory < store_s` and, on order,
the action index closest in physical units to (store_S - store_s)
under ACTION_MAP.

Selection rule: maximize mean training-split profit subject to the
80% per-store service-level floor (same SL constraint as the locked
Phase-2.8 production model). Cells violating the floor are still
recorded but cannot win.

Outputs:
    models/grid_tuned_classical.json        full grid + winner
    models/grid_tuned_classical_theta.npy   26-vec policy file

Run:
    python src/grid_tuned_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from itertools import product
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from multi_echelon import (                          # noqa: E402
    ACTION_MAP,
    SupplyChainNetwork,
    _load_m5_series,
    transform_warehouse_params,
)


# ───────────────── knobs ─────────────────

MODELS_DIR              = 'models'
TRAIN_FRACTION          = 0.6        # PRD: days 0..0.6*N for grid fitting
EPISODES_PER_CELL       = 16
EPISODE_LENGTH          = 90
BASE_SEED               = 42
SL_FLOOR_PER_STORE      = 0.80       # PRD: 80% per-store SL constraint

WAREHOUSE_S_CANDIDATES   = [50, 100, 200, 300, 400, 500, 700, 1000]
WAREHOUSE_GAP_CANDIDATES = [100, 200, 300, 500, 800, 1500]
STORE_S_CANDIDATES       = [10, 20, 30, 50, 80, 120]
STORE_GAP_CANDIDATES     = [10, 20, 40, 70, 120, 200]


# ───────────────── theta encoding ─────────────────

def _nearest_action_index(qty_units: float) -> int:
    """Return the action index in ACTION_MAP whose order quantity is
    closest to the requested physical qty."""
    keys   = list(ACTION_MAP.keys())
    diffs  = [abs(ACTION_MAP[k] - float(qty_units)) for k in keys]
    return int(keys[int(np.argmin(diffs))])


def encode_store_theta(store_s: float, store_S: float) -> np.ndarray:
    """Encode a physical (s, S) pair as an 8-vec ParameterizedSSPolicy
    theta. The 6 context-weight slots are zeroed so the resulting
    policy is a flat (constant-threshold) classical (s, S) policy."""
    s_base = float(store_s) / 1500.0
    gap    = float(store_S) - float(store_s)
    k      = _nearest_action_index(gap)
    S_base = float(k) + s_base
    return np.array([s_base, S_base, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    dtype=np.float64)


def reverse_warehouse_raw(warehouse_s: float, warehouse_S: float
                          ) -> Tuple[float, float]:
    """Inverse of transform_warehouse_params for the chosen physical
    (s, S). Falls back to the floor-respecting solution when one of
    the floors was active during the forward transform.

    Forward (multi_echelon.transform_warehouse_params):
        s = max(WH_S_FLOOR=50,            raw_s * 50  + 400)
        S = max(s + WH_GAP_FLOOR=100,     raw_S * 100 + 1000)

    For the warehouse_s side: we need raw_s such that
        max(50, raw_s * 50 + 400) == warehouse_s
    If warehouse_s > 50, raw_s = (warehouse_s - 400) / 50.
    If warehouse_s == 50, raw_s can be anything ≤ -7.0 (floor active);
    we pick -7.0 as a deterministic representative value.

    For the warehouse_S side, similarly:
    If warehouse_S > warehouse_s + 100, raw_S = (warehouse_S - 1000) / 100.
    If warehouse_S == warehouse_s + 100, the gap floor was active; we pick
    the smallest raw_S that hits the floor: (warehouse_s + 100 - 1000) / 100.
    """
    if warehouse_s <= 50.0 + 1e-9:
        raw_s = -7.0
    else:
        raw_s = (float(warehouse_s) - 400.0) / 50.0

    gap = warehouse_S - warehouse_s
    if gap <= 100.0 + 1e-9:
        raw_S = (float(warehouse_s) + 100.0 - 1000.0) / 100.0
    else:
        raw_S = (float(warehouse_S) - 1000.0) / 100.0
    return raw_s, raw_S


def build_grid_theta26(store_s: float, store_S: float,
                       warehouse_s: float, warehouse_S: float
                       ) -> np.ndarray:
    """Construct the 26-vec policy file payload from the grid winner.
    Stores all share the same encoded theta (per PRD)."""
    store_theta = encode_store_theta(store_s, store_S)
    raw_s, raw_S = reverse_warehouse_raw(warehouse_s, warehouse_S)
    return np.concatenate([store_theta, store_theta, store_theta,
                           np.array([raw_s, raw_S], dtype=np.float64)])


# ───────────────── single-cell evaluation ─────────────────

def _evaluate_cell(
    store_theta: np.ndarray,
    warehouse_s: float,
    warehouse_S: float,
    demand_series: np.ndarray,
    n_episodes: int,
    episode_length: int,
    cell_seed: int,
) -> Dict[str, float]:
    """Run n_episodes paired episodes for one grid cell. Returns mean
    profit, std, and per-store mean service level. Each cell uses an
    independent seed sequence (cell_seed) so the (start_day, env_seed)
    plan is the same for every cell — episodes are paired across the
    grid so noise differences cancel between cells."""
    rng = np.random.default_rng(int(cell_seed))
    max_start = max(0, len(demand_series) - episode_length - 1)

    profits: List[float] = []
    sl_per_ep: Dict[str, List[float]] = {sid: [] for sid in ('A', 'B', 'C')}

    for _ in range(n_episodes):
        start_day = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        env_seed  = int(rng.integers(0, 2**31 - 1))
        thetas = {sid: store_theta.copy() for sid in ('A', 'B', 'C')}
        net = SupplyChainNetwork(
            thetas=thetas,
            demand_series=demand_series,
            start_day=start_day,
            seed=env_seed,
            warehouse_s_lower=float(warehouse_s),
            warehouse_s_upper=float(warehouse_S),
        )
        summary = net.simulate(n_steps=episode_length)
        profits.append(float(summary['network_total_profit']))
        for sid in ('A', 'B', 'C'):
            d = net.stores[sid].metrics.demand_units
            s = net.stores[sid].metrics.sales_units
            sl_per_ep[sid].append(s / d if d > 1e-9 else 1.0)

    mean_sl = {sid: float(np.mean(sl_per_ep[sid])) for sid in ('A', 'B', 'C')}
    return {
        'mean_profit':   float(np.mean(profits)),
        'std_profit':    float(np.std(profits)),
        'mean_sl_A':     mean_sl['A'],
        'mean_sl_B':     mean_sl['B'],
        'mean_sl_C':     mean_sl['C'],
        'min_store_sl':  float(min(mean_sl.values())),
    }


# ───────────────── main grid sweep ─────────────────

def run_grid(
    train_series: np.ndarray,
    base_seed: int = BASE_SEED,
) -> Tuple[List[Dict], Dict]:
    """Run the full grid and return (all_cells, winner)."""
    cells_total = (len(WAREHOUSE_S_CANDIDATES)
                   * len(WAREHOUSE_GAP_CANDIDATES)
                   * len(STORE_S_CANDIDATES)
                   * len(STORE_GAP_CANDIDATES))
    print(f"  grid cells:      {cells_total} "
          f"({len(WAREHOUSE_S_CANDIDATES)}×{len(WAREHOUSE_GAP_CANDIDATES)}×"
          f"{len(STORE_S_CANDIDATES)}×{len(STORE_GAP_CANDIDATES)})")
    print(f"  episodes/cell:   {EPISODES_PER_CELL}   "
          f"episode length: {EPISODE_LENGTH}")
    print(f"  SL floor:        {SL_FLOOR_PER_STORE:.0%} per store")
    print(f"  total episodes:  {cells_total * EPISODES_PER_CELL:,}")

    all_cells: List[Dict] = []
    cell_idx = 0
    feasible_count = 0
    best: Dict = {'mean_profit': -float('inf'), 'cell_idx': None}
    last_print = time.time()

    for ws, wg, ss, sg in product(WAREHOUSE_S_CANDIDATES,
                                  WAREHOUSE_GAP_CANDIDATES,
                                  STORE_S_CANDIDATES,
                                  STORE_GAP_CANDIDATES):
        cell_idx += 1
        warehouse_S = ws + wg
        store_S     = ss + sg
        store_theta = encode_store_theta(ss, store_S)

        # Each cell gets a deterministic but distinct seed so the
        # 16-episode plan is paired across cells (same offset/seed list
        # for every cell — see _evaluate_cell for how cell_seed drives
        # the rng).
        result = _evaluate_cell(
            store_theta=store_theta,
            warehouse_s=ws,
            warehouse_S=warehouse_S,
            demand_series=train_series,
            n_episodes=EPISODES_PER_CELL,
            episode_length=EPISODE_LENGTH,
            cell_seed=base_seed,
        )
        feasible = (result['min_store_sl'] >= SL_FLOOR_PER_STORE)
        cell_record = {
            'cell_idx':      cell_idx,
            'warehouse_s':   ws,
            'warehouse_S':   warehouse_S,
            'warehouse_gap': wg,
            'store_s':       ss,
            'store_S':       store_S,
            'store_gap':     sg,
            'store_theta':   store_theta.tolist(),
            'feasible':      feasible,
            **result,
        }
        all_cells.append(cell_record)
        if feasible:
            feasible_count += 1
            if result['mean_profit'] > best['mean_profit']:
                best = {**cell_record}

        # progress print every 5 seconds
        if time.time() - last_print > 5.0:
            print(f"    [{cell_idx:>4}/{cells_total}]  "
                  f"feasible={feasible_count:>4}  "
                  f"best_profit="
                  f"{(best['mean_profit'] if best['cell_idx'] is not None else float('nan')):>+12,.0f}")
            last_print = time.time()

    print(f"  done: {feasible_count}/{cells_total} cells respect "
          f"the {SL_FLOOR_PER_STORE:.0%} per-store SL floor")

    if best['cell_idx'] is None:
        raise RuntimeError(
            f"No grid cell satisfied the {SL_FLOOR_PER_STORE:.0%} "
            f"per-store SL floor — the grid is too tight. Either widen "
            f"the candidate set or relax the constraint."
        )
    return all_cells, best


# ───────────────── persistence ─────────────────

def main() -> int:
    t0 = time.time()
    print("Phase 4.5 — Grid-tuned classical (s, S) baseline")
    print("=" * 88)

    full_series = _load_m5_series()
    split_idx = int(len(full_series) * TRAIN_FRACTION)
    train_series = full_series[:split_idx]
    print(f"  data:            data/processed/m5_clean.csv")
    print(f"  total days:      {len(full_series)}")
    print(f"  train (60%):     days [0, {split_idx})  "
          f"({len(train_series)} days)")

    all_cells, best = run_grid(train_series=train_series, base_seed=BASE_SEED)

    # ── log feasible top-5 ──
    feasible = [c for c in all_cells if c['feasible']]
    feasible.sort(key=lambda c: c['mean_profit'], reverse=True)
    print(f"\n  top-5 feasible cells by mean profit:")
    for c in feasible[:5]:
        print(f"    profit={c['mean_profit']:>+12,.0f}  "
              f"min_sl={c['min_store_sl']:.1%}  "
              f"warehouse=({c['warehouse_s']:>4},{c['warehouse_S']:>5})  "
              f"store=({c['store_s']:>3},{c['store_S']:>4})")

    print(f"\n  WINNER: warehouse=({best['warehouse_s']}, "
          f"{best['warehouse_S']})  "
          f"store=({best['store_s']}, {best['store_S']})")
    print(f"          mean_profit  = ${best['mean_profit']:+,.0f}")
    print(f"          per-store SL = "
          f"A={best['mean_sl_A']:.1%}  B={best['mean_sl_B']:.1%}  "
          f"C={best['mean_sl_C']:.1%}")

    # ── persist policy file (26-vec) ──
    os.makedirs(MODELS_DIR, exist_ok=True)
    theta26 = build_grid_theta26(
        store_s=best['store_s'],
        store_S=best['store_S'],
        warehouse_s=best['warehouse_s'],
        warehouse_S=best['warehouse_S'],
    )
    theta_path = os.path.join(MODELS_DIR, 'grid_tuned_classical_theta.npy')
    np.save(theta_path, theta26)
    print(f"\n  saved theta26 ({theta26.shape[0]}-vec) -> {theta_path}")

    # ── persist full grid + winner JSON ──
    elapsed = time.time() - t0
    payload = {
        'metadata': {
            'train_fraction':       TRAIN_FRACTION,
            'train_days':           int(len(train_series)),
            'episodes_per_cell':    EPISODES_PER_CELL,
            'episode_length_days':  EPISODE_LENGTH,
            'base_seed':            BASE_SEED,
            'sl_floor_per_store':   SL_FLOOR_PER_STORE,
            'data_source':          'data/processed/m5_clean.csv',
            'timestamp':            datetime.now(timezone.utc).isoformat(),
            'wall_clock_seconds':   elapsed,
        },
        'grid_axes': {
            'warehouse_s':   list(WAREHOUSE_S_CANDIDATES),
            'warehouse_gap': list(WAREHOUSE_GAP_CANDIDATES),
            'store_s':       list(STORE_S_CANDIDATES),
            'store_gap':     list(STORE_GAP_CANDIDATES),
        },
        'winner': {
            'warehouse_s':       best['warehouse_s'],
            'warehouse_S':       best['warehouse_S'],
            'warehouse_gap':     best['warehouse_gap'],
            'store_s':           best['store_s'],
            'store_S':           best['store_S'],
            'store_gap':         best['store_gap'],
            'store_theta_8vec':  best['store_theta'],
            'mean_profit':       best['mean_profit'],
            'std_profit':        best['std_profit'],
            'mean_sl_A':         best['mean_sl_A'],
            'mean_sl_B':         best['mean_sl_B'],
            'mean_sl_C':         best['mean_sl_C'],
            'min_store_sl':      best['min_store_sl'],
            'theta_path':        theta_path,
        },
        'cells': all_cells,
    }
    json_path = os.path.join(MODELS_DIR, 'grid_tuned_classical.json')
    with open(json_path, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f"  saved full grid           -> {json_path}")
    print(f"  wall-clock: {elapsed:.1f}s")
    return 0


if __name__ == '__main__':
    sys.exit(main())
