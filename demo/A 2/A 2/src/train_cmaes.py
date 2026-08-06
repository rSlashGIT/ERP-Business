#!/usr/bin/env python3
"""
CMA-ES training for ParameterizedSSPolicy.

The policy's act() output is interpreted as an action index [0,7].
Features passed to act() are the normalized tactical state from
get_tactical_state() (inventory [0,3], demand [0,2], etc.).
CMA-ES optimizes theta = [s_base, S_base, w1..w6] to maximise
mean episode profit on the training split.
"""

import argparse
import json
import os
import random
import sys
import warnings

import numpy as np
import cma

warnings.filterwarnings('ignore')

sys.path.append('src')

from hybrid_policy import ParameterizedSSPolicy
from environment import SupplyChainEnv

DATA_PATH      = 'data/processed/m5_clean.csv'
SCALER_PATH    = None  # ignored in clean mode
MODELS_DIR     = 'models'
EPISODE_LENGTH = 90
STRATEGIC_MODE = 1


def make_train_env():
    return SupplyChainEnv(
        enable_hierarchical=True,
        data_path=DATA_PATH,
        data_split=(0.0, 0.8),
    )


def run_episode(policy, env):
    env.reset()
    env.set_strategic_mode(STRATEGIC_MODE)
    revenue = cost = demand = sales = 0.0

    for _ in range(EPISODE_LENGTH):
        ts = env.get_tactical_state()

        # Normalized features — consistent with what act() was designed for.
        # ts[0]: inventory/1500 (0-3), ts[1]: demand/300 (0-2),
        # ts[2]: demand_mean/300 (0-2), ts[3]: CV (0-2).
        state = {
            'inventory_level': float(ts[0]),
            'demand_forecast':  float(ts[1]),
            'demand_std':       float(ts[2]) * float(ts[3]),   # approx CV*mean
            'lead_time':        2.0,
        }

        action_idx = policy.act(state)   # already in [0, 7]
        _, _, done, info = env.step(action_idx)
        revenue += info['revenue']
        cost    += info['cost']
        demand  += info['demand']
        sales   += info['sales']
        if done:
            break

    sl = (sales / demand * 100.0) if demand > 0 else 0.0
    return {'profit': revenue - cost, 'sl': sl}


def evaluate_policy(theta, n_episodes=64, seed=42):
    random.seed(seed)
    np.random.seed(seed)
    env    = make_train_env()
    policy = ParameterizedSSPolicy(theta)
    profits = [run_episode(policy, env)['profit'] for _ in range(n_episodes)]
    return -float(np.mean(profits))   # CMA-ES minimises


def train(run_seed=42):
    os.makedirs(MODELS_DIR, exist_ok=True)

    seed_path = os.path.join(MODELS_DIR, f'cmaes_theta_seed{run_seed}.npy')
    if os.path.exists(seed_path):
        print(f"[seed {run_seed}] checkpoint exists at {seed_path} — skipping training.")
        theta = np.load(seed_path)
        return theta, None

    x0    = ParameterizedSSPolicy.DEFAULT_THETA.copy()
    sigma = 5.0

    opts = {
        'maxiter':  100,
        'popsize':  16,
        'seed':     run_seed,
        'tolx':     1e-4,
        'tolfun':   1e-4,
        'verbose':  -9,   # suppress cma internal output
    }

    es = cma.CMAEvolutionStrategy(x0, sigma, opts)

    log            = []
    best_profit    = -float('inf')
    best_theta     = x0.copy()
    no_improve_cnt = 0
    gen            = 0

    print(f"\n{'='*62}")
    print(f"  CMA-ES Training  |  popsize=16  |  sigma0={sigma}  |  seed={run_seed}")
    print(f"{'='*62}")
    print(f"  {'Gen':>4}  {'BestProfit':>12}  {'MeanProfit':>12}  {'Sigma':>9}")
    print(f"  {'-'*48}")

    while not es.stop():
        solutions = es.ask()
        eval_seed = (gen * 17 + run_seed) % 10_000
        fitnesses = [evaluate_policy(theta, n_episodes=64, seed=eval_seed)
                     for theta in solutions]
        es.tell(solutions, fitnesses)

        gen_best_profit = -float(min(fitnesses))
        gen_mean_profit = -float(np.mean(fitnesses))
        sigma_now       = float(es.sigma)

        print(f"  {gen+1:>4}  {gen_best_profit:>+12,.0f}  {gen_mean_profit:>+12,.0f}  {sigma_now:>9.4f}",
              flush=True)

        log.append({
            'generation':  gen + 1,
            'best_profit': gen_best_profit,
            'mean_profit': gen_mean_profit,
            'sigma':       sigma_now,
        })

        if gen_best_profit > best_profit + 1.0:
            best_profit    = gen_best_profit
            best_theta     = solutions[int(np.argmin(fitnesses))].copy()
            no_improve_cnt = 0
        else:
            no_improve_cnt += 1

        if no_improve_cnt >= 35:
            print(f"\n  Early stop: no improvement over 35 consecutive generations.")
            break

        gen += 1

    np.save(seed_path, best_theta)
    log_path = os.path.join(MODELS_DIR, f'cmaes_training_log_seed{run_seed}.json')
    with open(log_path, 'w') as f:
        json.dump({
            'run_seed':     run_seed,
            'best_profit':  best_profit,
            'best_theta':   best_theta.tolist(),
            'generations':  len(log),
            'stopped':      'early' if no_improve_cnt >= 35 else 'max_iter',
            'log':          log,
        }, f, indent=2)

    print(f"\n{'='*62}")
    print(f"  Best profit : ${best_profit:+,.0f}")
    print(f"  Best theta  : {np.round(best_theta, 3)}")
    print(f"  Generations: {len(log)}  (stopped: "
          f"{'early' if no_improve_cnt >= 35 else 'max_iter'})")
    print(f"  Saved: {seed_path}")
    print(f"         {log_path}")
    print(f"{'='*62}")

    return best_theta, best_profit


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-seed', '--seed', dest='run_seed', type=int, default=42)
    args = parser.parse_args()
    train(run_seed=args.run_seed)
    print("Training complete. Best theta saved.")
