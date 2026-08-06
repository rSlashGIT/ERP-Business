#!/usr/bin/env python3
"""
Phase 7 — CMA-ES training for the 30-SKU joint network.

Optimises a flat (n_skus * 10)-vector. Each SKU contributes:
    [0:8]  ParameterizedSSPolicy 8-vec (replicated across stores A/B/C)
    [8:10] raw warehouse-(s, S) controls (transformed to actual thresholds)

The SL constraint is per-(SKU, store) at 80% fill rate, applied as a
quadratic penalty. CMA-ES uses diagonal-only updates for the first 100
generations (memory-efficient at 300 dims), then full-covariance.

Training protocol matches Phase 2.7 robust mode:
    60 / 20 / 20 train / val / test split on the per-SKU demand series
    Validation early-stopping every val_check_every gens, val_patience=15
    Deploy theta = the one that validated best (not train-best)

CLI:
    python3 src/train_multi_sku.py --seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import warnings
from typing import Dict, List, Optional

import numpy as np

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from multi_sku_network import (                                       # noqa: E402
    MAX_WAREHOUSE_CAPACITY_DEFAULT,
    OVERFLOW_PENALTY,
    PARAMS_PER_SKU,
    SL_FLOOR,
    SL_PENALTY_GAIN,
    STORE_IDS,
    build_network,
    default_multi_theta,
    load_multi_sku_data,
    split_multi_theta,
)


MODELS_DIR = 'models'

DEFAULT_EPISODES   = 16
DEFAULT_EP_LENGTH  = 90
DEFAULT_POPSIZE    = 30
DEFAULT_MAX_ITER   = 200
DEFAULT_SIGMA0     = 0.5
DEFAULT_DIAGONAL   = 100  # diagonal-only updates for first N generations

VAL_CHECK_EVERY    = 5
VAL_PATIENCE       = 15
IMPROVE_EPS        = 100.0   # raise vs single-SKU; multi-SKU profits are larger


def _slice_demand(demand_by_sku: Dict[str, np.ndarray],
                  start_frac: float, end_frac: float) -> Dict[str, np.ndarray]:
    """Return a per-SKU demand dict sliced to [start, end) of each series."""
    out: Dict[str, np.ndarray] = {}
    for sid, d in demand_by_sku.items():
        L = len(d)
        i0 = int(L * start_frac)
        i1 = int(L * end_frac)
        if i1 - i0 < 200:
            raise RuntimeError(
                f"SKU {sid} slice [{i0},{i1}) too short ({i1 - i0} days)"
            )
        out[sid] = d[i0:i1]
    return out


def _evaluate(
    theta: np.ndarray,
    multi_data: Dict,
    demand_slice: Dict[str, np.ndarray],
    n_episodes: int,
    episode_length: int,
    seed: int,
    max_warehouse_capacity: float,
    return_detail: bool = False,
):
    """Evaluate a flat (n_skus*10) theta on a demand slice.

    Returns CMA-ES fitness (to minimise). ``return_detail`` adds a dict
    with raw_profit, mean_sl, per-(sku, store) SL grid, and penalty.
    """
    sku_ids = multi_data['sku_ids']
    n_skus = len(sku_ids)
    rng = np.random.default_rng(seed)

    # We swap the demand_by_sku in multi_data with the slice for eval.
    sub_data = dict(multi_data)
    sub_data['demand_by_sku'] = demand_slice

    # Episode start day: pick uniform within slice, ensuring episode
    # fits inside the slice for all SKUs.
    min_len = min(len(d) for d in demand_slice.values())
    max_start = max(0, min_len - episode_length - 1)

    profits: List[float] = []
    sls: List[float] = []
    overflow_costs: List[float] = []
    # per-(sku, store) SL averaged across episodes
    sku_store_sl_acc: Dict[str, Dict[str, List[float]]] = {
        sid: {st: [] for st in STORE_IDS} for sid in sku_ids
    }
    sku_profits_acc: Dict[str, List[float]] = {sid: [] for sid in sku_ids}

    for _ in range(n_episodes):
        start_day = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        ep_seed = int(rng.integers(0, 2**31 - 1))
        net = build_network(
            sub_data, theta, start_day=start_day, seed=ep_seed,
            max_warehouse_capacity=max_warehouse_capacity,
            overflow_penalty=OVERFLOW_PENALTY,
        )
        summary = net.simulate(n_steps=episode_length)
        profits.append(summary['network_total_profit'])
        sls.append(summary['service_level'])
        overflow_costs.append(summary['overflow_cost_total'])
        for sid in sku_ids:
            sku_summary = summary['per_sku'][sid]
            sku_profits_acc[sid].append(sku_summary['sku_total_profit'])
            for st in STORE_IDS:
                sku_store_sl_acc[sid][st].append(
                    sku_summary['store_service_level'][st]
                )

    raw_profit = float(np.mean(profits))
    mean_sl = float(np.mean(sls))
    mean_overflow = float(np.mean(overflow_costs))

    # Per-(sku, store) SL grid (mean across episodes).
    sl_grid: Dict[str, Dict[str, float]] = {
        sid: {st: float(np.mean(sku_store_sl_acc[sid][st]))
              for st in STORE_IDS}
        for sid in sku_ids
    }

    # Penalty: sum quadratic penalty over every (sku, store) pair below
    # SL_FLOOR.
    penalty = 0.0
    n_violations = 0
    worst_gap = 0.0
    for sid in sku_ids:
        for st in STORE_IDS:
            gap = SL_FLOOR - sl_grid[sid][st]
            if gap > 0:
                penalty += SL_PENALTY_GAIN * gap ** 2
                n_violations += 1
                if gap > worst_gap:
                    worst_gap = gap

    fitness = -(raw_profit - penalty)

    if return_detail:
        return fitness, {
            'raw_profit':      raw_profit,
            'mean_sl':         mean_sl,
            'mean_overflow':   mean_overflow,
            'penalty':         penalty,
            'n_violations':    n_violations,
            'worst_gap':       worst_gap,
            'sl_grid':         sl_grid,
            'sku_mean_profit': {sid: float(np.mean(v))
                                for sid, v in sku_profits_acc.items()},
        }
    return fitness


def run_cmaes(
    run_seed: int = 42,
    max_iter: int = DEFAULT_MAX_ITER,
    popsize: int = DEFAULT_POPSIZE,
    sigma0: float = DEFAULT_SIGMA0,
    n_eval_episodes: int = DEFAULT_EPISODES,
    episode_length: int = DEFAULT_EP_LENGTH,
    diagonal_iters: int = DEFAULT_DIAGONAL,
    max_warehouse_capacity: float = MAX_WAREHOUSE_CAPACITY_DEFAULT,
    val_check_every: int = VAL_CHECK_EVERY,
    val_patience: int = VAL_PATIENCE,
    save: bool = True,
    verbose: bool = True,
) -> Dict:
    import cma   # local import so module import doesn't fail without cma

    random.seed(run_seed)
    np.random.seed(run_seed)

    multi_data = load_multi_sku_data()
    n_skus = len(multi_data['sku_ids'])

    # 60 / 20 / 20 split per SKU.
    train_demand = _slice_demand(multi_data['demand_by_sku'], 0.0, 0.6)
    val_demand   = _slice_demand(multi_data['demand_by_sku'], 0.6, 0.8)
    test_demand  = _slice_demand(multi_data['demand_by_sku'], 0.8, 1.0)

    x0 = default_multi_theta(n_skus).astype(np.float64)
    dim = x0.size
    assert dim == n_skus * PARAMS_PER_SKU

    opts = {
        'maxiter':        max_iter,
        'popsize':        popsize,
        'seed':           run_seed,
        'CMA_active':     True,
        'CMA_diagonal':   diagonal_iters,
        'tolx':           1e-4,
        'tolfun':         1e-3,
        'tolfunhist':     1e-3,
        # Don't let CMA-ES self-stop on a single flat-fitness gen — our
        # validation patience handles plateaus.
        'tolflatfitness': max(20, val_patience * val_check_every),
        'verbose':        -9,
    }

    es = cma.CMAEvolutionStrategy(x0, sigma0, opts)

    log: List[dict] = []
    val_history: List[dict] = []
    best_profit = -1e15
    best_theta = x0.copy()
    train_no_improve = 0
    val_best_fitness = float('inf')
    val_best_theta: Optional[np.ndarray] = None
    val_no_improve = 0
    val_check_count = 0
    gen = 0
    t0 = time.time()

    if verbose:
        print(f"\n{'='*92}")
        print(f"  Multi-SKU CMA-ES  |  popsize={popsize}  |  sigma0={sigma0}  "
              f"|  seed={run_seed}")
        print(f"  n_skus={n_skus}  theta_dim={dim}  episodes/eval={n_eval_episodes}  "
              f"episode_len={episode_length}")
        print(f"  diagonal-only updates for first {diagonal_iters} gens, "
              f"max_iter={max_iter}")
        print(f"  warehouse_capacity={max_warehouse_capacity:,.0f}  "
              f"SL_floor={SL_FLOOR:.0%}  per-(sku,store)")
        print(f"  train/val/test days per SKU = "
              f"{len(next(iter(train_demand.values())))} / "
              f"{len(next(iter(val_demand.values())))} / "
              f"{len(next(iter(test_demand.values())))}")
        print(f"  val_check_every={val_check_every}  val_patience={val_patience}")
        print(f"{'='*92}")
        print(f"  {'Gen':>4}  {'AdjFit':>14}  {'RawProfit':>14}  "
              f"{'Overflow':>10}  {'SL':>6}  {'Viols':>6}  "
              f"{'WorstGap':>8}  {'RunBest':>14}  {'Sigma':>7}")
        print(f"  {'-'*98}")

    while not es.stop():
        solutions = es.ask()
        eval_seed = (gen * 17 + run_seed) % 100_000

        fits_and_info = [
            _evaluate(
                theta=np.asarray(theta),
                multi_data=multi_data,
                demand_slice=train_demand,
                n_episodes=n_eval_episodes,
                episode_length=episode_length,
                seed=eval_seed,
                max_warehouse_capacity=max_warehouse_capacity,
                return_detail=True,
            )
            for theta in solutions
        ]
        fitnesses = [fi[0] for fi in fits_and_info]
        infos = [fi[1] for fi in fits_and_info]
        es.tell(solutions, fitnesses)

        best_idx = int(np.argmin(fitnesses))
        adj_best_fit = -float(fitnesses[best_idx])
        gen_best_raw = float(infos[best_idx]['raw_profit'])
        gen_best_sl  = float(infos[best_idx]['mean_sl'])
        gen_best_overflow = float(infos[best_idx]['mean_overflow'])
        gen_best_viols = int(infos[best_idx]['n_violations'])
        gen_best_gap   = float(infos[best_idx]['worst_gap'])
        sigma_now = float(es.sigma)

        # Feasibility: every (sku, store) must clear the floor.
        feasible = (gen_best_viols == 0)
        if feasible and gen_best_raw > best_profit + IMPROVE_EPS:
            best_profit = gen_best_raw
            best_theta = np.asarray(solutions[best_idx]).copy()
            train_no_improve = 0
        else:
            train_no_improve += 1

        log.append({
            'generation':         gen + 1,
            'adjusted_fitness':   adj_best_fit,
            'raw_profit':         gen_best_raw,
            'mean_service_level': gen_best_sl,
            'mean_overflow_cost': gen_best_overflow,
            'n_sl_violations':    gen_best_viols,
            'worst_sl_gap':       gen_best_gap,
            'sigma':              sigma_now,
            'running_best_profit': best_profit,
            'feasible':           feasible,
        })

        if verbose:
            running = best_profit if best_profit > -1e14 else 0.0
            print(f"  {gen+1:>4}  {adj_best_fit:>+14,.0f}  "
                  f"{gen_best_raw:>+14,.0f}  "
                  f"{gen_best_overflow:>10,.0f}  {gen_best_sl:>5.1%}  "
                  f"{gen_best_viols:>6}  {gen_best_gap:>8.1%}  "
                  f"{running:>+14,.0f}  {sigma_now:>7.4f}", flush=True)

        # Validation check.
        if (gen + 1) % val_check_every == 0:
            val_check_count += 1
            val_seed = 9001 + (gen * 31) % 100_000
            val_fit, val_info = _evaluate(
                theta=best_theta,
                multi_data=multi_data,
                demand_slice=val_demand,
                n_episodes=n_eval_episodes,
                episode_length=episode_length,
                seed=val_seed,
                max_warehouse_capacity=max_warehouse_capacity,
                return_detail=True,
            )
            improved = val_fit < val_best_fitness - IMPROVE_EPS
            if improved:
                val_best_fitness = float(val_fit)
                val_best_theta = best_theta.copy()
                val_no_improve = 0
            else:
                val_no_improve += 1

            val_history.append({
                'generation':     gen + 1,
                'check_index':    val_check_count,
                'val_fitness':    float(val_fit),
                'val_profit':     float(val_info['raw_profit']),
                'val_mean_sl':    float(val_info['mean_sl']),
                'val_violations': int(val_info['n_violations']),
                'val_worst_gap':  float(val_info['worst_gap']),
                'val_best_fit':   val_best_fitness,
                'no_improve':     val_no_improve,
                'improved':       improved,
            })

            if verbose:
                marker = '  **IMPROVED**' if improved else ''
                print(f"    VAL check {val_check_count:>3} (gen {gen+1:>3}): "
                      f"fit={val_fit:>+12,.0f}  "
                      f"profit={val_info['raw_profit']:>+12,.0f}  "
                      f"SL={val_info['mean_sl']:>5.1%}  "
                      f"viols={val_info['n_violations']:>3}  "
                      f"val_best={val_best_fitness:>+12,.0f}  "
                      f"no_imp={val_no_improve}/{val_patience}{marker}",
                      flush=True)

            if val_no_improve >= val_patience:
                if verbose:
                    print(f"\n  Val early-stop: no val improvement over "
                          f"{val_patience} checks "
                          f"({val_check_every}×{val_patience}="
                          f"{val_check_every * val_patience} gens of slack).")
                break

        gen += 1
        if gen >= max_iter:
            break

    elapsed = time.time() - t0

    if val_best_theta is not None:
        deploy_theta = val_best_theta
        deploy_source = 'val_best'
    else:
        deploy_theta = best_theta
        deploy_source = 'train_best'

    if val_no_improve >= val_patience and val_best_theta is not None:
        stop_reason = 'val_early'
    elif gen >= max_iter:
        stop_reason = 'max_iter'
    else:
        stop_reason = 'cma_internal'

    # Final test evaluation on held-out slice.
    test_fit, test_info = _evaluate(
        theta=deploy_theta,
        multi_data=multi_data,
        demand_slice=test_demand,
        n_episodes=max(8, n_eval_episodes // 2),
        episode_length=episode_length,
        seed=20260502,
        max_warehouse_capacity=max_warehouse_capacity,
        return_detail=True,
    )

    val_fit_deploy, val_info_deploy = _evaluate(
        theta=deploy_theta,
        multi_data=multi_data,
        demand_slice=val_demand,
        n_episodes=max(8, n_eval_episodes // 2),
        episode_length=episode_length,
        seed=20260503,
        max_warehouse_capacity=max_warehouse_capacity,
        return_detail=True,
    )

    result: Dict = {
        'phase':              'phase7_multi_sku',
        'run_seed':           run_seed,
        'n_skus':             n_skus,
        'theta_dim':          int(deploy_theta.size),
        'best_profit':        best_profit,
        'best_theta':         best_theta.tolist(),
        'val_best_fitness':   val_best_fitness if np.isfinite(val_best_fitness) else None,
        'val_best_theta':     (val_best_theta.tolist() if val_best_theta is not None else None),
        'deploy_source':      deploy_source,
        'deploy_theta':       deploy_theta.tolist(),
        'generations':        len(log),
        'stopped':            stop_reason,
        'elapsed_seconds':    elapsed,
        'popsize':            popsize,
        'sigma0':             sigma0,
        'diagonal_iters':     diagonal_iters,
        'n_eval_episodes':    n_eval_episodes,
        'episode_length':     episode_length,
        'val_check_every':    val_check_every,
        'val_patience':       val_patience,
        'max_warehouse_capacity': max_warehouse_capacity,
        'log':                log,
        'val_history':        val_history,
        'final_eval': {
            'val_profit_deploy':  float(val_info_deploy['raw_profit']),
            'val_mean_sl_deploy': float(val_info_deploy['mean_sl']),
            'val_violations_deploy': int(val_info_deploy['n_violations']),
            'val_sl_grid_deploy': val_info_deploy['sl_grid'],
            'test_profit_deploy': float(test_info['raw_profit']),
            'test_mean_sl_deploy': float(test_info['mean_sl']),
            'test_violations_deploy': int(test_info['n_violations']),
            'test_sl_grid_deploy': test_info['sl_grid'],
            'test_sku_mean_profit': test_info['sku_mean_profit'],
            'generalization_gap': (float(val_info_deploy['raw_profit'])
                                   - float(test_info['raw_profit'])),
        },
    }

    if save:
        os.makedirs(MODELS_DIR, exist_ok=True)
        theta_path = os.path.join(MODELS_DIR, 'multi_sku_theta.npy')
        log_path = os.path.join(MODELS_DIR, 'multi_sku_training_log.json')
        eval_path = os.path.join(MODELS_DIR, 'multi_sku_evaluation.json')
        np.save(theta_path, deploy_theta)
        with open(log_path, 'w') as f:
            json.dump(result, f, indent=2)
        with open(eval_path, 'w') as f:
            json.dump({
                'phase':                  'phase7_multi_sku',
                'n_skus':                 n_skus,
                'deploy_source':          deploy_source,
                'val_profit':             result['final_eval']['val_profit_deploy'],
                'val_mean_sl':            result['final_eval']['val_mean_sl_deploy'],
                'val_violations':         result['final_eval']['val_violations_deploy'],
                'val_sl_grid':            result['final_eval']['val_sl_grid_deploy'],
                'test_profit':            result['final_eval']['test_profit_deploy'],
                'test_mean_sl':           result['final_eval']['test_mean_sl_deploy'],
                'test_violations':        result['final_eval']['test_violations_deploy'],
                'test_sl_grid':           result['final_eval']['test_sl_grid_deploy'],
                'test_sku_mean_profit':   result['final_eval']['test_sku_mean_profit'],
                'generalization_gap':     result['final_eval']['generalization_gap'],
                'elapsed_seconds':        elapsed,
                'generations':            len(log),
                'stopped':                stop_reason,
                'episode_length':         episode_length,
                'popsize':                popsize,
                'sigma0':                 sigma0,
                'max_warehouse_capacity': max_warehouse_capacity,
            }, f, indent=2)
        if verbose:
            print(f"\n  Saved theta -> {theta_path}")
            print(f"  Saved log   -> {log_path}")
            print(f"  Saved eval  -> {eval_path}")
            print(f"\n  Val  profit (deploy) = ${result['final_eval']['val_profit_deploy']:>+,.0f}")
            print(f"  Test profit (deploy) = ${result['final_eval']['test_profit_deploy']:>+,.0f}")
            print(f"  Generalization gap   = ${result['final_eval']['generalization_gap']:>+,.0f} (val − test)")
            print(f"  Test SL              = {result['final_eval']['test_mean_sl_deploy']:.1%}")
            print(f"  Test violations      = {result['final_eval']['test_violations_deploy']}/{n_skus * 3}")

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-iter', type=int, default=DEFAULT_MAX_ITER)
    parser.add_argument('--popsize', type=int, default=DEFAULT_POPSIZE)
    parser.add_argument('--sigma0', type=float, default=DEFAULT_SIGMA0)
    parser.add_argument('--episodes', type=int, default=DEFAULT_EPISODES)
    parser.add_argument('--episode-length', type=int, default=DEFAULT_EP_LENGTH)
    parser.add_argument('--diagonal-iters', type=int, default=DEFAULT_DIAGONAL)
    parser.add_argument('--num-skus', type=int, default=30,
                        help='Sanity check: must match the number of SKUs in '
                             'data/processed/m5_multi_sku_summary.json. The '
                             'data file is the source of truth; this flag is '
                             'a guard against running with mismatched data.')
    parser.add_argument('--max-warehouse-capacity', type=float,
                        default=MAX_WAREHOUSE_CAPACITY_DEFAULT)
    args = parser.parse_args()

    multi_data = load_multi_sku_data()
    if len(multi_data['sku_ids']) != args.num_skus:
        raise SystemExit(
            f"--num-skus={args.num_skus} but the data file holds "
            f"{len(multi_data['sku_ids'])} SKUs. Re-run "
            f"scripts/prepare_multi_sku_m5.py if you want to change the "
            f"SKU set."
        )

    run_cmaes(
        run_seed=args.seed,
        max_iter=args.max_iter,
        popsize=args.popsize,
        sigma0=args.sigma0,
        n_eval_episodes=args.episodes,
        episode_length=args.episode_length,
        diagonal_iters=args.diagonal_iters,
        max_warehouse_capacity=args.max_warehouse_capacity,
    )
    print("Multi-SKU training complete.")
