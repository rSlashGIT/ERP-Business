#!/usr/bin/env python3
"""Perturbation-based decision explainer for inventory policies.

Works with any callable policy that maps a state dict to an order quantity.
Policy-agnostic: ParameterizedSSPolicy, grid-tuned classical, CMA-ES,
multi-SKU variants.

Usage:
    from src.policy_explainer import explain_decision, make_policy_fn

    policy_fn = make_policy_fn('lgbm')  # or any callable
    explanation = explain_decision(policy_fn, state, feature_names)
    print(json.dumps(explanation, indent=2))

Or CLI:
    python src/policy_explainer.py --store A --day 1600
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

MODELS_DIR = 'models'

# Perturbation levels applied one feature at a time.
PERTURBATION_LEVELS = [-0.20, -0.10, 0.10, 0.20]

# Number of top factors to report.
TOP_K = 5

# Number of counterfactual statements to generate.
N_COUNTERFACTUALS = 5


# ───────────────── feature definitions ─────────────────

# Canonical feature names and their plausible lower/upper bounds for
# clamping. Bounds prevent nonsensical counterfactuals (e.g. negative demand).
FEATURE_BOUNDS: Dict[str, tuple] = {
    'inventory_level': (0.0, 3.0),     # normalized: inventory/1500, clipped [0,3]
    'demand_forecast': (0.0, 2.0),     # normalized: mean_demand/300
    'demand_std':      (0.0, 2.0),     # normalized: (mean/300)*cv
    'lead_time':       (1.0, 10.0),    # days, always >= 1
}


def _clamp_feature(name: str, value: float) -> float:
    lo, hi = FEATURE_BOUNDS.get(name, (-np.inf, np.inf))
    return float(np.clip(value, lo, hi))


def _perturb_state(state: Dict[str, float], feature: str, delta: float) -> Dict[str, float]:
    """Return a copy of *state* with *feature* perturbed by *delta* (relative)
    and clamped to plausible range."""
    new = dict(state)
    orig = float(state.get(feature, 0.0))
    perturbed = orig * (1.0 + delta)
    new[feature] = _clamp_feature(feature, perturbed)
    return new


# ───────────────── core explainer ─────────────────

def explain_decision(
    policy_fn: Callable[[Dict[str, float]], float],
    state: Dict[str, float],
    feature_names: Optional[List[str]] = None,
) -> Dict:
    """Explain one ordering decision via perturbation-based sensitivity analysis.

    Parameters
    ----------
    policy_fn : callable (state dict) -> order quantity (float)
    state     : dict of feature name -> value (current state)
    feature_names : features to perturb (default: all keys in state)

    Returns
    -------
    JSON-serialisable dict with:
        original_decision   : float
        top_factors         : list of dicts (feature, sensitivity, direction)
        counterfactuals     : list of strings
        all_sensitivities   : dict feature -> float
    """
    if feature_names is None:
        feature_names = list(state.keys())

    original_decision = float(policy_fn(state))

    # ── sensitivity per feature ──────────────────────────────────────────
    raw_sensitivities: Dict[str, List[float]] = {f: [] for f in feature_names}

    for feature in feature_names:
        orig_val = float(state.get(feature, 0.0))
        if abs(orig_val) < 1e-9:
            orig_val_nonzero = 1e-9
        else:
            orig_val_nonzero = orig_val

        for delta in PERTURBATION_LEVELS:
            perturbed = _perturb_state(state, feature, delta)
            new_decision = float(policy_fn(perturbed))
            # Sensitivity = change in decision / perturbation magnitude (relative)
            decision_change = new_decision - original_decision
            sensitivity = decision_change / (abs(delta) * abs(orig_val_nonzero) + 1e-9)
            raw_sensitivities[feature].append(sensitivity)

    # Aggregate: mean absolute sensitivity across all perturbation levels
    agg_sensitivity: Dict[str, float] = {
        f: float(np.mean(np.abs(raw_sensitivities[f])))
        for f in feature_names
    }

    # Direction: sign of mean signed sensitivity (does more X -> more order?)
    agg_direction: Dict[str, str] = {}
    for f in feature_names:
        mean_signed = float(np.mean(raw_sensitivities[f]))
        agg_direction[f] = 'positive' if mean_signed >= 0 else 'negative'

    # ── top-K factors ────────────────────────────────────────────────────
    sorted_features = sorted(
        feature_names, key=lambda f: agg_sensitivity[f], reverse=True
    )
    top_features = sorted_features[:TOP_K]

    total_sensitivity = sum(agg_sensitivity.values()) + 1e-12
    top_factors = [
        {
            'feature':     f,
            'sensitivity': round(agg_sensitivity[f], 4),
            'share':       round(agg_sensitivity[f] / total_sensitivity, 4),
            'direction':   agg_direction[f],
            'description': _feature_description(f, state, agg_direction[f]),
        }
        for f in top_features
    ]

    # ── counterfactuals ──────────────────────────────────────────────────
    counterfactuals = _generate_counterfactuals(
        policy_fn, state, original_decision,
        sorted_features[:N_COUNTERFACTUALS],
    )

    return {
        'original_decision': original_decision,
        'original_state':    {k: round(float(v), 4) for k, v in state.items()},
        'top_factors':       top_factors,
        'counterfactuals':   counterfactuals,
        'all_sensitivities': {k: round(v, 4) for k, v in agg_sensitivity.items()},
    }


def _feature_description(feature: str, state: Dict, direction: str) -> str:
    val = state.get(feature, 0.0)
    arrow = 'higher' if direction == 'positive' else 'lower'
    descriptions = {
        'inventory_level': (
            f"Current inventory is {val:.2f} (normalized). "
            f"{'Higher' if direction == 'positive' else 'Lower'} inventory -> "
            f"{arrow} order."
        ),
        'demand_forecast': (
            f"Forecasted demand is {val:.2f} (normalized). "
            f"Higher forecast -> {arrow} order."
        ),
        'demand_std': (
            f"Demand uncertainty (std) is {val:.2f}. "
            f"Higher uncertainty -> {arrow} order."
        ),
        'lead_time': (
            f"Lead time is {val:.1f} days. "
            f"Longer lead time -> {arrow} order."
        ),
    }
    return descriptions.get(feature, f"{feature}={val:.3f}; {direction} effect on order qty")


def _generate_counterfactuals(
    policy_fn: Callable,
    state: Dict[str, float],
    original_decision: float,
    features: List[str],
) -> List[str]:
    """Generate human-readable counterfactual statements for the top features."""
    counterfactuals = []
    seen = 0

    for feature in features:
        if seen >= N_COUNTERFACTUALS:
            break
        orig_val = float(state.get(feature, 0.0))

        # Try +20% first, then -20%
        for multiplier in [1.20, 0.80]:
            new_val = _clamp_feature(feature, orig_val * multiplier)
            if abs(new_val - orig_val) < 1e-6:
                continue  # clamped to same value, skip
            new_state = dict(state)
            new_state[feature] = new_val
            new_decision = float(policy_fn(new_state))

            direction_word = 'higher' if multiplier > 1 else 'lower'
            pct = int(abs(multiplier - 1) * 100)
            cf = (
                f"If {_feature_label(feature)} were {pct}% {direction_word} "
                f"({orig_val:.3f} -> {new_val:.3f}), "
                f"recommend {new_decision:.0f} units instead of {original_decision:.0f}."
            )
            counterfactuals.append(cf)
            seen += 1
            if seen >= N_COUNTERFACTUALS:
                break

    return counterfactuals


def _feature_label(feature: str) -> str:
    labels = {
        'inventory_level': 'inventory level',
        'demand_forecast':  'demand forecast',
        'demand_std':       'demand uncertainty',
        'lead_time':        'lead time',
    }
    return labels.get(feature, feature)


# ───────────────── policy factory ─────────────────

def make_policy_fn(policy_type: str, **kwargs) -> Callable[[Dict[str, float]], float]:
    """Return a policy_fn callable for the given policy type.

    Supported policy_type values:
        'default'      : ParameterizedSSPolicy with DEFAULT_THETA
        'cmaes_seed42' : Phase 1 CMA-ES theta seed 42
        'uniform_robust': Phase 2.8 uniform robust theta
        'pernode_robust': Phase 2.8 per-node robust theta
        'grid_tuned'   : Phase 4.5 grid-tuned classical theta
        custom         : pass theta=np.ndarray to use a raw theta directly
    """
    from hybrid_policy import ParameterizedSSPolicy
    from multi_echelon import split_theta26, ACTION_MAP

    def _policy_from_theta8(theta8: np.ndarray) -> Callable:
        policy = ParameterizedSSPolicy(theta8)
        def fn(state):
            action_idx = policy.act(state)
            return float(ACTION_MAP[int(action_idx)])
        return fn

    if policy_type == 'default':
        return _policy_from_theta8(ParameterizedSSPolicy.DEFAULT_THETA.copy())

    if policy_type == 'custom':
        theta = kwargs.get('theta')
        if theta is None:
            raise ValueError("pass theta=np.ndarray for policy_type='custom'")
        theta = np.asarray(theta, dtype=np.float64)
        if theta.size == 8:
            return _policy_from_theta8(theta)
        # 26-vec: use store A theta (or a specified store)
        store = kwargs.get('store', 'A')
        parts = split_theta26(theta)
        return _policy_from_theta8(parts[store])

    # Named saved thetas
    name_map = {
        'cmaes_seed42':    'cmaes_theta_seed42.npy',
        'cmaes_seed123':   'cmaes_theta_seed123.npy',
        'cmaes_seed456':   'cmaes_theta_seed456.npy',
        'cmaes_seed789':   'cmaes_theta_seed789.npy',
        'cmaes_seed999':   'cmaes_theta_seed999.npy',
        'uniform_robust':  'uniform_best_theta_robust.npy',
        'pernode_robust':  'network_best_theta_pernode_robust.npy',
        'grid_tuned':      'grid_tuned_classical_theta.npy',
        'legacy_mean_sl':  'network_best_theta.npy',
    }
    if policy_type not in name_map:
        raise ValueError(
            f"Unknown policy_type '{policy_type}'. "
            f"Choose from: default, custom, {', '.join(name_map)}"
        )
    models_dir = kwargs.get('models_dir', MODELS_DIR)
    path = os.path.join(models_dir, name_map[policy_type])
    raw = np.load(path)
    # Normalise and extract store theta
    from analyze_segmentation import _to_theta26
    theta26 = _to_theta26(raw)
    parts = split_theta26(theta26)
    store = kwargs.get('store', 'A')
    return _policy_from_theta8(parts[store])


# ───────────────── batch sample explanations ─────────────────

def generate_sample_explanations(
    policy_type: str = 'pernode_robust',
    store: str = 'A',
    n_decisions: int = 10,
    models_dir: str = MODELS_DIR,
) -> List[Dict]:
    """Generate explanations for *n_decisions* sample states.

    States are constructed from the M5 held-out test portion to represent
    realistic operating conditions.
    """
    from multi_echelon import _load_m5_series, STORE_SHARE

    policy_fn = make_policy_fn(policy_type, store=store, models_dir=models_dir)
    full_series = _load_m5_series()
    split_idx   = int(len(full_series) * 0.8)
    test_series = full_series[split_idx:]

    rng = np.random.default_rng(42)
    explanations = []

    for i in range(n_decisions):
        # Sample a random starting day in the test window
        start = int(rng.integers(0, max(1, len(test_series) - 15)))
        window = test_series[start:start + 10]

        mean_d = float(np.mean(window)) * STORE_SHARE.get(store, 0.5)
        std_d  = float(np.std(window))  * STORE_SHARE.get(store, 0.5)
        inv_raw = float(rng.uniform(50, 500))

        state = {
            'inventory_level': float(np.clip(inv_raw / 1500.0, 0.0, 3.0)),
            'demand_forecast': float(np.clip(mean_d / 300.0, 0.0, 2.0)),
            'demand_std':      float(np.clip((mean_d / 300.0) * (std_d / (mean_d + 1e-6)), 0.0, 2.0)),
            'lead_time':       2.0,
        }

        explanation = explain_decision(policy_fn, state)
        explanation['sample_id']   = i
        explanation['policy_type'] = policy_type
        explanation['store']       = store
        explanation['day_offset']  = start
        explanations.append(explanation)

    return explanations


# ───────────────── CLI ─────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Policy decision explainer')
    parser.add_argument('--policy', default='pernode_robust',
                        help='Policy type (default: pernode_robust)')
    parser.add_argument('--store', default='A',
                        help='Store A, B, or C (default: A)')
    parser.add_argument('--n', type=int, default=10,
                        help='Number of sample explanations (default: 10)')
    parser.add_argument('--out', default=None,
                        help='Output JSON path (default: stdout)')
    args = parser.parse_args()

    print(f'Generating {args.n} explanations for policy={args.policy} store={args.store} ...')
    explanations = generate_sample_explanations(
        policy_type=args.policy,
        store=args.store,
        n_decisions=args.n,
    )

    out = json.dumps(explanations, indent=2)
    if args.out:
        with open(args.out, 'w') as f:
            f.write(out)
        print(f'Saved -> {args.out}')
    else:
        print(out)
