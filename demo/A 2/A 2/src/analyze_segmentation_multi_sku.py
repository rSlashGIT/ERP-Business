#!/usr/bin/env python3
"""
Phase 7 — Reward-hacking analysis at SKU scale.

Mirrors src/analyze_segmentation.py but for the 30-SKU joint network.

The Phase 2 reward-hacking finding was that under a network-mean SL
floor, CMA-ES "abandoned" Store C (low-volume store) — meeting the
mean SL floor by over-serving Stores A/B while letting C drop below
floor. Phase 2.6 fixed this with a per-node SL constraint.

At SKU scale, the analogous failure mode is: CMA-ES "abandons" the
low-volume SKUs (each contributing little to total profit) by letting
their per-(sku, store) SL drop below the floor while over-serving the
high-volume SKUs. Phase 7 enforces a per-(sku, store) SL constraint
already, so we expect the constraint to PREVENT this — but verify.

Comparison:
    naive_uniform   single 10-vec replicated 30 times
    per_sku_grid    per-SKU classical grid winner
    cmaes_joint     Phase 7 joint CMA-ES (300-dim)

For each policy, computes:
    - per-SKU mean profit, total profit
    - per-SKU mean SL (overall and per-store)
    - SKU-volume bucket breakdown (high / medium / low)
    - distribution of per-(sku, store) SL → check tail

Output:
    models/multi_sku_segmentation_analysis.json
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from multi_sku_network import (                                       # noqa: E402
    MAX_WAREHOUSE_CAPACITY_DEFAULT,
    OVERFLOW_PENALTY,
    PARAMS_PER_SKU,
    SL_FLOOR,
    STORE_IDS,
    build_network,
    load_multi_sku_data,
)


MODELS_DIR = 'models'
HELDOUT_EPISODES = 16
EPISODE_LENGTH = 90
BASE_SEED = 42

POLICY_PATHS = {
    'naive_uniform': os.path.join(MODELS_DIR, 'multi_sku_baseline_uniform_theta.npy'),
    'per_sku_grid':  os.path.join(MODELS_DIR, 'multi_sku_baseline_persku_theta.npy'),
    'cmaes_joint':   os.path.join(MODELS_DIR, 'multi_sku_theta.npy'),
}


def _evaluate(theta: np.ndarray, multi_data: Dict,
              demand_slice: Dict[str, np.ndarray],
              n_episodes: int, episode_length: int, seed: int,
              max_warehouse_capacity: float) -> Dict:
    sub_data = dict(multi_data)
    sub_data['demand_by_sku'] = demand_slice
    rng = np.random.default_rng(seed)
    min_len = min(len(d) for d in demand_slice.values())
    max_start = max(0, min_len - episode_length - 1)
    sku_ids = multi_data['sku_ids']

    profits, sls, overflows = [], [], []
    sku_profit_acc = {sid: [] for sid in sku_ids}
    sku_sl_acc = {sid: [] for sid in sku_ids}
    sku_store_sl_acc: Dict[str, Dict[str, List[float]]] = {
        sid: {st: [] for st in STORE_IDS} for sid in sku_ids
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
        for sid in sku_ids:
            sub = s['per_sku'][sid]
            sku_profit_acc[sid].append(sub['sku_total_profit'])
            sku_sl_acc[sid].append(sub['sku_service_level'])
            for st in STORE_IDS:
                sku_store_sl_acc[sid][st].append(sub['store_service_level'][st])

    sl_grid = {sid: {st: float(np.mean(sku_store_sl_acc[sid][st]))
                     for st in STORE_IDS}
               for sid in sku_ids}
    return {
        'mean_network_profit': float(np.mean(profits)),
        'std_network_profit':  float(np.std(profits)),
        'mean_service_level':  float(np.mean(sls)),
        'mean_overflow':       float(np.mean(overflows)),
        'sku_mean_profit':     {sid: float(np.mean(sku_profit_acc[sid]))
                                for sid in sku_ids},
        'sku_mean_sl':         {sid: float(np.mean(sku_sl_acc[sid]))
                                for sid in sku_ids},
        'sl_grid':             sl_grid,
    }


def _bucketize(per_sku_stats: Dict, sku_meta: Dict) -> Dict:
    """Group per-SKU values by demand bucket from the prep-summary."""
    buckets: Dict[str, Dict[str, List[float]]] = {
        'high':   {'profit': [], 'sl': [], 'min_store_sl': []},
        'medium': {'profit': [], 'sl': [], 'min_store_sl': []},
        'low':    {'profit': [], 'sl': [], 'min_store_sl': []},
    }
    for sid, stats in per_sku_stats.items():
        b = sku_meta[sid]['bucket']
        buckets[b]['profit'].append(stats['profit'])
        buckets[b]['sl'].append(stats['sl'])
        buckets[b]['min_store_sl'].append(stats['min_store_sl'])
    return {
        b: {
            'count':            len(buckets[b]['profit']),
            'mean_profit':      float(np.mean(buckets[b]['profit']))
                                if buckets[b]['profit'] else 0.0,
            'total_profit':     float(np.sum(buckets[b]['profit'])),
            'mean_sl':          float(np.mean(buckets[b]['sl']))
                                if buckets[b]['sl'] else 0.0,
            'min_sl':           float(np.min(buckets[b]['sl']))
                                if buckets[b]['sl'] else 0.0,
            'mean_min_store_sl': float(np.mean(buckets[b]['min_store_sl']))
                                 if buckets[b]['min_store_sl'] else 0.0,
            'sku_below_floor':  int(sum(1 for x in buckets[b]['min_store_sl']
                                        if x < SL_FLOOR)),
        }
        for b in buckets
    }


def run_analysis(
    n_episodes: int = HELDOUT_EPISODES,
    episode_length: int = EPISODE_LENGTH,
    max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
    save: bool = True,
    verbose: bool = True,
) -> Dict:
    multi_data = load_multi_sku_data()
    sku_ids = multi_data['sku_ids']
    sku_meta = multi_data['summary']['per_sku_stats']
    test_demand = {sid: d[int(len(d) * 0.8):]
                   for sid, d in multi_data['demand_by_sku'].items()}

    thetas: Dict[str, np.ndarray] = {}
    for key, path in POLICY_PATHS.items():
        if not os.path.exists(path):
            raise FileNotFoundError(f"{key} policy missing at {path}")
        thetas[key] = np.load(path).astype(np.float64)

    if verbose:
        print(f"\n{'='*92}")
        print(f"  Multi-SKU segmentation analysis")
        print(f"  policies: {list(thetas.keys())}")
        print(f"  episodes/policy: {n_episodes}  episode_len: {episode_length}")
        print(f"{'='*92}")

    by_policy: Dict[str, Dict] = {}
    for pol_key, theta in thetas.items():
        if verbose:
            print(f"\n  Evaluating {pol_key}...")
        ev = _evaluate(theta, multi_data, test_demand,
                       n_episodes=n_episodes, episode_length=episode_length,
                       seed=BASE_SEED + hash(pol_key) % 1000,
                       max_warehouse_capacity=max_warehouse_capacity)
        # Per-SKU stats compactified
        per_sku_stats: Dict[str, Dict] = {}
        for sid in sku_ids:
            sl = ev['sku_mean_sl'][sid]
            store_sl = ev['sl_grid'][sid]
            per_sku_stats[sid] = {
                'profit':       ev['sku_mean_profit'][sid],
                'sl':           sl,
                'store_sl':     store_sl,
                'min_store_sl': float(min(store_sl.values())),
                'bucket':       sku_meta[sid]['bucket'],
                'mean_demand':  sku_meta[sid]['mean_demand'],
            }
        # SKU SL violations: pairs (sku, store) with SL < floor
        violations: List[Dict] = []
        for sid in sku_ids:
            for st in STORE_IDS:
                if ev['sl_grid'][sid][st] < SL_FLOOR:
                    violations.append({
                        'sku_id':    sid,
                        'store':     st,
                        'sl':        ev['sl_grid'][sid][st],
                        'gap':       SL_FLOOR - ev['sl_grid'][sid][st],
                        'bucket':    sku_meta[sid]['bucket'],
                        'mean_demand': sku_meta[sid]['mean_demand'],
                    })
        violations.sort(key=lambda r: -r['gap'])

        bucket_summary = _bucketize(per_sku_stats, sku_meta)
        by_policy[pol_key] = {
            'mean_network_profit': ev['mean_network_profit'],
            'std_network_profit':  ev['std_network_profit'],
            'mean_service_level':  ev['mean_service_level'],
            'mean_overflow':       ev['mean_overflow'],
            'per_sku_stats':       per_sku_stats,
            'violations':          violations,
            'n_violations':        len(violations),
            'bucket_summary':      bucket_summary,
            'policy_path':         POLICY_PATHS[pol_key],
        }

        if verbose:
            print(f"    profit=${ev['mean_network_profit']:+,.0f}  "
                  f"network_SL={ev['mean_service_level']:.1%}  "
                  f"overflow=${ev['mean_overflow']:,.0f}")
            print(f"    violations: {len(violations)}/{len(sku_ids) * 3} "
                  f"(sku, store) pairs below {SL_FLOOR:.0%}")
            print(f"    by bucket:")
            for b in ('high', 'medium', 'low'):
                bs = bucket_summary[b]
                print(f"      {b:<6}: profit_total=${bs['total_profit']:+,.0f}  "
                      f"mean_SL={bs['mean_sl']:.1%}  "
                      f"min_SL={bs['min_sl']:.1%}  "
                      f"abandoned={bs['sku_below_floor']}/{bs['count']}")

    # Cross-policy comparison: is the joint CMA-ES doing reward hacking?
    # i.e. tilting profit toward high-volume SKUs at the expense of low-volume.
    if 'cmaes_joint' in by_policy and 'per_sku_grid' in by_policy:
        joint = by_policy['cmaes_joint']
        persku = by_policy['per_sku_grid']
        comparison: Dict = {}
        for b in ('high', 'medium', 'low'):
            jp = joint['bucket_summary'][b]
            pp = persku['bucket_summary'][b]
            comparison[b] = {
                'joint_total_profit':  jp['total_profit'],
                'persku_total_profit': pp['total_profit'],
                'joint_min_sl':        jp['min_sl'],
                'persku_min_sl':       pp['min_sl'],
                'joint_abandoned':     jp['sku_below_floor'],
                'persku_abandoned':    pp['sku_below_floor'],
                'profit_diff':         jp['total_profit'] - pp['total_profit'],
            }
        # Reward-hacking detection: joint earns more for high-volume but
        # less for low-volume vs per-SKU baseline (stable comparison).
        rh_signal = (
            comparison['high']['profit_diff']
            - comparison['low']['profit_diff']
        )
        cross = {
            'comparison_by_bucket': comparison,
            'reward_hacking_signal': rh_signal,
            'reward_hacking_signal_explanation': (
                "Positive value means joint CMA-ES gains more on high-volume "
                "SKUs than it gains on low-volume SKUs (relative to the "
                "per-SKU grid baseline) — the multi-SKU equivalent of "
                "abandoning Store C. A near-zero or negative value means "
                "the per-(sku, store) SL constraint successfully prevented "
                "scale-out reward hacking."
            ),
        }
    else:
        cross = {}

    payload = {
        'phase':          'phase7_multi_sku_segmentation',
        'n_skus':         len(sku_ids),
        'n_episodes':     n_episodes,
        'episode_length': episode_length,
        'sl_floor':       SL_FLOOR,
        'by_policy':      by_policy,
        'cross_policy':   cross,
    }

    if save:
        out_path = os.path.join(MODELS_DIR, 'multi_sku_segmentation_analysis.json')
        with open(out_path, 'w') as f:
            json.dump(payload, f, indent=2)
        if verbose:
            print(f"\n  Saved -> {out_path}")
            if cross:
                print(f"\n  Reward-hacking signal: ${cross['reward_hacking_signal']:+,.0f}")
                print(f"  (joint − per-SKU profit difference: high-bucket "
                      f"gain minus low-bucket gain)")

    return payload


if __name__ == '__main__':
    run_analysis()
