#!/usr/bin/env python3
"""
Phase 4 — Disruption Resilience Engine (production theta26 edition).

Stress-tests the locked Phase-2.8 production policy
(models/network_best_theta_pernode_robust.npy, a 26-vec per-node
+ joint warehouse (s, S) policy) against the classical (s, S)
default baseline under six disruption scenarios:

    1. calm              no disruption (control)
    2. mild_supplier     supplier->warehouse lead x1.5 for 3 days,
                         start day 20  (5 -> 8d)
    3. major_supplier    supplier->warehouse lead x3   for 14 days,
                         start day 10  (5 -> 15d)
    4. demand_spike      base demand x3                for 3 days,
                         start day 30
    5. port_strike       supplier->warehouse lead ~ Uniform{2..10}
                         independently resampled each disrupted day,
                         for 10 days, start day 15
    6. compound_crisis   major_supplier + demand_spike overlapping

Per (scenario, policy) cell we run 50 paired episodes (the same 50
(start_day, episode_seed) tuples are reused across every cell so
results are paired) on the held-out 20% of clean M5 demand, and
report:

    - mean_profit, std_profit
    - p5_worst   (5th percentile, the tail-risk metric)
    - mean_service_level
    - profit_drop_vs_calm_pct (vs the same policy's calm baseline)
    - recovery_days (days after disrupt_end until per-day SL returns
      to 95% of the same policy's calm daily-SL profile, averaged
      over episodes that recover within the episode window)

Outputs:
    data/disruption_results.json   (Phase 4 schema, see Step 3 PRD)

Run:
    python src/disruption_engine.py

The engine does NOT retrain anything — both policies are loaded from
disk and evaluated. Phase 2.8 production model is required at
models/network_best_theta_pernode_robust.npy; if missing the script
exits with a clear error.
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

from multi_echelon import (                                          # noqa: E402
    LEAD_SUPPLIER_TO_WAREHOUSE,
    SupplyChainNetwork,
    WAREHOUSE_S_LOWER,
    WAREHOUSE_S_UPPER,
    _load_m5_series,
    default_theta24,
    split_theta24,
    split_theta26,
)


# ───────────────── run knobs ─────────────────

MODELS_DIR         = 'models'
DATA_DIR           = 'data'
DATA_PATH          = 'data/processed/m5_clean.csv'
EPISODES_PER_CELL  = 50
EPISODE_LENGTH     = 90
TRAIN_SPLIT        = 0.8
BASE_SEED          = 42         # Phase 4 PRD: seed 42 for reproducibility

PRODUCTION_THETA_PATH = os.path.join(
    MODELS_DIR, 'network_best_theta_pernode_robust.npy'
)
GRID_TUNED_THETA_PATH = os.path.join(
    MODELS_DIR, 'grid_tuned_classical_theta.npy'
)


# ───────────────── policy abstraction ─────────────────

class Policy:
    """A simple bundle: per-store thetas + warehouse (s, S) thresholds.

    Both policies in Phase 4 are evaluated through the same code path
    via this struct. The default policy keeps the legacy fixed
    (1000, 3000) thresholds; the CMA-ES per-node robust policy uses
    the learned thresholds extracted from the 26-vec model."""

    def __init__(
        self,
        key: str,
        store_thetas: Dict[str, np.ndarray],
        warehouse_s: float,
        warehouse_S: float,
        source_path: Optional[str],
        description: str,
    ):
        self.key = key
        self.store_thetas = {sid: np.asarray(t, dtype=np.float64)
                             for sid, t in store_thetas.items()}
        self.warehouse_s = float(warehouse_s)
        self.warehouse_S = float(warehouse_S)
        self.source_path = source_path
        self.description = description


def _policy_from_theta26_file(
    path: str,
    key: str,
    description: str,
) -> Policy:
    """Load a 26-vec policy file (per-store thetas + raw warehouse
    params) and wrap it as a Policy. Used for both the CMA-ES per-node
    robust production model and the Phase-4.5 grid-tuned baseline."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Policy file not found: {path}")
    raw = np.load(path).astype(np.float64)
    if raw.shape != (26,):
        raise ValueError(
            f"{path} must be shape (26,), got {raw.shape}"
        )
    parts = split_theta26(raw)
    return Policy(
        key=key,
        store_thetas={'A': parts['A'], 'B': parts['B'], 'C': parts['C']},
        warehouse_s=parts['warehouse_s'],
        warehouse_S=parts['warehouse_S'],
        source_path=path,
        description=description,
    )


def load_policies() -> Dict[str, Policy]:
    """Load the three Phase-4.5 policies and return them keyed by name.

    Order in the returned dict drives report column order:
        default_classical    legacy classical baseline (mis-calibrated)
        grid_tuned_classical Phase-4.5 grid-search winner (calibrated)
        cmaes_pernode_robust Phase-2.8 production CMA-ES policy
    """
    default_thetas = split_theta24(default_theta24())
    default_policy = Policy(
        key='default_classical',
        store_thetas=default_thetas,
        warehouse_s=WAREHOUSE_S_LOWER,
        warehouse_S=WAREHOUSE_S_UPPER,
        source_path=None,
        description=('Classical (s, S) baseline: default per-store '
                     'theta + fixed warehouse (1000, 3000)'),
    )

    grid_policy = _policy_from_theta26_file(
        path=GRID_TUNED_THETA_PATH,
        key='grid_tuned_classical',
        description=('Grid-tuned classical (s, S) baseline (Phase 4.5):'
                     ' shared per-store theta, fixed warehouse, best '
                     'of 1,728 cells s.t. ≥80% per-store SL'),
    )

    cmaes_policy = _policy_from_theta26_file(
        path=PRODUCTION_THETA_PATH,
        key='cmaes_pernode_robust',
        description=('CMA-ES per-node robust theta26 (Phase 2.8 '
                     'production)'),
    )

    return {
        'default_classical':    default_policy,
        'grid_tuned_classical': grid_policy,
        'cmaes_pernode_robust': cmaes_policy,
    }


# ───────────────── scenario factories ─────────────────

def _make_window_lead_fn(
    start_day: int,
    duration: int,
    supplier_lead: int,
) -> Callable[[int], Dict]:
    """Disruption that overrides supplier->warehouse lead time to a
    *fixed* value during [start_day, start_day+duration)."""
    end = start_day + duration
    base = int(LEAD_SUPPLIER_TO_WAREHOUSE)
    def fn(day: int) -> Dict:
        if start_day <= day < end:
            return {'supplier_lead': supplier_lead}
        return {'supplier_lead': base}
    return fn


def _make_random_lead_fn(
    start_day: int,
    duration: int,
    lead_low: int,
    lead_high: int,
    seed: int,
) -> Callable[[int], Dict]:
    """Disruption that replaces supplier lead time with an integer drawn
    uniformly from {lead_low..lead_high} on each disrupted day.

    Uses an independent RNG so re-running the same scenario seed is
    deterministic. Caches per-day draws so if the same day is queried
    twice in one episode we return the same value (important for the
    _current_overrides cache in SupplyChainNetwork)."""
    end = start_day + duration
    base = int(LEAD_SUPPLIER_TO_WAREHOUSE)
    rng = np.random.default_rng(int(seed))
    cache: Dict[int, int] = {}
    def fn(day: int) -> Dict:
        if start_day <= day < end:
            if day not in cache:
                cache[day] = int(rng.integers(lead_low, lead_high + 1))
            return {'supplier_lead': cache[day]}
        return {'supplier_lead': base}
    return fn


def _make_demand_spike_fn(
    start_day: int,
    duration: int,
    multiplier: float,
) -> Callable[[int], Dict]:
    end = start_day + duration
    def fn(day: int) -> Dict:
        if start_day <= day < end:
            return {'demand_multiplier': float(multiplier)}
        return {}
    return fn


def _make_compound_fn(
    supplier_start: int,
    supplier_duration: int,
    supplier_lead: int,
    demand_start: int,
    demand_duration: int,
    demand_multiplier: float,
) -> Callable[[int], Dict]:
    supplier_end = supplier_start + supplier_duration
    demand_end = demand_start + demand_duration
    base = int(LEAD_SUPPLIER_TO_WAREHOUSE)
    def fn(day: int) -> Dict:
        out: Dict = {}
        if supplier_start <= day < supplier_end:
            out['supplier_lead'] = int(supplier_lead)
        else:
            out['supplier_lead'] = base
        if demand_start <= day < demand_end:
            out['demand_multiplier'] = float(demand_multiplier)
        return out
    return fn


def build_scenarios(scenario_seed: int) -> Dict[str, Dict]:
    """Return scenario_name -> { factory, disrupt_start, disrupt_end,
    description }. `factory()` returns a fresh disruption_fn for each
    episode (so the port_strike RNG state resets)."""
    base_lead = int(LEAD_SUPPLIER_TO_WAREHOUSE)
    mild_lead = int(np.ceil(base_lead * 1.5))        # 5 -> 8
    major_lead = int(base_lead * 3)                   # 5 -> 15

    scenarios: Dict[str, Dict] = {
        'calm': {
            'factory':       lambda: None,
            'disrupt_start': None,
            'disrupt_end':   None,
            'description':   'no disruption (control)',
        },
        'mild_supplier': {
            'factory':       lambda: _make_window_lead_fn(
                start_day=20, duration=3, supplier_lead=mild_lead,
            ),
            'disrupt_start': 20,
            'disrupt_end':   23,
            'description':   (f'supplier lead x1.5 ({base_lead}->'
                              f'{mild_lead}d) for 3 days, days 20-22'),
        },
        'major_supplier': {
            'factory':       lambda: _make_window_lead_fn(
                start_day=10, duration=14, supplier_lead=major_lead,
            ),
            'disrupt_start': 10,
            'disrupt_end':   24,
            'description':   (f'supplier lead x3 ({base_lead}->'
                              f'{major_lead}d) for 14 days, days 10-23'),
        },
        'demand_spike': {
            'factory':       lambda: _make_demand_spike_fn(
                start_day=30, duration=3, multiplier=3.0,
            ),
            'disrupt_start': 30,
            'disrupt_end':   33,
            'description':   'base demand x3 for 3 days, days 30-32',
        },
        'port_strike': {
            'factory':       lambda seed=scenario_seed: _make_random_lead_fn(
                start_day=15, duration=10, lead_low=2, lead_high=10,
                seed=seed,
            ),
            'disrupt_start': 15,
            'disrupt_end':   25,
            'description':   ('supplier lead ~ Uniform{2..10} '
                              'independently each day, days 15-24'),
        },
        'compound_crisis': {
            'factory':       lambda: _make_compound_fn(
                supplier_start=10, supplier_duration=14,
                supplier_lead=major_lead,
                demand_start=30, demand_duration=3,
                demand_multiplier=3.0,
            ),
            'disrupt_start': 10,
            'disrupt_end':   33,
            'description':   ('major_supplier (days 10-23) + demand_spike '
                              '(days 30-32) overlapping'),
        },
    }
    return scenarios


# ───────────────── single-episode runner ─────────────────

def _run_episode(
    policy: Policy,
    demand_series: np.ndarray,
    start_day: int,
    seed: int,
    disruption_fn: Optional[Callable[[int], Dict]],
    episode_length: int = EPISODE_LENGTH,
) -> Dict:
    """Run one episode and return {profit, service_level, daily_sl}.

    `daily_sl` is per-day sales/demand across the whole network; on a
    zero-demand day we carry the previous day's value forward (first
    such day defaults to 1.0)."""
    net = SupplyChainNetwork(
        thetas=policy.store_thetas,
        demand_series=demand_series,
        start_day=start_day,
        seed=seed,
        disruption_fn=disruption_fn,
        warehouse_s_lower=policy.warehouse_s,
        warehouse_s_upper=policy.warehouse_S,
    )
    summary = net.simulate(n_steps=episode_length)

    daily_sl: List[float] = []
    prev = 1.0
    for rec in net.daily_records:
        demand = sum(rec['store_demand'].values())
        sales  = sum(rec['store_sales'].values())
        if demand > 1e-9:
            sl_today = sales / demand
        else:
            sl_today = prev
        daily_sl.append(sl_today)
        prev = sl_today

    return {
        'profit':        float(summary['network_total_profit']),
        'service_level': float(summary['service_level']),
        'daily_sl':      daily_sl,
    }


# ───────────────── aggregation ─────────────────

def _drop_pct(calm: float, scen: float) -> float:
    """Positive = worse than calm. Uses |calm| as denominator so a
    further loss past a negative-profit baseline shows up positive."""
    denom = abs(calm) if abs(calm) > 1e-9 else 1.0
    return 100.0 * (calm - scen) / denom


def _aggregate(
    policy_key: str,
    scenario_key: str,
    disrupt_start: Optional[int],
    disrupt_end: Optional[int],
    episodes: List[Dict],
    calm_mean_profit: float,
    calm_daily_sl_mean: Optional[np.ndarray],
) -> Dict:
    profits = np.array([e['profit']        for e in episodes])
    sls     = np.array([e['service_level'] for e in episodes])
    daily   = np.array([e['daily_sl']      for e in episodes])  # (ep, day)

    mean_daily_sl = daily.mean(axis=0)

    # Recovery target = 95% of the same policy's calm daily-SL profile.
    # For the calm scenario itself we fall back to a flat 0.95 target.
    if calm_daily_sl_mean is not None:
        target_profile = 0.95 * calm_daily_sl_mean
    else:
        target_profile = np.full_like(mean_daily_sl, 0.95)

    if disrupt_end is None:
        recovery_days: float = float('nan')
    else:
        recs: List[float] = []
        for row in daily:
            for i in range(disrupt_end, len(row)):
                if row[i] >= target_profile[i]:
                    recs.append(float(i - disrupt_end))
                    break
            else:
                recs.append(float('nan'))
        finite = [r for r in recs if not np.isnan(r)]
        recovery_days = float(np.mean(finite)) if finite else float('nan')

    mean_profit = float(profits.mean())
    return {
        'policy':                   policy_key,
        'scenario':                 scenario_key,
        'disrupt_start':            disrupt_start,
        'disrupt_end':              disrupt_end,
        'n_episodes':               len(episodes),
        'mean_profit':              mean_profit,
        'std_profit':               float(profits.std()),
        'p5_worst':                 float(np.percentile(profits, 5)),
        'mean_service_level':       float(sls.mean()),
        'profit_drop_vs_calm_pct':  _drop_pct(calm_mean_profit, mean_profit),
        'recovery_days':            recovery_days,
        'mean_daily_sl':            mean_daily_sl.tolist(),
    }


# ───────────────── main sweep ─────────────────

def main():
    t0 = time.time()
    print("Phase 4.5 — Disruption Resilience Engine "
          "(default vs grid-tuned vs CMA-ES)")
    print("="*88)
    full_series = _load_m5_series()
    split_idx = int(len(full_series) * TRAIN_SPLIT)
    test_series = full_series[split_idx:]
    if len(test_series) <= EPISODE_LENGTH + 1:
        raise RuntimeError(
            f"Held-out split too short: {len(test_series)} days for "
            f"episode_length={EPISODE_LENGTH}"
        )
    print(f"  data:          {DATA_PATH}")
    print(f"  held-out days: {len(test_series)}  "
          f"(train_split={TRAIN_SPLIT})")
    print(f"  episodes/cell: {EPISODES_PER_CELL}   "
          f"episode length: {EPISODE_LENGTH}")

    policies = load_policies()
    print(f"  policies:      {list(policies.keys())}")
    for k, p in policies.items():
        print(f"    - {k:<22}  s={p.warehouse_s:.1f}  "
              f"S={p.warehouse_S:.1f}  src={p.source_path or '(builtin)'}")

    scenarios = build_scenarios(scenario_seed=BASE_SEED)
    print(f"  scenarios:     {list(scenarios.keys())}")
    for name, s in scenarios.items():
        print(f"    - {name:<17}  {s['description']}")

    # Pre-draw 50 (start_day, env_seed) tuples once. ALL (policy,
    # scenario) cells reuse this list so episodes are paired across
    # cells.
    master_rng = np.random.default_rng(BASE_SEED)
    max_start = max(0, len(test_series) - EPISODE_LENGTH - 1)
    episode_plan: List[Dict] = []
    for _ in range(EPISODES_PER_CELL):
        start = int(master_rng.integers(0, max_start + 1)) if max_start > 0 else 0
        seed  = int(master_rng.integers(0, 2**31 - 1))
        episode_plan.append({'start_day': start, 'seed': seed})

    # First pass: calm baseline per policy. We need the calm mean profit
    # for drop_pct and the calm daily-SL profile for recovery thresholds.
    results: Dict[str, Dict[str, Dict]] = {k: {} for k in policies}
    calm_daily_means: Dict[str, np.ndarray] = {}
    calm_mean_profits: Dict[str, float] = {}

    print(f"\n  Running calm baselines first...")
    for policy_key, policy in policies.items():
        eps: List[Dict] = []
        for plan in episode_plan:
            eps.append(_run_episode(
                policy=policy,
                demand_series=test_series,
                start_day=plan['start_day'],
                seed=plan['seed'],
                disruption_fn=None,
                episode_length=EPISODE_LENGTH,
            ))
        daily = np.array([e['daily_sl'] for e in eps])
        calm_daily_means[policy_key] = daily.mean(axis=0)
        profits_arr = np.array([e['profit'] for e in eps])
        calm_mean_profits[policy_key] = float(profits_arr.mean())
        results[policy_key]['calm'] = _aggregate(
            policy_key=policy_key,
            scenario_key='calm',
            disrupt_start=None,
            disrupt_end=None,
            episodes=eps,
            calm_mean_profit=calm_mean_profits[policy_key],
            calm_daily_sl_mean=None,
        )
        r = results[policy_key]['calm']
        print(f"    calm / {policy_key:<22}  profit="
              f"{r['mean_profit']:>+12,.0f}  p5={r['p5_worst']:>+12,.0f}  "
              f"SL={r['mean_service_level']:.1%}")

    # Second pass: every other scenario, for both policies.
    for scen_key, scen in scenarios.items():
        if scen_key == 'calm':
            continue
        print(f"\n  Scenario: {scen_key}  ({scen['description']})")
        for policy_key, policy in policies.items():
            eps: List[Dict] = []
            for plan in episode_plan:
                eps.append(_run_episode(
                    policy=policy,
                    demand_series=test_series,
                    start_day=plan['start_day'],
                    seed=plan['seed'],
                    disruption_fn=scen['factory'](),
                    episode_length=EPISODE_LENGTH,
                ))
            results[policy_key][scen_key] = _aggregate(
                policy_key=policy_key,
                scenario_key=scen_key,
                disrupt_start=scen['disrupt_start'],
                disrupt_end=scen['disrupt_end'],
                episodes=eps,
                calm_mean_profit=calm_mean_profits[policy_key],
                calm_daily_sl_mean=calm_daily_means[policy_key],
            )
            r = results[policy_key][scen_key]
            rec = r['recovery_days']
            rec_str = f"{rec:.1f}d" if not np.isnan(rec) else "  n/a"
            print(f"    {policy_key:<22}  profit="
                  f"{r['mean_profit']:>+12,.0f}  drop="
                  f"{r['profit_drop_vs_calm_pct']:>+6.1f}%  "
                  f"p5={r['p5_worst']:>+12,.0f}  "
                  f"SL={r['mean_service_level']:.1%}  recovery={rec_str}")

    _print_headline_table(results, list(scenarios.keys()),
                          list(policies.keys()))

    elapsed = time.time() - t0

    # ── persist (Phase 4 schema) ──
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, 'disruption_results.json')
    payload = {
        'policies': {
            policy_key: {
                'description':  policies[policy_key].description,
                'warehouse_s':  policies[policy_key].warehouse_s,
                'warehouse_S':  policies[policy_key].warehouse_S,
                'source_path':  policies[policy_key].source_path,
                'scenarios':    {
                    scen_key: {
                        'mean_profit':              r['mean_profit'],
                        'std_profit':               r['std_profit'],
                        'p5_worst':                 r['p5_worst'],
                        'mean_service_level':       r['mean_service_level'],
                        'profit_drop_vs_calm_pct':  r['profit_drop_vs_calm_pct'],
                        'recovery_days':            r['recovery_days'],
                        'disrupt_start':            r['disrupt_start'],
                        'disrupt_end':              r['disrupt_end'],
                        'n_episodes':               r['n_episodes'],
                        'mean_daily_sl':            r['mean_daily_sl'],
                    }
                    for scen_key, r in results[policy_key].items()
                },
            }
            for policy_key in policies
        },
        'metadata': {
            'n_episodes_per_scenario': EPISODES_PER_CELL,
            'episode_length_days':     EPISODE_LENGTH,
            'train_split':             TRAIN_SPLIT,
            'base_seed':               BASE_SEED,
            'data_source':             DATA_PATH,
            'policy_paths': {
                policy_key: policies[policy_key].source_path
                for policy_key in policies
            },
            'scenario_descriptions':   {k: v['description']
                                        for k, v in scenarios.items()},
            'timestamp':               datetime.now(timezone.utc).isoformat(),
            'wall_clock_seconds':      elapsed,
        },
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=_json_safe)
    print(f"\n  Saved disruption results -> {out_path}")
    print(f"  Phase 4 wall-clock: {elapsed:.1f}s")


def _json_safe(x):
    if isinstance(x, np.floating):
        return float(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, float) and np.isnan(x):
        return None
    raise TypeError(f"not JSON-serializable: {type(x)}")


def _print_headline_table(
    results: Dict[str, Dict[str, Dict]],
    scen_order: List[str],
    policy_order: List[str],
) -> None:
    print()
    print("="*120)
    print(f"  Headline: {EPISODES_PER_CELL} paired episodes × "
          f"{EPISODE_LENGTH} days, held-out clean M5 demand")
    print("="*120)
    hdr = (f"  {'Scenario':<18} "
           f"{'Policy':<22} "
           f"{'MeanProfit':>14} "
           f"{'Drop%':>8} "
           f"{'P5Profit':>14} "
           f"{'SL':>7} "
           f"{'Recovery':>10}")
    print(hdr)
    print("  " + "-"*(len(hdr)-2))
    for scen in scen_order:
        for policy in policy_order:
            r = results[policy][scen]
            rec = r['recovery_days']
            rec_str = f"{rec:.1f}d" if not np.isnan(rec) else "   n/a"
            print(f"  {scen:<18} "
                  f"{policy:<22} "
                  f"{r['mean_profit']:>+14,.0f} "
                  f"{r['profit_drop_vs_calm_pct']:>+7.1f}% "
                  f"{r['p5_worst']:>+14,.0f} "
                  f"{r['mean_service_level']:>6.1%} "
                  f"{rec_str:>10}")
        print("  " + "-"*(len(hdr)-2))
    print("  Drop% is vs the same policy's calm baseline; positive "
          "means worse than calm.")
    print("  Recovery = days after disruption end until daily SL "
          "returns to 95% of the calm daily-SL profile,")
    print("             averaged across episodes that recover within "
          "the episode window ('n/a' if none did).")


if __name__ == '__main__':
    main()
