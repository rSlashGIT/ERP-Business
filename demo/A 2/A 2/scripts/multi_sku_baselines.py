#!/usr/bin/env python3
"""
Phase 7 — Multi-SKU baselines for the joint CMA-ES policy.

Computes two classical baselines plus a computational-complexity table.
Both baselines use the *same* raw-CMA-ES theta layout the joint policy
uses (8 store params + 2 warehouse raw controls per SKU), so the
comparison is in a single, shared parameter space.

Baselines:

    1. NAIVE-UNIFORM
       Single shared 10-vec replicated across all 30 SKUs. Grid search
       in raw theta space (5×4×5×4 = 400 cells), evaluated on the full
       30-SKU network. Picks the feasible cell with highest mean
       network profit.

    2. PER-SKU GRID
       Per-SKU independent grid search (same 400-cell grid). Each SKU
       is evaluated *alone* (a single-SKU instance of MultiSKUNetwork),
       so this strong classical baseline is what you get when you
       optimise SKUs in isolation. The 30 per-SKU best 10-vecs are
       concatenated into a 300-vec and re-evaluated on the full
       multi-SKU network so the shared-capacity penalty applies.

Outputs:
    models/multi_sku_baselines.json
        {
          'naive_uniform': {'best_theta_block': ..., 'mean_profit': ...,
                            'sl_grid': ..., 'eval_full_network': ...},
          'per_sku_grid':  {'per_sku_theta': {sku_id: 10-vec}, ...,
                            'eval_full_network': ...},
          'complexity_table': [...]
        }
    models/multi_sku_baseline_uniform_theta.npy
    models/multi_sku_baseline_persku_theta.npy
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from itertools import product
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from multi_sku_network import (                                       # noqa: E402
    MAX_WAREHOUSE_CAPACITY_DEFAULT,
    OVERFLOW_PENALTY,
    PARAMS_PER_SKU,
    SL_FLOOR,
    STORE_IDS,
    SKUConfig,
    MultiSKUNetwork,
    build_network,
    load_multi_sku_data,
)


# ───────────────── grid axes ─────────────────

# Grid in raw CMA-ES parameter space. Same axes for uniform and per-SKU.
S_BASE_GRID    = [0.5, 1.0, 1.5, 2.0, 2.5]   # 5  (normalized inv threshold)
S_BIG_GRID     = [2.0, 3.5, 5.0, 6.5]        # 4  (action-index target)
WH_S_RAW_GRID  = [-7.0, -3.0, 0.0, 3.0, 7.0] # 5  (raw control -> 50..750)
WH_S_BIG_GRID  = [-5.0, 0.0, 5.0, 10.0]      # 4  (raw control -> 500..2000)

GRID_SIZE = (len(S_BASE_GRID) * len(S_BIG_GRID)
             * len(WH_S_RAW_GRID) * len(WH_S_BIG_GRID))     # 400 cells

DEFAULT_EPISODES_UNIFORM = 4
DEFAULT_EPISODES_PER_SKU = 3
DEFAULT_EP_LENGTH        = 90
DEFAULT_BASE_SEED        = 42

MODELS_DIR = 'models'


def _block(s_base: float, s_big: float,
           wh_s_raw: float, wh_S_raw: float) -> np.ndarray:
    """Build a 10-vec for one SKU. Context-weight slots zeroed (flat
    classical (s,S) policy)."""
    return np.array(
        [s_base, s_big, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
         wh_s_raw, wh_S_raw],
        dtype=np.float64,
    )


# ───────────────── shared evaluator ─────────────────

def _evaluate_full_network(
    theta: np.ndarray,
    multi_data: Dict,
    demand_slice: Dict[str, np.ndarray],
    n_episodes: int,
    episode_length: int,
    seed: int,
    max_warehouse_capacity: float,
) -> Dict:
    """Evaluate a 300-vec theta on the full multi-SKU network."""
    sub_data = dict(multi_data)
    sub_data['demand_by_sku'] = demand_slice
    rng = np.random.default_rng(seed)
    min_len = min(len(d) for d in demand_slice.values())
    max_start = max(0, min_len - episode_length - 1)

    profits, sls, overflows = [], [], []
    sl_grid_acc: Dict[str, Dict[str, List[float]]] = {
        sid: {st: [] for st in STORE_IDS} for sid in multi_data['sku_ids']
    }
    sku_profits_acc: Dict[str, List[float]] = {
        sid: [] for sid in multi_data['sku_ids']
    }

    for _ in range(n_episodes):
        start_day = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        ep_seed = int(rng.integers(0, 2**31 - 1))
        net = build_network(
            sub_data, theta, start_day=start_day, seed=ep_seed,
            max_warehouse_capacity=max_warehouse_capacity,
            overflow_penalty=OVERFLOW_PENALTY,
        )
        s = net.simulate(n_steps=episode_length)
        profits.append(s['network_total_profit'])
        sls.append(s['service_level'])
        overflows.append(s['overflow_cost_total'])
        for sid in multi_data['sku_ids']:
            sub = s['per_sku'][sid]
            sku_profits_acc[sid].append(sub['sku_total_profit'])
            for st in STORE_IDS:
                sl_grid_acc[sid][st].append(sub['store_service_level'][st])

    sl_grid = {sid: {st: float(np.mean(sl_grid_acc[sid][st]))
                     for st in STORE_IDS}
               for sid in multi_data['sku_ids']}
    n_violations = sum(1 for sid in multi_data['sku_ids']
                       for st in STORE_IDS
                       if sl_grid[sid][st] < SL_FLOOR)
    return {
        'mean_profit':   float(np.mean(profits)),
        'std_profit':    float(np.std(profits)),
        'mean_sl':       float(np.mean(sls)),
        'mean_overflow': float(np.mean(overflows)),
        'sl_grid':       sl_grid,
        'n_violations':  n_violations,
        'sku_mean_profit': {sid: float(np.mean(v))
                            for sid, v in sku_profits_acc.items()},
    }


# ───────────────── single-SKU evaluator (used by per-SKU grid) ─────────────────

def _evaluate_single_sku(
    block: np.ndarray,
    sku_id: str,
    multi_data: Dict,
    demand_slice: Dict[str, np.ndarray],
    n_episodes: int,
    episode_length: int,
    seed: int,
) -> Dict:
    """Evaluate a 10-vec block for ONE SKU in isolation. Builds a
    1-SKU MultiSKUNetwork (no shared capacity penalty since there's
    nothing else to share with). Returns mean profit and per-store SL."""
    one_data = dict(multi_data)
    one_data['sku_ids'] = [sku_id]
    one_data['demand_by_sku']    = {sku_id: demand_slice[sku_id]}
    one_data['mean_by_sku']      = {sku_id: multi_data['mean_by_sku'][sku_id]}
    one_data['price_by_sku']     = {sku_id: multi_data['price_by_sku'][sku_id]}
    one_data['unit_cost_by_sku'] = {sku_id: multi_data['unit_cost_by_sku'][sku_id]}

    rng = np.random.default_rng(seed)
    L = len(demand_slice[sku_id])
    max_start = max(0, L - episode_length - 1)

    profits = []
    store_sl_acc = {st: [] for st in STORE_IDS}
    for _ in range(n_episodes):
        start_day = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        ep_seed = int(rng.integers(0, 2**31 - 1))
        # Single-SKU evaluation is uncoupled from joint capacity, so
        # set capacity high enough to never trip the overflow penalty.
        net = build_network(
            one_data, block, start_day=start_day, seed=ep_seed,
            max_warehouse_capacity=1e15,
            overflow_penalty=0.0,
        )
        s = net.simulate(n_steps=episode_length)
        profits.append(s['network_total_profit'])
        for st in STORE_IDS:
            store_sl_acc[st].append(
                s['per_sku'][sku_id]['store_service_level'][st]
            )
    return {
        'mean_profit':   float(np.mean(profits)),
        'store_sl':      {st: float(np.mean(store_sl_acc[st]))
                          for st in STORE_IDS},
        'min_store_sl':  float(min(np.mean(store_sl_acc[st])
                                   for st in STORE_IDS)),
    }


# ───────────────── baseline 1: naive uniform ─────────────────

def run_naive_uniform(
    multi_data: Dict,
    train_demand: Dict[str, np.ndarray],
    n_episodes: int = DEFAULT_EPISODES_UNIFORM,
    episode_length: int = DEFAULT_EP_LENGTH,
    base_seed: int = DEFAULT_BASE_SEED,
    max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
    verbose: bool = True,
) -> Dict:
    n_skus = len(multi_data['sku_ids'])
    cells: List[Dict] = []
    best: Optional[Dict] = None

    if verbose:
        print(f"\n  NAIVE UNIFORM: {GRID_SIZE} cells × {n_episodes} episodes "
              f"= {GRID_SIZE * n_episodes:,} multi-SKU episodes")

    t0 = time.time()
    last_print = t0
    cell_idx = 0
    feasible_count = 0
    for s_base, s_big, wh_s, wh_S in product(S_BASE_GRID, S_BIG_GRID,
                                              WH_S_RAW_GRID, WH_S_BIG_GRID):
        cell_idx += 1
        block = _block(s_base, s_big, wh_s, wh_S)
        theta = np.tile(block, n_skus)
        result = _evaluate_full_network(
            theta, multi_data, train_demand,
            n_episodes=n_episodes, episode_length=episode_length,
            seed=base_seed, max_warehouse_capacity=max_warehouse_capacity,
        )
        feasible = (result['n_violations'] == 0)
        cells.append({
            's_base': s_base, 's_big': s_big,
            'wh_s_raw': wh_s, 'wh_S_raw': wh_S,
            'feasible': feasible,
            **{k: v for k, v in result.items()
               if k not in ('sl_grid', 'sku_mean_profit')},
        })
        if feasible:
            feasible_count += 1
            if best is None or result['mean_profit'] > best['mean_profit']:
                best = {
                    'block':            block.tolist(),
                    'mean_profit':      result['mean_profit'],
                    'std_profit':       result['std_profit'],
                    'mean_sl':          result['mean_sl'],
                    'mean_overflow':    result['mean_overflow'],
                    's_base': s_base, 's_big': s_big,
                    'wh_s_raw': wh_s, 'wh_S_raw': wh_S,
                    'sl_grid':          result['sl_grid'],
                    'sku_mean_profit':  result['sku_mean_profit'],
                }
        if verbose and time.time() - last_print > 5.0:
            best_p = best['mean_profit'] if best else float('nan')
            print(f"    [{cell_idx:>4}/{GRID_SIZE}]  "
                  f"feasible={feasible_count:>4}  "
                  f"best_profit=${best_p:>+12,.0f}")
            last_print = time.time()
    elapsed = time.time() - t0

    if verbose:
        if best is None:
            print(f"  ⚠️  no feasible cell at {SL_FLOOR:.0%} per-(sku,store)")
        else:
            print(f"  best uniform: s_base={best['s_base']}  "
                  f"S_base={best['s_big']}  "
                  f"wh_raw_s={best['wh_s_raw']}  wh_raw_S={best['wh_S_raw']}  "
                  f"profit=${best['mean_profit']:+,.0f}")
        print(f"  uniform grid wall time: {elapsed:.1f}s")

    return {
        'best':                  best,
        'all_cells':              cells,
        'feasible_count':         feasible_count,
        'grid_size':              GRID_SIZE,
        'episodes_per_cell':      n_episodes,
        'episode_length':         episode_length,
        'elapsed_seconds':        elapsed,
        'sl_floor':               SL_FLOOR,
        'max_warehouse_capacity': max_warehouse_capacity,
    }


# ───────────────── baseline 2: per-SKU grid ─────────────────

def run_per_sku_grid(
    multi_data: Dict,
    train_demand: Dict[str, np.ndarray],
    n_episodes: int = DEFAULT_EPISODES_PER_SKU,
    episode_length: int = DEFAULT_EP_LENGTH,
    base_seed: int = DEFAULT_BASE_SEED,
    verbose: bool = True,
) -> Dict:
    """For each SKU, run the same 400-cell grid in isolation. The
    feasibility check is per-(this-SKU, store), and the winner per SKU
    is the highest-profit feasible cell."""
    sku_ids = multi_data['sku_ids']
    per_sku_best: Dict[str, Dict] = {}
    per_sku_all: Dict[str, List[Dict]] = {}

    t0 = time.time()
    if verbose:
        print(f"\n  PER-SKU GRID: {GRID_SIZE} cells × {n_episodes} episodes "
              f"× {len(sku_ids)} SKUs = "
              f"{GRID_SIZE * n_episodes * len(sku_ids):,} single-SKU episodes")

    for i, sid in enumerate(sku_ids, 1):
        cells: List[Dict] = []
        best: Optional[Dict] = None
        feasible_count = 0
        for s_base, s_big, wh_s, wh_S in product(S_BASE_GRID, S_BIG_GRID,
                                                  WH_S_RAW_GRID, WH_S_BIG_GRID):
            block = _block(s_base, s_big, wh_s, wh_S)
            r = _evaluate_single_sku(
                block, sid, multi_data, train_demand,
                n_episodes=n_episodes, episode_length=episode_length,
                seed=base_seed + i,
            )
            feasible = (r['min_store_sl'] >= SL_FLOOR)
            cell = {
                's_base': s_base, 's_big': s_big,
                'wh_s_raw': wh_s, 'wh_S_raw': wh_S,
                'mean_profit':   r['mean_profit'],
                'min_store_sl':  r['min_store_sl'],
                'store_sl':      r['store_sl'],
                'feasible':      feasible,
            }
            cells.append(cell)
            if feasible:
                feasible_count += 1
                if best is None or r['mean_profit'] > best['mean_profit']:
                    best = {**cell, 'block': block.tolist()}
        if best is None:
            # If nothing is feasible per the per-store floor, pick the
            # cell with the highest min_store_sl (closest to feasible).
            cells.sort(key=lambda c: -c['min_store_sl'])
            fallback = cells[0]
            best = {**fallback,
                    'block':           _block(fallback['s_base'],
                                              fallback['s_big'],
                                              fallback['wh_s_raw'],
                                              fallback['wh_S_raw']).tolist(),
                    'fallback_reason': 'no feasible cell at SL_FLOOR'}
        per_sku_best[sid] = best
        per_sku_all[sid] = cells
        if verbose:
            print(f"    [{i:>2}/{len(sku_ids)}] {sid:<20} "
                  f"profit=${best['mean_profit']:+,.0f}  "
                  f"feasible={feasible_count:>3}/{GRID_SIZE}  "
                  f"min_SL={best['min_store_sl']:.1%}"
                  f"{'  (fallback)' if best.get('fallback_reason') else ''}")

    elapsed = time.time() - t0
    if verbose:
        print(f"  per-SKU grid wall time: {elapsed:.1f}s")
    return {
        'per_sku_best':      per_sku_best,
        'per_sku_all':       per_sku_all,
        'grid_size':         GRID_SIZE,
        'episodes_per_cell': n_episodes,
        'episode_length':    episode_length,
        'elapsed_seconds':   elapsed,
        'sl_floor':          SL_FLOOR,
    }


# ───────────────── computational complexity table ─────────────────

def complexity_table(n_skus: int, grid_per_sku: int = GRID_SIZE) -> List[Dict]:
    """Return entries for the complexity comparison table:

        Method                        Total evaluations  Wall (this run)  Feasible?
        Uniform grid                  GRID_SIZE          ~min             yes
        Per-SKU grid                  GRID_SIZE * n_skus ~min             yes
        Joint exhaustive grid         GRID_SIZE^n_skus   age-of-universe  no
        CMA-ES (joint)                CMA evals          ~hr              yes
    """
    return [
        {
            'method':     'Naive uniform grid',
            'parameter_space': f'{grid_per_sku} cells (single 10-vec replicated)',
            'evaluations': grid_per_sku,
            'note':       'Same (s,S) for all SKUs — ignores SKU heterogeneity.',
            'feasible':   True,
        },
        {
            'method':     'Per-SKU independent grid',
            'parameter_space': f'{grid_per_sku} cells × {n_skus} SKUs in isolation',
            'evaluations': grid_per_sku * n_skus,
            'note':       'Strong classical baseline. Ignores shared warehouse capacity.',
            'feasible':   True,
        },
        {
            'method':     'Joint exhaustive grid',
            'parameter_space': f'{grid_per_sku}^{n_skus} cells',
            'evaluations': f'{grid_per_sku}**{n_skus}',
            'evaluations_log10': float(n_skus) * math.log10(grid_per_sku),
            'note':       (f'10**{n_skus * math.log10(grid_per_sku):.1f} '
                           'cells — vastly exceeds atoms in observable universe.'),
            'feasible':   False,
        },
        {
            'method':     'Joint CMA-ES (this work)',
            'parameter_space': f'{n_skus * PARAMS_PER_SKU} continuous dims',
            'evaluations': 'O(popsize × generations) ≪ exhaustive',
            'note':       (f'Active CMA-ES with diagonal-only updates for '
                           f'first 100 gens. Hansen 2008, Loshchilov 2014.'),
            'feasible':   True,
        },
    ]


# ───────────────── orchestration ─────────────────

def run_baselines(
    seed: int = DEFAULT_BASE_SEED,
    n_episodes_uniform: int = DEFAULT_EPISODES_UNIFORM,
    n_episodes_per_sku: int = DEFAULT_EPISODES_PER_SKU,
    episode_length: int = DEFAULT_EP_LENGTH,
    max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
    save: bool = True,
    verbose: bool = True,
) -> Dict:
    multi_data = load_multi_sku_data()
    n_skus = len(multi_data['sku_ids'])

    # 60% train slice (same as CMA-ES) to keep grid + CMA-ES fair.
    train_demand = {sid: d[: int(len(d) * 0.6)]
                    for sid, d in multi_data['demand_by_sku'].items()}
    val_demand   = {sid: d[int(len(d) * 0.6): int(len(d) * 0.8)]
                    for sid, d in multi_data['demand_by_sku'].items()}
    test_demand  = {sid: d[int(len(d) * 0.8):]
                    for sid, d in multi_data['demand_by_sku'].items()}

    if verbose:
        print(f"{'='*92}")
        print(f"  Multi-SKU baselines  |  n_skus={n_skus}  "
              f"|  warehouse_cap={max_warehouse_capacity:,.0f}  |  seed={seed}")
        print(f"  Grid: 5 × 4 × 5 × 4 = {GRID_SIZE} cells")
        print(f"  Train days/SKU: {len(next(iter(train_demand.values())))}")
        print(f"{'='*92}")

    uniform = run_naive_uniform(
        multi_data, train_demand,
        n_episodes=n_episodes_uniform, episode_length=episode_length,
        base_seed=seed, max_warehouse_capacity=max_warehouse_capacity,
        verbose=verbose,
    )

    per_sku = run_per_sku_grid(
        multi_data, train_demand,
        n_episodes=n_episodes_per_sku, episode_length=episode_length,
        base_seed=seed, verbose=verbose,
    )

    # Build full 300-vecs from each baseline winner.
    if uniform['best'] is not None:
        uniform_block = np.asarray(uniform['best']['block'], dtype=np.float64)
        uniform_theta = np.tile(uniform_block, n_skus)
    else:
        # Fallback: pick the highest-mean-SL infeasible cell (extreme of grid)
        cells = sorted(uniform['all_cells'], key=lambda c: -c['mean_sl'])
        f = cells[0]
        uniform_block = _block(f['s_base'], f['s_big'],
                               f['wh_s_raw'], f['wh_S_raw'])
        uniform_theta = np.tile(uniform_block, n_skus)
        uniform['best'] = {
            'block':         uniform_block.tolist(),
            'mean_profit':   f['mean_profit'],
            'mean_sl':       f['mean_sl'],
            'sl_grid':       None,
            's_base': f['s_base'], 's_big': f['s_big'],
            'wh_s_raw': f['wh_s_raw'], 'wh_S_raw': f['wh_S_raw'],
            'sku_mean_profit': None,
            'fallback_reason': 'no feasible cell — selected highest-SL infeasible cell',
        }

    # Per-SKU concatenation (in SKU order from multi_data['sku_ids']).
    per_sku_blocks = []
    for sid in multi_data['sku_ids']:
        b = per_sku['per_sku_best'][sid]['block']
        per_sku_blocks.append(np.asarray(b, dtype=np.float64))
    per_sku_theta = np.concatenate(per_sku_blocks)

    # Re-evaluate both 300-vecs on the full multi-SKU network across
    # train/val/test (so we get apples-to-apples numbers vs the joint
    # CMA-ES policy). Each evaluation uses the joint capacity penalty.
    print(f"\n  Re-evaluating both baseline 300-vecs on full network...")
    full_uniform_train = _evaluate_full_network(
        uniform_theta, multi_data, train_demand,
        n_episodes=n_episodes_uniform * 2, episode_length=episode_length,
        seed=seed + 1, max_warehouse_capacity=max_warehouse_capacity,
    )
    full_uniform_val = _evaluate_full_network(
        uniform_theta, multi_data, val_demand,
        n_episodes=n_episodes_uniform * 2, episode_length=episode_length,
        seed=seed + 2, max_warehouse_capacity=max_warehouse_capacity,
    )
    full_uniform_test = _evaluate_full_network(
        uniform_theta, multi_data, test_demand,
        n_episodes=n_episodes_uniform * 2, episode_length=episode_length,
        seed=seed + 3, max_warehouse_capacity=max_warehouse_capacity,
    )

    full_persku_train = _evaluate_full_network(
        per_sku_theta, multi_data, train_demand,
        n_episodes=n_episodes_uniform * 2, episode_length=episode_length,
        seed=seed + 11, max_warehouse_capacity=max_warehouse_capacity,
    )
    full_persku_val = _evaluate_full_network(
        per_sku_theta, multi_data, val_demand,
        n_episodes=n_episodes_uniform * 2, episode_length=episode_length,
        seed=seed + 12, max_warehouse_capacity=max_warehouse_capacity,
    )
    full_persku_test = _evaluate_full_network(
        per_sku_theta, multi_data, test_demand,
        n_episodes=n_episodes_uniform * 2, episode_length=episode_length,
        seed=seed + 13, max_warehouse_capacity=max_warehouse_capacity,
    )

    if verbose:
        print(f"\n  ── Full-network re-evaluation (train/val/test) ──")
        print(f"  Uniform   train=${full_uniform_train['mean_profit']:+,.0f}  "
              f"val=${full_uniform_val['mean_profit']:+,.0f}  "
              f"test=${full_uniform_test['mean_profit']:+,.0f}  "
              f"test_SL={full_uniform_test['mean_sl']:.1%}  "
              f"viols={full_uniform_test['n_violations']}/{n_skus*3}")
        print(f"  Per-SKU   train=${full_persku_train['mean_profit']:+,.0f}  "
              f"val=${full_persku_val['mean_profit']:+,.0f}  "
              f"test=${full_persku_test['mean_profit']:+,.0f}  "
              f"test_SL={full_persku_test['mean_sl']:.1%}  "
              f"viols={full_persku_test['n_violations']}/{n_skus*3}")

    result = {
        'phase':      'phase7_multi_sku_baselines',
        'n_skus':     n_skus,
        'grid_size':  GRID_SIZE,
        'naive_uniform': {
            'best':              uniform['best'],
            'feasible_count':    uniform['feasible_count'],
            'grid_size':         uniform['grid_size'],
            'episodes_per_cell': uniform['episodes_per_cell'],
            'episode_length':    uniform['episode_length'],
            'elapsed_seconds':   uniform['elapsed_seconds'],
            'eval_full_train':   full_uniform_train,
            'eval_full_val':     full_uniform_val,
            'eval_full_test':    full_uniform_test,
        },
        'per_sku_grid': {
            'per_sku_best':       per_sku['per_sku_best'],
            'grid_size':          per_sku['grid_size'],
            'episodes_per_cell':  per_sku['episodes_per_cell'],
            'episode_length':     per_sku['episode_length'],
            'elapsed_seconds':    per_sku['elapsed_seconds'],
            'eval_full_train':    full_persku_train,
            'eval_full_val':      full_persku_val,
            'eval_full_test':     full_persku_test,
        },
        'complexity_table': complexity_table(n_skus, GRID_SIZE),
        'sl_floor':         SL_FLOOR,
        'max_warehouse_capacity': max_warehouse_capacity,
    }

    if save:
        os.makedirs(MODELS_DIR, exist_ok=True)
        np.save(os.path.join(MODELS_DIR, 'multi_sku_baseline_uniform_theta.npy'),
                uniform_theta)
        np.save(os.path.join(MODELS_DIR, 'multi_sku_baseline_persku_theta.npy'),
                per_sku_theta)
        with open(os.path.join(MODELS_DIR, 'multi_sku_baselines.json'), 'w') as f:
            json.dump(result, f, indent=2)
        if verbose:
            print(f"\n  Saved -> models/multi_sku_baselines.json")
            print(f"  Saved -> models/multi_sku_baseline_uniform_theta.npy")
            print(f"  Saved -> models/multi_sku_baseline_persku_theta.npy")

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument('--episodes-uniform', type=int,
                        default=DEFAULT_EPISODES_UNIFORM)
    parser.add_argument('--episodes-per-sku', type=int,
                        default=DEFAULT_EPISODES_PER_SKU)
    parser.add_argument('--episode-length', type=int, default=DEFAULT_EP_LENGTH)
    parser.add_argument('--max-warehouse-capacity', type=float,
                        default=MAX_WAREHOUSE_CAPACITY_DEFAULT)
    args = parser.parse_args()

    run_baselines(
        seed=args.seed,
        n_episodes_uniform=args.episodes_uniform,
        n_episodes_per_sku=args.episodes_per_sku,
        episode_length=args.episode_length,
        max_warehouse_capacity=args.max_warehouse_capacity,
    )
