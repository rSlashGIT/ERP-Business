"""Tests for src/hack_detector.py — reward-hacking detection.

Three tests:
  1. Legacy mean-SL theta (network_best_theta.npy) flags Check 1 (store abandonment)
     with severity 'high'.
  2. Robust per-node theta (network_best_theta_pernode_robust.npy) returns all
     four checks False.
  3. Synthetic policy with known generalization gap flags Check 3.

All tests use the pure check functions directly so no simulation is needed.
"""

import sys
import os
import json

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hack_detector import (
    check_store_abandonment,
    check_constraint_migration,
    check_generalization_gap,
    check_degenerate_policy,
    detect_hacking,
    STORE_DEMAND_SHARES_DEFAULT,
)


# ───────────────── Test 1: legacy mean-SL theta flags Check 1 ─────────────────

def test_legacy_mean_sl_theta_flags_check1():
    """network_best_theta.npy (segmented_mean) has Store C SL 22.8% and
    zero mean order qty — known Phase 2.5 reward-hacking finding.
    Check 1 must trigger with severity 'high'.
    """
    # Values directly from models/segmentation_analysis.json, segmented_mean entry
    per_store_sl   = {'A': 1.0, 'B': 1.0, 'C': 0.2228}
    mean_order_qty = {'A': 89.79, 'B': 60.34, 'C': 0.0}

    c1 = check_store_abandonment(per_store_sl)
    c4 = check_degenerate_policy(mean_order_qty)

    assert c1['triggered'], "Store C (22.8% SL, 20% demand share) must trigger Check 1"
    assert c1['severity'] == 'high'
    assert 'C' in c1['flagged_stores']

    assert c4['triggered'], "Store C mean_order_qty=0 must trigger Check 4"
    assert 'C' in c4['flagged_stores']


# ───────────────── Test 2: robust per-node theta passes all checks ─────────────

def test_robust_pernode_theta_all_false():
    """network_best_theta_pernode_robust.npy passes all four checks.
    Values from models/network_evaluation_pernode_robust.json and
    models/segmentation_analysis.json (segmented_pernode entry).
    """
    # From network_evaluation_pernode_robust.json
    test_store_sl  = {'A': 0.8670, 'B': 1.0, 'C': 1.0}
    val_store_sl   = {'A': 0.9529, 'B': 1.0, 'C': 0.9952}
    val_profit     = -6607.718892760827
    test_profit    = -6083.4701928297
    # From segmentation_analysis.json, segmented_pernode
    mean_order_qty = {'A': 49.81, 'B': 50.0, 'C': 49.85}

    c1 = check_store_abandonment(test_store_sl)
    c2 = check_constraint_migration(val_store_sl, test_store_sl)
    c3 = check_generalization_gap(val_profit, test_profit)
    c4 = check_degenerate_policy(mean_order_qty)

    assert not c1['triggered'], f"Check 1 should be False; got: {c1['details']}"
    assert not c2['triggered'], f"Check 2 should be False; got: {c2['details']}"
    assert not c3['triggered'], f"Check 3 should be False; got: {c3['details']}"
    assert not c4['triggered'], f"Check 4 should be False; got: {c4['details']}"

    for c in (c1, c2, c3, c4):
        assert c['severity'] == 'none'


# ───────────────── Test 3: synthetic gap triggers Check 3 ─────────────────────

def test_synthetic_generalization_gap_triggers_check3():
    """Construct a val/test pair where val profit >> test profit.
    gap_ratio = (1000 - 800) / 1000 = 0.20 > 0.05 threshold.
    """
    val_profit  =  1000.0
    test_profit =   800.0

    result = check_generalization_gap(val_profit, test_profit)

    assert result['triggered'], "20% gap should trigger Check 3"
    assert result['severity'] == 'medium'
    assert result['gap_ratio'] > 0.05
    assert abs(result['gap_ratio'] - 0.20) < 1e-6

    # Also verify the inverse (test > val) does NOT trigger
    result_inv = check_generalization_gap(-600.0, -550.0)
    assert not result_inv['triggered'], "Negative gap (test >= val) must not trigger"


# ───────────────── Additional structural tests ────────────────────────────────

def test_detect_hacking_returns_required_keys():
    """detect_hacking() output must contain all required keys and types."""
    eval_data = {
        'test_store_sl':    {'A': 0.90, 'B': 0.95, 'C': 0.92},
        'val_store_sl':     {'A': 0.91, 'B': 0.96, 'C': 0.93},
        'test_profit':      -1000.0,
        'val_profit':       -1050.0,
        'mean_order_qty':   {'A': 50.0, 'B': 40.0, 'C': 30.0},
        'source':           'test',
    }
    theta26 = np.zeros(26)
    report = detect_hacking('synthetic.npy', theta26, eval_data)

    assert 'theta' in report
    assert 'any_triggered' in report
    assert isinstance(report['any_triggered'], bool)
    assert 'max_severity' in report
    assert 'checks' in report
    for key in ('store_abandonment', 'constraint_migration',
                'generalization_gap', 'degenerate_policy'):
        assert key in report['checks']
        c = report['checks'][key]
        assert 'triggered' in c
        assert 'severity' in c
        assert 'details' in c
        assert 'mitigation' in c


def test_hack_detection_report_json_exists():
    """models/hack_detection_report.json must exist and be valid."""
    path = os.path.join('models', 'hack_detection_report.json')
    assert os.path.exists(path), (
        f"{path} not found — run python src/hack_detector.py to generate it"
    )
    with open(path) as f:
        data = json.load(f)
    assert 'summary' in data
    assert 'results' in data
    assert data['summary']['total_audited'] >= 3
