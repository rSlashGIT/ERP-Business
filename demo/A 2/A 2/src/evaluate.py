#!/usr/bin/env python3
"""
Honest holdout evaluation: CMA-ES ParameterizedSSPolicy vs (s,S) baseline.

- 5 independent CMA-ES training runs (seeds 42, 123, 456, 789, 999)
- 200 paired holdout episodes per run (identical env resets/demand streams)
- Baseline: (s,S) grid scan on holdout, best pair reported
- Prints all 5 seeds — no cherry-picking, no filtering
"""

import argparse
import json
import os
import random
import sys
import warnings

import numpy as np

sys.path.append('src')

from hybrid_policy import ParameterizedSSPolicy
from environment import SupplyChainEnv
from train_cmaes import train as train_cmaes_for_seed

warnings.filterwarnings('ignore')

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


DATA_PATH      = 'data/processed/m5_clean.csv'
SCALER_PATH    = None  # ignored in clean mode
MODELS_DIR     = 'models'
EPISODE_LENGTH = 90
STRATEGIC_MODE = 1

CMAES_SEEDS = [42, 123, 456, 789, 999]

# Historical Deep RL numbers (honest, 3-seed holdout from earlier experiment)
HISTORICAL_DEEP_RL = {
    'mean_profit': 7384,
    'std':         8500,
    'worst':      -3138,
    'svc_lvl':     79.5,
}


def make_holdout_env():
    return SupplyChainEnv(
        enable_hierarchical=True,
        data_path=DATA_PATH,
        scaler_path=SCALER_PATH,
        data_split=(0.8, 1.0),
    )


def set_all_seeds(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    if HAS_TORCH:
        torch.manual_seed(seed)


def _state_from_env(env) -> dict:
    ts = env.get_tactical_state()
    return {
        'inventory_level': float(ts[0]),
        'demand_forecast':  float(ts[1]),
        'demand_std':       float(ts[2]) * float(ts[3]),
        'lead_time':        2.0,
    }


def run_single_episode(policy, env) -> dict:
    env.reset()
    env.set_strategic_mode(STRATEGIC_MODE)
    revenue = cost = demand = sales = 0.0
    holding = stockout_cost = 0.0
    stockout_steps = 0
    steps = 0

    for _ in range(EPISODE_LENGTH):
        action_idx = policy.act(_state_from_env(env))   # already in [0, 7]
        _, _, done, info = env.step(action_idx)

        revenue       += info['revenue']
        cost          += info['cost']
        demand        += info['demand']
        sales         += info['sales']
        holding       += info['holding_cost']
        stockout_cost += info['stockout_penalty']
        if (info['demand'] - info['sales']) > 1e-9:
            stockout_steps += 1
        steps += 1
        if done:
            break

    sl            = (sales / demand * 100.0) if demand > 0 else 0.0
    stockout_rate = (stockout_steps / steps)     if steps  > 0 else 0.0
    return {
        'profit':         revenue - cost,
        'service_level':  sl,
        'stockout_rate':  stockout_rate,
        'holding_cost':   holding,
        'stockout_cost':  stockout_cost,
    }


def _summarize(rows):
    profits = np.array([r['profit']        for r in rows])
    sls     = np.array([r['service_level'] for r in rows])
    sors    = np.array([r['stockout_rate'] for r in rows])
    holds   = np.array([r['holding_cost']  for r in rows])
    stks    = np.array([r['stockout_cost'] for r in rows])
    return {
        'profit_mean': float(profits.mean()),
        'profit_std':  float(profits.std(ddof=1)) if len(profits) > 1 else 0.0,
        'profit_min':  float(profits.min()),
        'profit_max':  float(profits.max()),
        'sl_mean':     float(sls.mean()),
        'sl_min':      float(sls.min()),
        'stockout_rate_mean':  float(sors.mean()),
        'holding_cost_mean':   float(holds.mean()),
        'stockout_cost_mean':  float(stks.mean()),
        'n_episodes':  len(rows),
    }


def evaluate_on_holdout(theta, n_episodes=200, eval_seed=0) -> dict:
    """Evaluate a ParameterizedSSPolicy (theta) on paired holdout episodes."""
    policy = ParameterizedSSPolicy(theta)
    env    = make_holdout_env()
    rows   = []
    for k in range(n_episodes):
        set_all_seeds(eval_seed + k)    # paired per-episode seed
        rows.append(run_single_episode(policy, env))
    return _summarize(rows)


def evaluate_ss_baseline(s, S, n_episodes=200, eval_seed=0) -> dict:
    """Classical (s,S) = ParameterizedSSPolicy with all context weights = 0."""
    theta = np.array([s, S, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
    return evaluate_on_holdout(theta, n_episodes, eval_seed)


def scan_ss_baseline(n_episodes, eval_seed):
    print("\n[Baseline scan] evaluating (s,S) grid on holdout…")
    s_values = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]   # normalized inv thresholds
    S_values = [3, 4, 5, 6, 7]                   # action-index targets
    grid = [(s, S) for s in s_values for S in S_values]
    results = []
    for s, S in grid:
        r = evaluate_ss_baseline(s, S, n_episodes, eval_seed)
        print(f"  (s={s:>3.1f}, S={S})  profit=${r['profit_mean']:+10,.0f}  "
              f"SL={r['sl_mean']:5.1f}%  std=${r['profit_std']:6,.0f}")
        results.append({'s': s, 'S': S, **r})
    best = max(results, key=lambda r: r['profit_mean'])
    print(f"  → Best: (s={best['s']}, S={best['S']})  "
          f"profit=${best['profit_mean']:+,.0f}  SL={best['sl_mean']:.1f}%")
    return best, results


def load_or_train_cmaes(seed) -> np.ndarray:
    path = os.path.join(MODELS_DIR, f'cmaes_theta_seed{seed}.npy')
    if os.path.exists(path):
        print(f"  [seed {seed}] loading {path}")
        return np.load(path)
    print(f"  [seed {seed}] no checkpoint — running CMA-ES training…")
    theta, _ = train_cmaes_for_seed(run_seed=seed)
    np.save(path, theta)
    return theta


def main(n_episodes, eval_seed):
    os.makedirs(MODELS_DIR, exist_ok=True)

    print("=" * 70)
    print(f"  HONEST EVALUATION — 5 CMA-ES runs × {n_episodes} holdout eps")
    print("=" * 70)

    # 1. Load/train 5 CMA-ES policies
    print("\n[1/3] CMA-ES checkpoints")
    thetas = {seed: load_or_train_cmaes(seed) for seed in CMAES_SEEDS}

    # 2. Baseline scan
    best_ss, ss_grid = scan_ss_baseline(n_episodes, eval_seed)

    # 3. CMA-ES holdout eval (paired with baseline)
    print(f"\n[3/3] CMA-ES holdout eval — {n_episodes} episodes, eval_seed={eval_seed}")
    cmaes_results = {}
    for seed in CMAES_SEEDS:
        r = evaluate_on_holdout(thetas[seed], n_episodes, eval_seed)
        cmaes_results[seed] = r
        print(f"  [seed {seed}]  profit=${r['profit_mean']:+10,.0f}  "
              f"SL={r['sl_mean']:5.1f}%  min=${r['profit_min']:+9,.0f}  "
              f"std=${r['profit_std']:6,.0f}")

    seed_means = np.array([cmaes_results[s]['profit_mean'] for s in CMAES_SEEDS])
    seed_sls   = np.array([cmaes_results[s]['sl_mean']     for s in CMAES_SEEDS])

    mean_of_means = float(seed_means.mean())
    std_of_means  = float(seed_means.std(ddof=1)) if len(seed_means) > 1 else 0.0
    worst_seed    = float(seed_means.min())
    best_seed_p   = float(seed_means.max())
    best_seed_idx = CMAES_SEEDS[int(np.argmax(seed_means))]
    min_sl        = float(seed_sls.min())
    mean_sl       = float(seed_sls.mean())

    # ── RESULTS TABLE ──────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("HONEST EVALUATION RESULTS")
    print("=" * 64)
    print(f"{'Method':<22}| {'Mean Profit':>12} | {'Std':>7} | "
          f"{'Worst':>9} | {'Svc Lvl':>7}")
    print("-" * 64)
    print(f"{'(s,S) baseline':<22}| ${best_ss['profit_mean']:>+10,.0f} | "
          f"${best_ss['profit_std']:>5,.0f} | ${best_ss['profit_min']:>+7,.0f} | "
          f"{best_ss['sl_mean']:>6.1f}%")
    dr = HISTORICAL_DEEP_RL
    print(f"{'Deep RL (old)':<22}| ${dr['mean_profit']:>+10,.0f} | "
          f"${dr['std']:>5,.0f} | ${dr['worst']:>+7,.0f} | {dr['svc_lvl']:>6.1f}%")

    best_seed_sl = cmaes_results[best_seed_idx]['sl_mean']
    print(f"{'CMA-ES (best seed)':<22}| ${best_seed_p:>+10,.0f} | "
          f"{'—':>6} | {'—':>8} | {best_seed_sl:>6.1f}%")
    print(f"{'CMA-ES (5 seeds)':<22}| ${mean_of_means:>+10,.0f} | "
          f"${std_of_means:>5,.0f} | ${worst_seed:>+7,.0f} | {mean_sl:>6.1f}%")
    print("=" * 64)

    # ── ACCEPTANCE CRITERIA ────────────────────────────────────────
    ss_baseline = best_ss['profit_mean']
    zero_sub85  = all(cmaes_results[s]['sl_mean'] >= 85.0 for s in CMAES_SEEDS)
    criteria = {
        'Worst-seed SL > 90%':            min_sl > 90.0,
        'Std across seeds < $3,000':      std_of_means < 3000.0,
        'Mean profit >= (s,S) baseline':  mean_of_means >= ss_baseline,
        'Zero seeds with SL < 85%':       zero_sub85,
    }
    print("\nACCEPTANCE CRITERIA")
    for name, ok in criteria.items():
        print(f"  {name}: {'YES' if ok else 'NO'}")

    failed = [k for k, v in criteria.items() if not v]
    if failed:
        print()
        for f in failed:
            print(f"CRITERION FAILED: {f}. Do not report only passing seeds.")

    # ── VERDICT ────────────────────────────────────────────────────
    delta = mean_of_means - ss_baseline
    if not criteria['Mean profit >= (s,S) baseline']:
        verdict = (f"CMA-ES did NOT beat (s,S) baseline (Δ=${delta:+,.0f}). "
                   f"Classical policy remains the preferred choice.")
    elif not criteria['Worst-seed SL > 90%']:
        verdict = (f"CMA-ES beats baseline in $ (Δ=${delta:+,.0f}) but fails "
                   f"reliability (min SL {min_sl:.1f}%).")
    elif not criteria['Std across seeds < $3,000']:
        verdict = (f"CMA-ES beats baseline (Δ=${delta:+,.0f}) but is unstable "
                   f"(σ=${std_of_means:,.0f} across seeds).")
    else:
        verdict = (f"CMA-ES passes all criteria. Δ vs (s,S): ${delta:+,.0f}, "
                   f"σ_seed=${std_of_means:,.0f}, worst-seed SL={min_sl:.1f}%.")
    print(f"\nVerdict: {verdict}")

    # ── SAVE ───────────────────────────────────────────────────────
    out = {
        'n_episodes_per_run':  n_episodes,
        'eval_seed':           eval_seed,
        'cmaes_seeds':         CMAES_SEEDS,
        'baseline_grid':       ss_grid,
        'baseline_best':       best_ss,
        'cmaes_per_seed':      {str(s): cmaes_results[s] for s in CMAES_SEEDS},
        'cmaes_aggregate': {
            'mean_of_means':  mean_of_means,
            'std_of_means':   std_of_means,
            'worst_seed':     worst_seed,
            'best_seed':      best_seed_p,
            'min_sl':         min_sl,
            'mean_sl':        mean_sl,
        },
        'historical_deep_rl': HISTORICAL_DEEP_RL,
        'criteria':           {k: bool(v) for k, v in criteria.items()},
        'verdict':            verdict,
    }
    out_path = os.path.join(MODELS_DIR, 'evaluation_results.json')
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=float)
    print(f"\nSaved: {out_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--episodes', type=int, default=200,
                        help='holdout episodes per policy')
    parser.add_argument('--eval-seed', type=int, default=0,
                        help='paired seed base for all episodes')
    args = parser.parse_args()
    main(args.episodes, args.eval_seed)
