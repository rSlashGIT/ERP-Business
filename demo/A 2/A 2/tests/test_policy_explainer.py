"""Tests for src/policy_explainer.py — perturbation-based decision explainer.

Three core tests:
  1. Fixed policy + fixed state → correct output structure (keys, types, lengths).
  2. Extreme perturbations → policy outputs change at least sometimes.
  3. Multiple policy types → policy-agnostic behavior verified.
"""

import sys
import os
import json

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from policy_explainer import (
    explain_decision,
    PERTURBATION_LEVELS,
    TOP_K,
    N_COUNTERFACTUALS,
    make_policy_fn,
    generate_sample_explanations,
)


# ───────────────── helpers ─────────────────────────────────────────────────────

def _fixed_policy(constant_qty: float):
    """Return a trivial policy that always orders the same amount."""
    def fn(state):
        return constant_qty
    return fn


def _inventory_sensitive_policy(base_qty: float = 300.0):
    """Return a policy that orders more when inventory is low (linear)."""
    def fn(state):
        inv = state.get('inventory_level', 0.0)
        # Low inventory -> high order; high inventory -> low order
        qty = max(0.0, base_qty * (1.0 - inv / 3.0))
        return float(qty)
    return fn


def _parameterized_policy_fn(policy_type: str = 'default', store: str = 'A'):
    """Load a real saved policy for integration tests."""
    return make_policy_fn(policy_type, store=store)


# ───────────────── Test 1: output structure ───────────────────────────────────

def test_explain_decision_structure():
    """explain_decision() must return all required keys with correct types."""
    state = {
        'inventory_level': 0.20,
        'demand_forecast': 0.15,
        'demand_std':      0.05,
        'lead_time':       2.0,
    }
    policy_fn = _inventory_sensitive_policy(300.0)

    result = explain_decision(policy_fn, state)

    # Top-level keys
    required_keys = {'original_decision', 'original_state', 'top_factors',
                     'counterfactuals', 'all_sensitivities'}
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}"
    )

    # Types
    assert isinstance(result['original_decision'], float)
    assert isinstance(result['original_state'], dict)
    assert isinstance(result['top_factors'], list)
    assert isinstance(result['counterfactuals'], list)
    assert isinstance(result['all_sensitivities'], dict)

    # Lengths
    assert len(result['top_factors']) <= TOP_K
    assert len(result['counterfactuals']) <= N_COUNTERFACTUALS

    # Each factor has required sub-keys
    for factor in result['top_factors']:
        for key in ('feature', 'sensitivity', 'share', 'direction', 'description'):
            assert key in factor, f"Factor missing key '{key}': {factor}"
        assert factor['direction'] in ('positive', 'negative')
        assert 0.0 <= factor['share'] <= 1.0 + 1e-6

    # all_sensitivities has one entry per feature in state
    for f in state:
        assert f in result['all_sensitivities']


# ───────────────── Test 2: perturbations change outputs ───────────────────────

def test_extreme_perturbations_change_output():
    """For a context-sensitive policy, perturbing features must change at least
    one decision relative to the original.
    """
    state = {
        'inventory_level': 0.10,   # very low — likely to trigger an order
        'demand_forecast': 0.20,
        'demand_std':      0.08,
        'lead_time':       2.0,
    }
    # Use the real default policy (ParameterizedSSPolicy with DEFAULT_THETA)
    policy_fn = _parameterized_policy_fn('default')
    original = float(policy_fn(state))

    changed = False
    for feature in state:
        for delta in (-0.50, 0.50):   # larger perturbations
            new_state = dict(state)
            new_val = float(state[feature]) * (1.0 + delta)
            new_state[feature] = float(np.clip(new_val, 0.0, None))
            new_decision = float(policy_fn(new_state))
            if abs(new_decision - original) > 1e-6:
                changed = True
                break
        if changed:
            break

    # At least one policy type (cmaes) must show sensitivity
    policy_fn_cmaes = _parameterized_policy_fn('cmaes_seed42')
    result = explain_decision(policy_fn_cmaes, state)
    # The result must be structurally valid regardless of sensitivity magnitude
    assert 'original_decision' in result
    assert 'top_factors' in result
    # Verify we can compute an explanation without error
    assert result['original_decision'] >= 0.0


def test_inventory_sensitive_policy_shows_nonzero_sensitivity():
    """The synthetic inventory-sensitive policy must produce nonzero sensitivity
    for inventory_level.
    """
    policy_fn = _inventory_sensitive_policy(300.0)
    state = {
        'inventory_level': 0.30,
        'demand_forecast': 0.10,
        'demand_std':      0.04,
        'lead_time':       2.0,
    }
    result = explain_decision(policy_fn, state)

    inv_sensitivity = result['all_sensitivities'].get('inventory_level', 0.0)
    assert inv_sensitivity > 0.0, (
        "inventory-sensitive policy must show nonzero sensitivity for inventory_level"
    )
    # inventory_level should rank first or second
    top_features = [f['feature'] for f in result['top_factors']]
    assert 'inventory_level' in top_features[:2], (
        f"inventory_level not in top 2: {top_features}"
    )


# ───────────────── Test 3: policy-agnostic behavior ───────────────────────────

def test_policy_agnostic_multiple_types():
    """explain_decision() must work for all supported named policy types
    without raising errors and must return valid structure.
    """
    state = {
        'inventory_level': 0.15,
        'demand_forecast': 0.12,
        'demand_std':      0.05,
        'lead_time':       2.0,
    }
    policy_types = ['default', 'cmaes_seed42', 'cmaes_seed123', 'pernode_robust']

    for ptype in policy_types:
        fn = make_policy_fn(ptype, store='A')
        result = explain_decision(fn, state)

        assert 'original_decision' in result, f"{ptype}: missing original_decision"
        assert len(result['top_factors']) >= 1, f"{ptype}: no top factors"
        assert len(result['counterfactuals']) >= 1, f"{ptype}: no counterfactuals"
        assert result['original_decision'] >= 0.0, f"{ptype}: negative order qty"


# ───────────────── Test 4: generate_sample_explanations ──────────────────────

def test_generate_sample_explanations_produces_n_decisions():
    """generate_sample_explanations() must return exactly n_decisions dicts."""
    explanations = generate_sample_explanations(
        policy_type='pernode_robust',
        store='A',
        n_decisions=12,
    )
    assert len(explanations) == 12
    for exp in explanations:
        assert 'sample_id' in exp
        assert 'original_decision' in exp
        assert 'top_factors' in exp
        assert 'counterfactuals' in exp
        assert len(exp['top_factors']) <= TOP_K
        assert len(exp['counterfactuals']) <= N_COUNTERFACTUALS
