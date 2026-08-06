#!/usr/bin/env python3
"""
Phase 2.5 — Segmented-vs-uniform MEIO policy analysis.

Computes per-store policy metrics for the segmented 24-parameter CMA-ES
solution and compares three policies on matched held-out episodes:

    1. Default classical (s, S) (DEFAULT_THETA replicated to each store)
    2. Uniform 8-parameter CMA-ES (same theta for all 3 stores)
    3. Segmented 24-parameter CMA-ES (distinct theta per store)

Outputs:
    models/segmentation_analysis.json    structured results
    stdout                                3-row comparison table

The per-store metrics (mean order qty, order frequency, SL per store,
cost per unit sold) are used to judge whether the segmented solution
actually learned *different* policies for A / B / C. If the per-store
thetas are close (Euclidean distance small) OR the behavioural metrics
are close, that is reported honestly — segmentation of the demand alone
does not guarantee that the extra parameters are exploited.

Run with:
    python src/analyze_segmentation.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from multi_echelon import (                                          # noqa: E402
    HOLDING_COST_FACTOR,
    STOCKOUT_PENALTY,
    SupplyChainNetwork,
    _load_m5_series,
    default_theta26,
    split_theta26,
)

MODELS_DIR        = 'models'
HELDOUT_EPISODES  = 16
EPISODE_LENGTH    = 90
TRAIN_SPLIT       = 0.8
HELDOUT_RNG_SEED  = 20260419  # matches train_network_cmaes._final_eval


# ───────────────── policy assembly ─────────────────

def _to_theta26(raw: np.ndarray) -> np.ndarray:
    """Normalise any saved theta to a deployable 26-vec.

    Accepts:
        - 26-vec: already canonical, returned as-is.
        - 24-vec: legacy segmented (pre-Phase 2.8); pad with raw=(0,0)
          which transforms to the legacy default warehouse regime
          (s=400, S=1000) — NOT the original (1000, 3000) constants.
          That is intentional: the only reason these legacy thetas
          exist is for back-compat reporting; their original training
          ran against fixed (1000, 3000), so analytics on them are
          approximate even before the warehouse change.
        - 10-vec: Phase 2.8 uniform raw — replicate 8-vec store
          theta and append the 2-vec warehouse raw controls.
        - 8-vec:  legacy uniform — replicate to 24-vec, append (0,0).
    """
    raw = np.asarray(raw, dtype=np.float64)
    n = raw.size
    if n == 26:
        return raw.copy()
    if n == 24:
        return np.concatenate([raw, np.zeros(2, dtype=np.float64)])
    if n == 10:
        store = raw[:8]
        wh    = raw[8:10]
        return np.concatenate([store, store, store, wh])
    if n == 8:
        return np.concatenate([raw, raw, raw, np.zeros(2, dtype=np.float64)])
    raise ValueError(
        f"unrecognised theta size {n}; expected 8, 10, 24 or 26"
    )


def load_policies() -> Dict[str, Optional[np.ndarray]]:
    """Return dict of 26-vec thetas keyed by policy name. Missing files
    return None so the caller can skip them with a printed warning."""
    out: Dict[str, Optional[np.ndarray]] = {}

    # Phase 2.8 default: 26-vec with raw=(0,0) → warehouse (400, 1000).
    out['default'] = default_theta26().astype(np.float64)

    # Prefer the most recent robust model files first; fall back to
    # legacy filenames so older runs still appear in the comparison.
    candidate_files = {
        'uniform': [
            'uniform_best_theta_robust.npy',
            'uniform_best_theta.npy',
        ],
        'segmented_pernode': [
            'network_best_theta_pernode_robust.npy',
            'network_best_theta_pernode.npy',
        ],
        'segmented_mean': [
            'network_best_theta_robust.npy',
            'network_best_theta.npy',
        ],
    }

    for key, names in candidate_files.items():
        loaded = None
        for name in names:
            p = os.path.join(MODELS_DIR, name)
            if os.path.exists(p):
                loaded = _to_theta26(np.load(p))
                break
        out[key] = loaded

    # Back-compat key used by older reporting code paths: point to
    # whichever segmented model is current (pernode if available).
    out['segmented'] = (out['segmented_pernode']
                        if out['segmented_pernode'] is not None
                        else out['segmented_mean'])

    return out


# ───────────────── held-out evaluator ─────────────────

def run_heldout(theta26: np.ndarray,
                demand_series: np.ndarray,
                n_episodes: int = HELDOUT_EPISODES,
                episode_length: int = EPISODE_LENGTH) -> Dict:
    """Run a fixed-seed held-out evaluation and return aggregated stats.

    Seeds are drawn from a single rng seeded with HELDOUT_RNG_SEED so the
    same matched episodes are used for every policy called inside one
    process invocation.

    Phase 2.8: takes a 26-vec theta (24 store params + 2 raw warehouse
    controls). The warehouse (s, S) are extracted via split_theta26 and
    passed to SupplyChainNetwork so the held-out episodes use the same
    learned warehouse policy as training.
    """
    parts  = split_theta26(theta26)
    thetas = {'A': parts['A'], 'B': parts['B'], 'C': parts['C']}
    wh_s   = parts['warehouse_s']
    wh_S   = parts['warehouse_S']
    rng = np.random.default_rng(HELDOUT_RNG_SEED)
    L = len(demand_series)
    max_start = max(0, L - episode_length - 1)

    ep_profits: List[float] = []
    ep_sls:     List[float] = []
    ep_store_profits = {'A': [], 'B': [], 'C': []}

    # Aggregates for per-store behavioural metrics (segmented only cares
    # but we compute for all so the JSON is uniform).
    orders_placed = {'A': [], 'B': [], 'C': []}
    mean_order_qty_per_episode = {'A': [], 'B': [], 'C': []}
    sales_units = {'A': [], 'B': [], 'C': []}
    demand_units = {'A': [], 'B': [], 'C': []}
    procurement_cost = {'A': [], 'B': [], 'C': []}
    holding_cost = {'A': [], 'B': [], 'C': []}
    stockout_cost = {'A': [], 'B': [], 'C': []}

    for _ in range(n_episodes):
        start = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        ep_seed = int(rng.integers(0, 2**31 - 1))
        net = SupplyChainNetwork(
            thetas=thetas,
            demand_series=demand_series,
            start_day=start,
            seed=ep_seed,
            warehouse_s_lower=wh_s,
            warehouse_s_upper=wh_S,
        )
        summary = net.simulate(n_steps=episode_length)
        ep_profits.append(summary['network_total_profit'])
        ep_sls.append(summary['service_level'])

        for sid in ('A', 'B', 'C'):
            store = net.stores[sid]
            ep_store_profits[sid].append(summary['store_profits'][sid])
            orders_placed[sid].append(store.metrics.orders_placed)
            sales_units[sid].append(store.metrics.sales_units)
            demand_units[sid].append(store.metrics.demand_units)
            procurement_cost[sid].append(store.metrics.procurement_cost)
            holding_cost[sid].append(store.metrics.holding_cost)
            stockout_cost[sid].append(store.metrics.stockout_cost)

        # Mean per-episode order quantity per store, across days: total
        # shipped to that store / number of non-zero order days. Derived
        # from daily_records so we can separate "orders placed" from
        # "units shipped".
        records = net.daily_records
        for sid in ('A', 'B', 'C'):
            shipped_by_day = [r['store_shipped'][sid] for r in records]
            ordered_by_day = [r['store_orders'][sid]  for r in records]
            n_order_days = sum(1 for q in ordered_by_day if q > 1e-9)
            total_shipped = sum(shipped_by_day)
            if n_order_days > 0:
                mean_order_qty_per_episode[sid].append(
                    total_shipped / n_order_days
                )
            else:
                mean_order_qty_per_episode[sid].append(0.0)

    per_store = {}
    for sid in ('A', 'B', 'C'):
        d = float(np.mean(demand_units[sid]))
        s = float(np.mean(sales_units[sid]))
        units_sold = s
        total_cost = (float(np.mean(procurement_cost[sid]))
                      + float(np.mean(holding_cost[sid]))
                      + float(np.mean(stockout_cost[sid])))
        per_store[sid] = {
            'mean_orders_placed_per_episode': float(np.mean(orders_placed[sid])),
            'order_frequency':                float(np.mean(orders_placed[sid])
                                                     / episode_length),
            'mean_order_qty':                 float(np.mean(mean_order_qty_per_episode[sid])),
            'mean_store_profit':              float(np.mean(ep_store_profits[sid])),
            'mean_service_level':             (s / d) if d > 1e-9 else 1.0,
            'mean_demand_per_episode':        d,
            'mean_sales_per_episode':         s,
            'mean_procurement_cost':          float(np.mean(procurement_cost[sid])),
            'mean_holding_cost':              float(np.mean(holding_cost[sid])),
            'mean_stockout_cost':             float(np.mean(stockout_cost[sid])),
            'cost_per_unit_sold': (total_cost / units_sold) if units_sold > 1e-9 else float('nan'),
        }

    return {
        'n_episodes':           n_episodes,
        'episode_length':       episode_length,
        'mean_network_profit':  float(np.mean(ep_profits)),
        'std_network_profit':   float(np.std(ep_profits)),
        'mean_service_level':   float(np.mean(ep_sls)),
        'per_store':            per_store,
        'mean_store_profits':   {sid: float(np.mean(ep_store_profits[sid]))
                                 for sid in ('A', 'B', 'C')},
    }


# ───────────────── specialization diagnostics ─────────────────

def theta_distance(theta26: np.ndarray) -> Dict:
    """Pairwise Euclidean distances between the per-store 8-vec thetas.
    Small distances => stores converged to similar policies."""
    per = split_theta26(theta26)
    a, b, c = per['A'], per['B'], per['C']
    return {
        'A_vs_B': float(np.linalg.norm(a - b)),
        'A_vs_C': float(np.linalg.norm(a - c)),
        'B_vs_C': float(np.linalg.norm(b - c)),
        'max_pairwise': float(max(
            np.linalg.norm(a - b),
            np.linalg.norm(a - c),
            np.linalg.norm(b - c),
        )),
    }


def behavioural_divergence(segmented_result: Dict) -> Dict:
    """Coefficient of variation of per-store behavioural metrics. Low CV
    => 3 stores behave the same despite having separate thetas."""
    def _cv(xs: List[float]) -> float:
        m = float(np.mean(xs))
        s = float(np.std(xs))
        return (s / abs(m)) if abs(m) > 1e-9 else 0.0

    per = segmented_result['per_store']
    metric_names = [
        'mean_order_qty',
        'order_frequency',
        'mean_service_level',
        'cost_per_unit_sold',
    ]
    cv = {}
    for m in metric_names:
        vals = [per['A'][m], per['B'][m], per['C'][m]]
        vals = [v for v in vals if not (isinstance(v, float) and np.isnan(v))]
        cv[m] = _cv(vals) if len(vals) == 3 else float('nan')
    return cv


# ───────────────── reporting ─────────────────

def print_comparison_table(results: Dict[str, Optional[Dict]]) -> None:
    order = ('default', 'uniform', 'segmented_mean', 'segmented_pernode')
    labels = {
        'default':             'Default classical (s,S)',
        'uniform':             'Uniform 8-param CMA-ES',
        'segmented_mean':      'Segmented 24 (mean-floor, legacy)',
        'segmented_pernode':   'Segmented 24 (per-node floor)',
    }

    print()
    print('=' * 112)
    print(f"  Phase 2.5 / 2.6 held-out comparison "
          f"({HELDOUT_EPISODES} matched episodes × {EPISODE_LENGTH} days)")
    print('=' * 112)
    hdr = (f"  {'Policy':<34} "
           f"{'NetProfit':>14} {'±Std':>10} {'NetSL':>7} "
           f"{'SL_A':>6} {'SL_B':>6} {'SL_C':>6} "
           f"{'A $':>11} {'B $':>11} {'C $':>11}")
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for key in order:
        r = results.get(key)
        if r is None:
            print(f"  {labels[key]:<34} {'(missing)':>14}")
            continue
        per = r['per_store']
        print(
            f"  {labels[key]:<34} "
            f"{r['mean_network_profit']:>+14,.0f} "
            f"{r['std_network_profit']:>10,.0f} "
            f"{r['mean_service_level']:>6.1%} "
            f"{per['A']['mean_service_level']:>5.1%} "
            f"{per['B']['mean_service_level']:>5.1%} "
            f"{per['C']['mean_service_level']:>5.1%} "
            f"{r['mean_store_profits']['A']:>+11,.0f} "
            f"{r['mean_store_profits']['B']:>+11,.0f} "
            f"{r['mean_store_profits']['C']:>+11,.0f}"
        )
    print('=' * 112)


def print_per_store_block(segmented_result: Dict,
                          segmented_theta: np.ndarray) -> None:
    per = segmented_result['per_store']
    parts = split_theta26(segmented_theta)
    thetas = {'A': parts['A'], 'B': parts['B'], 'C': parts['C']}
    print()
    print('  Per-store behaviour (segmented 24-param CMA-ES, held-out):')
    print(f"  Warehouse policy: s={parts['warehouse_s']:.0f} "
          f"S={parts['warehouse_S']:.0f}")
    print(f"  {'Store':<6} {'s_base':>8} {'S_base':>8} "
          f"{'OrderQty':>10} {'OrderFreq':>10} {'SL':>7} "
          f"{'Cost/Unit':>10} {'Profit':>12}")
    print('  ' + '-' * 86)
    for sid in ('A', 'B', 'C'):
        t = thetas[sid]
        p = per[sid]
        cpu = p['cost_per_unit_sold']
        cpu_str = f"{cpu:>10.3f}" if np.isfinite(cpu) else f"{'nan':>10}"
        print(
            f"  {sid:<6} "
            f"{t[0]:>+8.2f} {t[1]:>+8.2f} "
            f"{p['mean_order_qty']:>10.1f} "
            f"{p['order_frequency']:>10.3f} "
            f"{p['mean_service_level']:>6.1%} "
            f"{cpu_str} "
            f"{p['mean_store_profit']:>+12,.0f}"
        )


# ───────────────── main ─────────────────

def main():
    print(f"Loading M5 demand and splitting train/test ({TRAIN_SPLIT:.0%}/"
          f"{1 - TRAIN_SPLIT:.0%})...")
    full_series = _load_m5_series()
    split_idx = int(len(full_series) * TRAIN_SPLIT)
    test_series = full_series[split_idx:]
    if len(test_series) <= EPISODE_LENGTH + 1:
        raise RuntimeError(
            f"Held-out split too short: {len(test_series)} days for "
            f"episode_length={EPISODE_LENGTH}"
        )

    print(f"Test-split days: {len(test_series)}  "
          f"HOLDING_COST_FACTOR={HOLDING_COST_FACTOR}  "
          f"STOCKOUT_PENALTY={STOCKOUT_PENALTY}")

    thetas = load_policies()

    results: Dict[str, Optional[Dict]] = {}
    eval_keys = ('default', 'uniform', 'segmented_mean', 'segmented_pernode')
    for key in eval_keys:
        t = thetas.get(key)
        if t is None:
            print(f"  SKIP {key}: model file not found")
            results[key] = None
            continue
        print(f"  Evaluating {key} policy on {HELDOUT_EPISODES} matched "
              f"held-out episodes...")
        results[key] = run_heldout(t, test_series)
    # Back-compat alias: existing callers read results['segmented'].
    results['segmented'] = (results.get('segmented_pernode')
                            or results.get('segmented_mean'))

    diag: Dict = {}
    if thetas['segmented'] is not None:
        diag['theta_distances'] = theta_distance(thetas['segmented'])
        diag['behavioural_cv'] = behavioural_divergence(results['segmented'])

        # Honest flag: are the per-store thetas actually different?
        max_pair = diag['theta_distances']['max_pairwise']
        mean_cv_metrics = ['mean_order_qty', 'order_frequency',
                           'cost_per_unit_sold']
        behav_cvs = [diag['behavioural_cv'][m]
                     for m in mean_cv_metrics
                     if np.isfinite(diag['behavioural_cv'][m])]
        mean_behav_cv = float(np.mean(behav_cvs)) if behav_cvs else 0.0
        diag['specialization_check'] = {
            'max_pairwise_theta_distance': max_pair,
            'mean_behavioural_cv':          mean_behav_cv,
            'stores_differentiated':        bool(max_pair > 5.0 and mean_behav_cv > 0.10),
            'note': (
                'stores_differentiated=True when max_pairwise_theta_distance>5 '
                'AND mean behavioural CV across (order qty, order frequency, '
                'cost/unit sold) > 0.10. If False, segmentation did NOT buy '
                'distinct policies and the uniform baseline is the honest '
                'comparison.'
            ),
        }

    # 3-row summary dict for the comparison table (JSON-safe).
    summary: Dict = {
        'heldout_episodes':   HELDOUT_EPISODES,
        'episode_length':     EPISODE_LENGTH,
        'train_split':        TRAIN_SPLIT,
        'heldout_rng_seed':   HELDOUT_RNG_SEED,
        'results':            results,
        'diagnostics':        diag,
    }

    print_comparison_table(results)
    if thetas['segmented'] is not None:
        print_per_store_block(results['segmented'], thetas['segmented'])

        td = diag['theta_distances']
        cv = diag['behavioural_cv']
        sc = diag['specialization_check']
        print()
        print('  Specialization diagnostics:')
        print(f"    Per-store theta distances: "
              f"A-B={td['A_vs_B']:.2f}  A-C={td['A_vs_C']:.2f}  "
              f"B-C={td['B_vs_C']:.2f}  (max={td['max_pairwise']:.2f})")
        print(f"    Behavioural CV — order_qty={cv['mean_order_qty']:.3f}  "
              f"order_freq={cv['order_frequency']:.3f}  "
              f"cost/unit={cv['cost_per_unit_sold']:.3f}")
        print(f"    stores_differentiated = {sc['stores_differentiated']}")
        if not sc['stores_differentiated']:
            print(
                "    NOTE: the segmented solution did NOT clearly specialise "
                "per store; treat any segmented-vs-uniform profit delta with "
                "caution and prefer the simpler uniform baseline in reporting."
            )

    # Narrative verdicts.
    uni = (results['uniform']['mean_network_profit']
           if results['uniform'] is not None else None)
    seg_mean = (results.get('segmented_mean', {}) or {}).get('mean_network_profit')
    seg_pernode = (results.get('segmented_pernode', {}) or {}).get('mean_network_profit')
    diag['segmented_vs_uniform'] = {
        'uniform_profit':         uni,
        'segmented_mean_profit':  seg_mean,
        'segmented_pernode_profit': seg_pernode,
    }
    print()
    if uni is not None and seg_pernode is not None:
        delta = seg_pernode - uni
        diag['segmented_vs_uniform']['pernode_vs_uniform_delta'] = delta
        diag['segmented_vs_uniform']['pernode_beats_uniform']     = bool(delta > 0)
        if delta > 0:
            print(f"  Segmented (per-node) beats uniform by ${delta:,.0f} — "
                  f"per-node constraints plus 24 params pay off.")
        else:
            print(f"  Uniform matches or beats segmented (per-node) by "
                  f"${-delta:,.0f} — per-node constraints alone did not "
                  f"unlock the 24-param advantage. Report honestly.")
    if uni is not None and seg_mean is not None and seg_pernode is not None:
        delta_fix = seg_pernode - seg_mean
        diag['segmented_vs_uniform']['pernode_vs_mean_delta'] = delta_fix
        if delta_fix > 0:
            print(f"  Per-node floor recovered ${delta_fix:,.0f} of "
                  f"profit vs the mean-floor segmented baseline.")
        else:
            print(f"  Per-node floor cost ${-delta_fix:,.0f} of profit "
                  f"vs the mean-floor segmented baseline (but should fix "
                  f"the Store-C abandonment — see SL_C column above).")

    os.makedirs(MODELS_DIR, exist_ok=True)
    out_path = os.path.join(MODELS_DIR, 'segmentation_analysis.json')
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2, default=float)
    print(f"\n  Saved analysis -> {out_path}")


if __name__ == '__main__':
    main()
