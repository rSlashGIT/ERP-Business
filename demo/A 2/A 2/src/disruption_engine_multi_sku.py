#!/usr/bin/env python3
"""
Phase 7 — Multi-SKU disruption resilience engine.

Stress-tests three multi-SKU policies under the same six disruption
scenarios as Phase 4:

    1. calm              no disruption (control)
    2. mild_supplier     supplier->warehouse lead x1.5 for 3 days @ d20
    3. major_supplier    supplier->warehouse lead x3   for 14 days @ d10
    4. demand_spike      base demand x3                for 3 days @ d30
    5. port_strike       supplier->warehouse lead ~ U{2..10} for 10 days @ d15
    6. compound_crisis   major_supplier + demand_spike overlapping

Three policies (all 300-vec MultiSKUNetwork-compatible):
    naive_uniform     models/multi_sku_baseline_uniform_theta.npy
    per_sku_grid      models/multi_sku_baseline_persku_theta.npy
    cmaes_joint       models/multi_sku_theta.npy

Per (scenario, policy) cell we run 30 paired episodes (the same
(start_day, seed) tuples reused across cells for paired comparison)
on the held-out 20% test slice and report:

    - mean_profit, std_profit
    - p5_worst (5th percentile, tail risk)
    - mean_service_level (network-level fill rate)
    - profit_drop_vs_calm_pct (vs same policy's calm baseline)
    - per-SKU SL distribution (so we can spot which SKUs fail)

Output:
    data/disruption_results_multi_sku.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from disruption_engine import (                                       # noqa: E402
    _make_window_lead_fn,
    _make_random_lead_fn,
    _make_demand_spike_fn,
    _make_compound_fn,
    LEAD_SUPPLIER_TO_WAREHOUSE,
)
from multi_sku_network import (                                        # noqa: E402
    MAX_WAREHOUSE_CAPACITY_DEFAULT,
    OVERFLOW_PENALTY,
    SL_FLOOR,
    STORE_IDS,
    build_network,
    load_multi_sku_data,
)


MODELS_DIR = 'models'
DATA_DIR   = 'data'
EPISODES_PER_CELL = 30
EPISODE_LENGTH    = 90
BASE_SEED         = 42

POLICY_PATHS = {
    'naive_uniform': os.path.join(MODELS_DIR, 'multi_sku_baseline_uniform_theta.npy'),
    'per_sku_grid':  os.path.join(MODELS_DIR, 'multi_sku_baseline_persku_theta.npy'),
    'cmaes_joint':   os.path.join(MODELS_DIR, 'multi_sku_theta.npy'),
}

POLICY_DESCRIPTIONS = {
    'naive_uniform': 'Naive uniform classical (s,S) — single 10-vec replicated across all SKUs',
    'per_sku_grid':  'Per-SKU grid-tuned classical (s,S) — independent grid per SKU, no joint awareness',
    'cmaes_joint':   'Joint CMA-ES (Phase 7) — 300-dim diagonal-active CMA-ES on full network',
}


def build_multi_sku_scenarios(scenario_seed: int) -> Dict[str, Dict]:
    base_lead = int(LEAD_SUPPLIER_TO_WAREHOUSE)
    mild_lead  = int(np.ceil(base_lead * 1.5))
    major_lead = int(base_lead * 3)
    return {
        'calm': {
            'factory': lambda: None,
            'disrupt_start': None, 'disrupt_end': None,
            'description': 'no disruption (control)',
        },
        'mild_supplier': {
            'factory': lambda: _make_window_lead_fn(
                start_day=20, duration=3, supplier_lead=mild_lead),
            'disrupt_start': 20, 'disrupt_end': 23,
            'description': f'supplier lead x1.5 ({base_lead}->{mild_lead}) days 20-22',
        },
        'major_supplier': {
            'factory': lambda: _make_window_lead_fn(
                start_day=10, duration=14, supplier_lead=major_lead),
            'disrupt_start': 10, 'disrupt_end': 24,
            'description': f'supplier lead x3 ({base_lead}->{major_lead}) days 10-23',
        },
        'demand_spike': {
            'factory': lambda: _make_demand_spike_fn(
                start_day=30, duration=3, multiplier=3.0),
            'disrupt_start': 30, 'disrupt_end': 33,
            'description': 'base demand x3 for 3 days, days 30-32',
        },
        'port_strike': {
            'factory': lambda seed=scenario_seed: _make_random_lead_fn(
                start_day=15, duration=10, lead_low=2, lead_high=10, seed=seed),
            'disrupt_start': 15, 'disrupt_end': 25,
            'description': 'supplier lead ~ U{2..10} indep. per day, days 15-24',
        },
        'compound_crisis': {
            'factory': lambda: _make_compound_fn(
                supplier_start=10, supplier_duration=14, supplier_lead=major_lead,
                demand_start=30, demand_duration=3, demand_multiplier=3.0),
            'disrupt_start': 10, 'disrupt_end': 33,
            'description': 'major_supplier (10-23) + demand_spike (30-32) overlap',
        },
    }


def _run_episode(
    theta: np.ndarray,
    multi_data: Dict,
    test_demand: Dict[str, np.ndarray],
    start_day: int,
    seed: int,
    disruption_fn: Optional[Callable[[int], Dict]],
    max_warehouse_capacity: float,
    episode_length: int = EPISODE_LENGTH,
) -> Dict:
    sub_data = dict(multi_data)
    sub_data['demand_by_sku'] = test_demand
    net = build_network(
        sub_data, theta, start_day=start_day, seed=seed,
        max_warehouse_capacity=max_warehouse_capacity,
        overflow_penalty=OVERFLOW_PENALTY,
        disruption_fn=disruption_fn,
    )
    summary = net.simulate(n_steps=episode_length)
    return summary


def _evaluate_cell(
    theta: np.ndarray,
    multi_data: Dict,
    test_demand: Dict[str, np.ndarray],
    factory: Callable[[], Optional[Callable[[int], Dict]]],
    n_episodes: int,
    paired_plan: List[Dict],
    max_warehouse_capacity: float,
) -> Dict:
    """Evaluate a (theta, scenario_factory) cell with paired episodes.

    paired_plan: list of {'start_day': int, 'seed': int} drawn ONCE
    per BASE_SEED so all cells share the same plan."""
    profits, sls, overflows = [], [], []
    sku_sl_acc: Dict[str, List[float]] = {sid: [] for sid in multi_data['sku_ids']}
    sku_profit_acc: Dict[str, List[float]] = {sid: [] for sid in multi_data['sku_ids']}

    for plan in paired_plan[:n_episodes]:
        disruption_fn = factory()
        summary = _run_episode(
            theta=theta, multi_data=multi_data, test_demand=test_demand,
            start_day=plan['start_day'], seed=plan['seed'],
            disruption_fn=disruption_fn,
            max_warehouse_capacity=max_warehouse_capacity,
        )
        profits.append(summary['network_total_profit'])
        sls.append(summary['service_level'])
        overflows.append(summary['overflow_cost_total'])
        for sid in multi_data['sku_ids']:
            sub = summary['per_sku'][sid]
            sku_sl_acc[sid].append(sub['sku_service_level'])
            sku_profit_acc[sid].append(sub['sku_total_profit'])

    profits_arr = np.asarray(profits, dtype=np.float64)
    return {
        'mean_profit':   float(profits_arr.mean()),
        'std_profit':    float(profits_arr.std()),
        'p5_worst':      float(np.percentile(profits_arr, 5)),
        'p50':           float(np.percentile(profits_arr, 50)),
        'p95':           float(np.percentile(profits_arr, 95)),
        'mean_sl':       float(np.mean(sls)),
        'mean_overflow': float(np.mean(overflows)),
        'sku_mean_sl':   {sid: float(np.mean(sku_sl_acc[sid]))
                          for sid in multi_data['sku_ids']},
        'sku_mean_profit': {sid: float(np.mean(sku_profit_acc[sid]))
                            for sid in multi_data['sku_ids']},
        'episode_profits': profits_arr.tolist(),
    }


def make_paired_plan(test_demand: Dict[str, np.ndarray],
                     n_episodes: int,
                     base_seed: int = BASE_SEED,
                     episode_length: int = EPISODE_LENGTH) -> List[Dict]:
    """Pre-draw a list of (start_day, seed) tuples shared across every
    (scenario, policy) cell so they are paired."""
    rng = np.random.default_rng(base_seed)
    min_len = min(len(d) for d in test_demand.values())
    max_start = max(0, min_len - episode_length - 1)
    plan: List[Dict] = []
    for _ in range(n_episodes):
        plan.append({
            'start_day': int(rng.integers(0, max_start + 1)) if max_start > 0 else 0,
            'seed':      int(rng.integers(0, 2**31 - 1)),
        })
    return plan


def _load_policy_thetas() -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    for key, path in POLICY_PATHS.items():
        if not os.path.exists(path):
            raise FileNotFoundError(f"Policy {key!r} missing at {path}")
        out[key] = np.load(path).astype(np.float64)
    return out


def run_disruption_sweep(
    n_episodes: int = EPISODES_PER_CELL,
    max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
    save: bool = True,
    verbose: bool = True,
) -> Dict:
    multi_data = load_multi_sku_data()
    n_skus = len(multi_data['sku_ids'])
    test_demand = {sid: d[int(len(d) * 0.8):]
                   for sid, d in multi_data['demand_by_sku'].items()}

    plan = make_paired_plan(test_demand, n_episodes,
                            base_seed=BASE_SEED, episode_length=EPISODE_LENGTH)
    scenarios = build_multi_sku_scenarios(scenario_seed=BASE_SEED + 7777)
    thetas = _load_policy_thetas()

    if verbose:
        print(f"\n{'='*92}")
        print(f"  Multi-SKU Disruption Engine  |  n_skus={n_skus}  "
              f"|  episodes/cell={n_episodes}  |  episode_len={EPISODE_LENGTH}")
        print(f"  policies: {list(thetas.keys())}")
        print(f"  scenarios: {list(scenarios.keys())}")
        print(f"  warehouse_capacity={max_warehouse_capacity:,.0f}")
        print(f"{'='*92}")

    t0 = time.time()
    results: Dict[str, Dict[str, Dict]] = {pol: {} for pol in thetas}
    for pol_key, theta in thetas.items():
        for scen_key, scen in scenarios.items():
            t_cell = time.time()
            cell = _evaluate_cell(
                theta=theta, multi_data=multi_data, test_demand=test_demand,
                factory=scen['factory'], n_episodes=n_episodes,
                paired_plan=plan,
                max_warehouse_capacity=max_warehouse_capacity,
            )
            cell['elapsed_seconds']  = time.time() - t_cell
            cell['scenario_description'] = scen['description']
            cell['disrupt_start']    = scen['disrupt_start']
            cell['disrupt_end']      = scen['disrupt_end']
            results[pol_key][scen_key] = cell
            if verbose:
                print(f"  [{pol_key:<14}|{scen_key:<16}] "
                      f"profit=${cell['mean_profit']:>+12,.0f}  "
                      f"p5=${cell['p5_worst']:>+12,.0f}  "
                      f"SL={cell['mean_sl']:.1%}  "
                      f"({cell['elapsed_seconds']:.1f}s)")

    # profit_drop_vs_calm_pct
    for pol_key in thetas:
        calm_profit = results[pol_key]['calm']['mean_profit']
        for scen_key in scenarios:
            cell = results[pol_key][scen_key]
            if scen_key == 'calm':
                cell['profit_drop_vs_calm_pct'] = 0.0
            else:
                if calm_profit == 0.0:
                    cell['profit_drop_vs_calm_pct'] = float('nan')
                else:
                    drop = (calm_profit - cell['mean_profit']) / abs(calm_profit)
                    cell['profit_drop_vs_calm_pct'] = float(drop * 100.0)

    elapsed_total = time.time() - t0

    payload = {
        'phase':            'phase7_multi_sku_disruption',
        'created_utc':      datetime.now(timezone.utc).isoformat(),
        'n_skus':           n_skus,
        'episodes_per_cell': n_episodes,
        'episode_length':   EPISODE_LENGTH,
        'base_seed':        BASE_SEED,
        'max_warehouse_capacity': max_warehouse_capacity,
        'paired_plan':      plan,
        'scenarios': {k: {'description': v['description'],
                          'disrupt_start': v['disrupt_start'],
                          'disrupt_end':   v['disrupt_end']}
                      for k, v in scenarios.items()},
        'policies': POLICY_DESCRIPTIONS,
        'results':          results,
        'elapsed_seconds':  elapsed_total,
    }

    if save:
        os.makedirs(DATA_DIR, exist_ok=True)
        out_path = os.path.join(DATA_DIR, 'disruption_results_multi_sku.json')
        with open(out_path, 'w') as f:
            json.dump(payload, f, indent=2)
        if verbose:
            print(f"\n  Saved -> {out_path}")
            print(f"  Total elapsed: {elapsed_total:.1f}s")

    return payload


if __name__ == '__main__':
    run_disruption_sweep()
