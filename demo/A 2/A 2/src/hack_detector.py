#!/usr/bin/env python3
"""Reward-hacking detector for saved policy theta files.

Four automated checks per policy, each returning a flag, severity, and
diagnostic message. Imports evaluation utilities from analyze_segmentation
rather than duplicating them.

Usage:
    python src/hack_detector.py                      # audit all saved thetas
    python src/hack_detector.py --theta models/network_best_theta_pernode_robust.npy
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from analyze_segmentation import _to_theta26, run_heldout, HELDOUT_RNG_SEED
from multi_echelon import _load_m5_series, STORE_SHARE

MODELS_DIR = 'models'
TRAIN_SPLIT = 0.8

# Thresholds (documented in hack_detector_methodology.md)
SL_ABANDONMENT_THRESHOLD = 0.60
DEMAND_SHARE_THRESHOLD   = 0.15
SL_FLOOR                 = 0.80
GEN_GAP_THRESHOLD        = 0.05
MIN_ORDER_QTY            = 1e-9   # mean order qty <= this -> degenerate

# Default store demand shares from multi_echelon.STORE_SHARE
STORE_DEMAND_SHARES_DEFAULT = {'A': 0.50, 'B': 0.30, 'C': 0.20}


# ───────────────── pure check functions ─────────────────

def check_store_abandonment(
    per_store_sl: Dict[str, float],
    store_demand_shares: Optional[Dict[str, float]] = None,
    sl_threshold: float = SL_ABANDONMENT_THRESHOLD,
    demand_share_threshold: float = DEMAND_SHARE_THRESHOLD,
) -> Dict:
    """Check 1 — store abandonment.

    Flags any store whose service level falls below *sl_threshold* AND whose
    demand share is at least *demand_share_threshold*. A non-trivial demand
    node should never be abandoned.
    """
    if store_demand_shares is None:
        store_demand_shares = {k: v for k, v in STORE_SHARE.items()}

    flagged = []
    details = []
    for store, sl in per_store_sl.items():
        share = store_demand_shares.get(store, 0.0)
        if sl < sl_threshold and share >= demand_share_threshold:
            flagged.append(store)
            details.append(
                f"Store {store}: SL={sl:.1%} < {sl_threshold:.0%} "
                f"(demand share {share:.0%} ≥ {demand_share_threshold:.0%})"
            )

    triggered = len(flagged) > 0
    return {
        'check': 'store_abandonment',
        'triggered': triggered,
        'severity': 'high' if triggered else 'none',
        'flagged_stores': flagged,
        'details': '; '.join(details) if details else 'All stores above SL floor',
        'mitigation': 'Apply per-node service-level constraints (Phase 2.6+)',
        'per_store_sl': per_store_sl,
        'sl_threshold': sl_threshold,
    }


def check_constraint_migration(
    val_store_sl: Dict[str, float],
    test_store_sl: Dict[str, float],
    sl_floor: float = SL_FLOOR,
) -> Dict:
    """Check 2 — constraint migration.

    Flags if every store passes *sl_floor* on validation but at least one
    falls below on test. This pattern means the optimizer learnt feasibility
    on the val split that did not transfer to the test split.
    """
    if not val_store_sl or not test_store_sl:
        return {
            'check': 'constraint_migration',
            'triggered': False,
            'severity': 'none',
            'migrated_stores': [],
            'details': 'Insufficient val/test data to evaluate constraint migration',
            'mitigation': 'N/A',
            'val_store_sl': val_store_sl,
            'test_store_sl': test_store_sl,
        }

    val_all_pass  = all(sl >= sl_floor for sl in val_store_sl.values())
    migrated = [
        s for s, sl in test_store_sl.items()
        if val_store_sl.get(s, 0.0) >= sl_floor and sl < sl_floor
    ]
    triggered = val_all_pass and len(migrated) > 0
    return {
        'check': 'constraint_migration',
        'triggered': triggered,
        'severity': 'medium' if triggered else 'none',
        'migrated_stores': migrated,
        'details': (
            f"Stores {migrated} passed {sl_floor:.0%} floor on val but failed on test"
            if triggered else 'SL floor constraint held at test time'
        ),
        'mitigation': 'Increase held-out set size; refit with robust CMA-ES',
        'val_store_sl': val_store_sl,
        'test_store_sl': test_store_sl,
        'sl_floor': sl_floor,
    }


def check_generalization_gap(
    val_profit: float,
    test_profit: float,
    threshold: float = GEN_GAP_THRESHOLD,
) -> Dict:
    """Check 3 — generalization gap.

    Flags if (val_profit - test_profit) / |val_profit| > threshold (default
    5%). A positive ratio means the policy looked meaningfully better on val
    than on test — training-specific memorization.
    """
    if val_profit == 0.0:
        gap_ratio = 0.0
    else:
        gap_ratio = (val_profit - test_profit) / abs(val_profit)

    triggered = gap_ratio > threshold
    return {
        'check': 'generalization_gap',
        'triggered': triggered,
        'severity': 'medium' if triggered else 'none',
        'val_profit': val_profit,
        'test_profit': test_profit,
        'gap_ratio': round(gap_ratio, 6),
        'threshold': threshold,
        'details': (
            f"Val profit {val_profit:+,.0f} exceeds test profit {test_profit:+,.0f} "
            f"by {gap_ratio:.1%} (threshold {threshold:.0%})"
            if triggered else
            f"Gap ratio {gap_ratio:.1%} within {threshold:.0%} threshold"
        ),
        'mitigation': 'Increase held-out evaluation episodes; apply train/val/test split',
    }


def check_degenerate_policy(
    mean_order_qty: Dict[str, float],
    store_demand_shares: Optional[Dict[str, float]] = None,
    min_qty: float = MIN_ORDER_QTY,
) -> Dict:
    """Check 4 — degenerate zero-order policy.

    Flags any store or SKU with mean order quantity ≤ *min_qty* across all
    evaluation episodes AND demand share ≥ DEMAND_SHARE_THRESHOLD. A policy
    that never orders for a non-trivial demand node is degenerate.
    """
    if store_demand_shares is None:
        store_demand_shares = {k: v for k, v in STORE_SHARE.items()}

    flagged = []
    for store, qty in mean_order_qty.items():
        share = store_demand_shares.get(store, 0.0)
        if qty <= min_qty and share >= DEMAND_SHARE_THRESHOLD:
            flagged.append(store)

    triggered = len(flagged) > 0
    return {
        'check': 'degenerate_policy',
        'triggered': triggered,
        'severity': 'high' if triggered else 'none',
        'flagged_stores': flagged,
        'details': (
            f"Stores {flagged} have mean order qty ≈ 0 across all episodes"
            if triggered else 'All stores place non-zero orders'
        ),
        'mitigation': 'Inspect theta raw values; recheck reward shaping for ordering incentive',
        'mean_order_qty': mean_order_qty,
    }


# ───────────────── evaluation data extraction ─────────────────

def _load_segmentation_result(seg_key: str, models_dir: str = MODELS_DIR) -> Optional[Dict]:
    """Load a named policy result from segmentation_analysis.json."""
    path = os.path.join(models_dir, 'segmentation_analysis.json')
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    return data.get('results', {}).get(seg_key)


def _read_robust_eval_json(eval_json: str, models_dir: str = MODELS_DIR) -> Optional[Dict]:
    """Read a robust-format evaluation JSON with val/test split fields."""
    path = os.path.join(models_dir, eval_json)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _eval_data_from_robust_json(j: Dict) -> Dict:
    """Extract standardised eval data from a robust evaluation JSON."""
    return {
        'test_store_sl':    j.get('mean_store_service_level', {}),
        'val_store_sl':     j.get('val_store_sl_deploy', {}),
        'test_profit':      j.get('mean_network_profit'),
        'val_profit':       j.get('val_profit_deploy'),
        'order_frequencies': None,
        'mean_order_qty':   None,
        'source': os.path.basename(j.get('_source', 'eval_json')),
    }


def _eval_data_from_segmentation(seg_result: Dict) -> Dict:
    """Extract standardised eval data from a segmentation_analysis entry."""
    per = seg_result.get('per_store', {})
    return {
        'test_store_sl': {
            s: per[s]['mean_service_level'] for s in per
        },
        'val_store_sl':      None,
        'test_profit':       seg_result.get('mean_network_profit'),
        'val_profit':        None,
        'order_frequencies': {s: per[s]['order_frequency'] for s in per},
        'mean_order_qty':    {s: per[s]['mean_order_qty']   for s in per},
        'source': 'segmentation_analysis.json',
    }


def _eval_data_from_simulation(
    theta26: np.ndarray,
    demand_series: np.ndarray,
    n_episodes: int = 8,
) -> Dict:
    """Run a fresh held-out evaluation and return standardised eval data."""
    result = run_heldout(theta26, demand_series, n_episodes=n_episodes)
    per = result['per_store']
    return {
        'test_store_sl': {
            s: per[s]['mean_service_level'] for s in per
        },
        'val_store_sl':      None,
        'test_profit':       result['mean_network_profit'],
        'val_profit':        None,
        'order_frequencies': {s: per[s]['order_frequency']   for s in per},
        'mean_order_qty':    {s: per[s]['mean_order_qty']     for s in per},
        'source': 'fresh_simulation',
    }


# ───────────────── theta catalogue ─────────────────

# Each entry: eval_json (new-format with val/test) and/or seg_key
THETA_CATALOGUE: List[Dict] = [
    {
        'filename': 'network_best_theta_pernode_robust.npy',
        'description': 'Phase 2.8 segmented per-node (robust)',
        'eval_json': 'network_evaluation_pernode_robust.json',
        'seg_key': 'segmented_pernode',
    },
    {
        'filename': 'uniform_best_theta_robust.npy',
        'description': 'Phase 2.8 uniform (robust)',
        'eval_json': 'uniform_evaluation_robust.json',
        'seg_key': 'uniform',
    },
    {
        'filename': 'network_best_theta.npy',
        'description': 'Phase 2.5 segmented mean-SL (legacy)',
        'eval_json': None,
        'seg_key': 'segmented_mean',
    },
    {
        'filename': 'network_best_theta_pernode.npy',
        'description': 'Phase 2.6 segmented per-node (pre-robust)',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'uniform_best_theta.npy',
        'description': 'Phase 2.0 uniform (initial)',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'cmaes_theta_seed42.npy',
        'description': 'Phase 1 CMA-ES single-store seed 42',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'cmaes_theta_seed123.npy',
        'description': 'Phase 1 CMA-ES single-store seed 123',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'cmaes_theta_seed456.npy',
        'description': 'Phase 1 CMA-ES single-store seed 456',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'cmaes_theta_seed789.npy',
        'description': 'Phase 1 CMA-ES single-store seed 789',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'cmaes_theta_seed999.npy',
        'description': 'Phase 1 CMA-ES single-store seed 999',
        'eval_json': None,
        'seg_key': None,
    },
    {
        'filename': 'grid_tuned_classical_theta.npy',
        'description': 'Phase 4.5 grid-tuned classical baseline',
        'eval_json': None,
        'seg_key': None,
    },
]


def _get_eval_data(
    entry: Dict,
    theta26: np.ndarray,
    demand_series: np.ndarray,
    models_dir: str = MODELS_DIR,
) -> Dict:
    """Return standardised eval data for one catalogue entry.

    Priority:
    1. Robust eval JSON (has val/test split) — best data
    2. segmentation_analysis.json entry — has per-store SL + order metrics
    3. Fresh simulation (8 episodes) — fallback
    """
    # Attempt 1: robust eval JSON
    if entry.get('eval_json'):
        j = _read_robust_eval_json(entry['eval_json'], models_dir)
        if j and 'mean_store_service_level' in j and 'val_store_sl_deploy' in j:
            ed = _eval_data_from_robust_json(j)
            # Also pull order_qty from segmentation if available
            if entry.get('seg_key'):
                seg = _load_segmentation_result(entry['seg_key'], models_dir)
                if seg:
                    per = seg.get('per_store', {})
                    ed['order_frequencies'] = {s: per[s]['order_frequency'] for s in per}
                    ed['mean_order_qty']    = {s: per[s]['mean_order_qty']   for s in per}
            return ed

    # Attempt 2: segmentation_analysis.json
    if entry.get('seg_key'):
        seg = _load_segmentation_result(entry['seg_key'], models_dir)
        if seg:
            return _eval_data_from_segmentation(seg)

    # Attempt 3: fresh simulation
    return _eval_data_from_simulation(theta26, demand_series)


# ───────────────── main detect function ─────────────────

def detect_hacking(
    theta_name: str,
    theta26: np.ndarray,
    eval_data: Dict,
) -> Dict:
    """Run all 4 checks on pre-loaded eval data.

    Parameters
    ----------
    theta_name : display name / filename
    theta26    : 26-vec theta
    eval_data  : standardised dict from _get_eval_data()

    Returns
    -------
    Structured dict with per-check results and a top-level 'any_triggered'.
    """
    test_sl  = eval_data.get('test_store_sl') or {}
    val_sl   = eval_data.get('val_store_sl')  or {}
    t_profit = eval_data.get('test_profit')
    v_profit = eval_data.get('val_profit')
    oqty     = eval_data.get('mean_order_qty') or {}

    c1 = check_store_abandonment(test_sl)
    c2 = check_constraint_migration(val_sl, test_sl)
    c3 = (
        check_generalization_gap(v_profit, t_profit)
        if (v_profit is not None and t_profit is not None)
        else {
            'check': 'generalization_gap',
            'triggered': False,
            'severity': 'none',
            'details': 'No val/test profit split available — check skipped',
            'mitigation': 'N/A',
            'gap_ratio': None,
        }
    )
    c4 = (
        check_degenerate_policy(oqty)
        if oqty
        else {
            'check': 'degenerate_policy',
            'triggered': False,
            'severity': 'none',
            'details': 'Order quantity data unavailable — check skipped',
            'mitigation': 'N/A',
            'mean_order_qty': {},
        }
    )

    checks = [c1, c2, c3, c4]
    any_triggered = any(c['triggered'] for c in checks)
    severity_rank = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}
    max_severity = max(checks, key=lambda c: severity_rank.get(c['severity'], 0))

    return {
        'theta': theta_name,
        'any_triggered': any_triggered,
        'max_severity': max_severity['severity'],
        'eval_source': eval_data.get('source', 'unknown'),
        'checks': {
            'store_abandonment':    c1,
            'constraint_migration': c2,
            'generalization_gap':   c3,
            'degenerate_policy':    c4,
        },
    }


# ───────────────── audit all thetas ─────────────────

def audit_all_thetas(models_dir: str = MODELS_DIR) -> Dict:
    """Audit every catalogued theta file and return the full report dict."""
    print('Loading M5 demand series ...')
    full_series = _load_m5_series()
    split_idx   = int(len(full_series) * TRAIN_SPLIT)
    test_series = full_series[split_idx:]

    results = {}
    for entry in THETA_CATALOGUE:
        fname = entry['filename']
        path  = os.path.join(models_dir, fname)
        if not os.path.exists(path):
            print(f'  SKIP {fname}: file not found')
            results[fname] = {
                'theta': fname,
                'description': entry['description'],
                'skipped': True,
                'reason': 'file not found',
            }
            continue

        raw = np.load(path)
        try:
            theta26 = _to_theta26(raw)
        except ValueError as e:
            print(f'  SKIP {fname}: {e}')
            results[fname] = {
                'theta': fname,
                'description': entry['description'],
                'skipped': True,
                'reason': str(e),
            }
            continue

        print(f'  Auditing {fname} ({entry["description"]}) ...')
        ed = _get_eval_data(entry, theta26, test_series, models_dir)
        report = detect_hacking(fname, theta26, ed)
        report['description'] = entry['description']
        results[fname] = report

        flags = [k for k, c in report['checks'].items() if c['triggered']]
        print(f'    any_triggered={report["any_triggered"]}  '
              f'severity={report["max_severity"]}  '
              f'flags={flags or "none"}')

    summary = {
        'total_audited':  sum(1 for v in results.values() if not v.get('skipped')),
        'total_flagged':  sum(1 for v in results.values()
                             if not v.get('skipped') and v.get('any_triggered')),
        'total_skipped':  sum(1 for v in results.values() if v.get('skipped')),
        'train_split':    TRAIN_SPLIT,
        'heldout_rng_seed': HELDOUT_RNG_SEED,
        'thresholds': {
            'sl_abandonment': SL_ABANDONMENT_THRESHOLD,
            'demand_share':   DEMAND_SHARE_THRESHOLD,
            'sl_floor':       SL_FLOOR,
            'gen_gap':        GEN_GAP_THRESHOLD,
        },
    }

    return {'summary': summary, 'results': results}


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Reward-hacking detector')
    parser.add_argument('--theta', default=None,
                        help='Single theta .npy file to check (default: audit all)')
    parser.add_argument('--models-dir', default=MODELS_DIR)
    args = parser.parse_args()

    if args.theta:
        full_series = _load_m5_series()
        split_idx   = int(len(full_series) * TRAIN_SPLIT)
        test_series = full_series[split_idx:]
        fname = os.path.basename(args.theta)
        raw   = np.load(args.theta)
        theta26 = _to_theta26(raw)
        entry = next(
            (e for e in THETA_CATALOGUE if e['filename'] == fname),
            {'filename': fname, 'description': 'unknown', 'eval_json': None, 'seg_key': None},
        )
        ed = _get_eval_data(entry, theta26, test_series, args.models_dir)
        report = detect_hacking(fname, theta26, ed)
        print(json.dumps(report, indent=2, default=float))
    else:
        report = audit_all_thetas(args.models_dir)
        out_path = os.path.join(args.models_dir, 'hack_detection_report.json')
        os.makedirs(args.models_dir, exist_ok=True)
        with open(out_path, 'w') as f:
            json.dump(report, f, indent=2, default=float)
        print(f'\nSaved -> {out_path}')
        s = report['summary']
        print(f"Audited: {s['total_audited']}  "
              f"Flagged: {s['total_flagged']}  "
              f"Skipped: {s['total_skipped']}")
