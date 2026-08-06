#!/usr/bin/env python3
"""
CMA-ES training for the multi-echelon SupplyChainNetwork.

Optimizes a combined 24-parameter vector (3 stores * 8 theta params).
The warehouse's fixed classical (s,S) policy is NOT optimized.

Matches existing CMA-ES hyperparameters from train_cmaes.py:
    popsize=16, sigma0=5.0, max_iter=100, early-stop after 35 gens
    without an improvement of at least $1.

CLI:
    python src/train_network_cmaes.py --seed 42
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

from multi_echelon import (                                          # noqa: E402
    SupplyChainNetwork,
    _load_m5_series,
    default_theta24,
    default_theta26,
    split_theta26,
)


MODELS_DIR          = 'models'
DEFAULT_EPISODES    = 32
DEFAULT_EP_LENGTH   = 90
EARLY_STOP_PATIENCE = 35
IMPROVE_EPS         = 1.0            # dollars

# Phase 2.8 dimensionality. The deployed theta is a 26-vec: 24 store
# parameters (8 per store × 3 stores) + 2 warehouse-policy parameters
# (raw controls for the (s, S) thresholds — see
# transform_warehouse_params in multi_echelon.py).
TOTAL_PARAMS        = 26
STORE_PARAMS        = 24
WAREHOUSE_PARAMS    = 2
UNIFORM_PARAMS      = 8 + WAREHOUSE_PARAMS   # 10-vec for uniform CMA-ES

# Robust-mode validation hyperparameters (Fix 3: train/val/test split).
# Every VAL_CHECK_EVERY generations we evaluate the running-best theta
# on the validation split; training early-stops if validation fitness
# fails to improve for VAL_PATIENCE consecutive checks. Standard ML
# protocol (Bishop 2006; Hastie et al. 2009).
VAL_CHECK_EVERY = 5
VAL_PATIENCE    = 15

# Service-level floor applied as a quadratic penalty on the fitness.
# Below SL_FLOOR the penalty grows fast enough that CMA-ES can never
# find a "do not order" regime attractive. Above the floor the fitness
# is just -raw_profit.
SL_FLOOR        = 0.80
SL_PENALTY_GAIN = 100_000.0 * 10.0   # 100k * 10 scaling as specified
SL_WARN_FLOOR   = 0.90               # held-out SL threshold for warning


# ───────────────── fitness ─────────────────

def _expand_theta(theta: np.ndarray, uniform: bool) -> np.ndarray:
    """Expand the CMA-ES decision vector to the 26-dim deployment vector.

    Phase 2.8 layout (26 params total):
      [0:24]  per-store policy thetas (A, B, C × 8)
      [24:26] raw warehouse controls (transformed via
              transform_warehouse_params)

    Uniform mode optimizes a 10-vec (one shared 8-vec store theta + 2
    warehouse controls), expanded by replicating the 8-vec to all three
    stores. Segmented mode optimizes the 26-vec directly.

    A 24-vec input (legacy theta24) is also accepted in segmented mode
    for back-compat with stored thetas; missing warehouse controls are
    padded with 0.0 (=> default warehouse regime s=400, S=1000).
    """
    theta = np.asarray(theta, dtype=np.float64)
    if uniform:
        if theta.size == UNIFORM_PARAMS:
            store_theta = theta[:8]
            wh_raw      = theta[8:10]
        elif theta.size == 8:
            # Legacy 8-vec uniform input: assume default warehouse regime.
            store_theta = theta
            wh_raw      = np.zeros(2, dtype=np.float64)
        else:
            raise ValueError(
                f"uniform mode expects {UNIFORM_PARAMS}-vec "
                f"(or legacy 8-vec), got {theta.size}"
            )
        return np.concatenate([store_theta, store_theta, store_theta, wh_raw])

    if theta.size == TOTAL_PARAMS:
        return theta
    if theta.size == STORE_PARAMS:
        # Legacy 24-vec input: pad with default warehouse controls.
        return np.concatenate([theta, np.zeros(2, dtype=np.float64)])
    raise ValueError(
        f"segmented mode expects {TOTAL_PARAMS}-vec "
        f"(or legacy {STORE_PARAMS}-vec), got {theta.size}"
    )


def evaluate_network(
    theta24: np.ndarray,
    demand_series: np.ndarray,
    n_episodes: int = DEFAULT_EPISODES,
    episode_length: int = DEFAULT_EP_LENGTH,
    seed: int = 42,
    return_detail: bool = False,
    uniform: bool = False,
    per_node_sl: bool = False,
):
    """Evaluate a 24-vector theta across n_episodes episodes.

    Returns a CMA-ES-ready fitness (to minimise). When ``return_detail``
    is True, also returns a dict with raw profit, service level, the
    applied penalty, AND per-store fill rates so the CMA-ES loop can
    inspect whether any single store is being abandoned.

    Service-level metric: **fill rate** (beta service level). For each
    store we report mean episode-level (total_sales / total_demand).
    The network-level ``mean_sl`` carried in the return dict is also
    a fill rate (the SupplyChainNetwork.summary() definition).

    Fitness construction:

        If ``per_node_sl`` is False (mean-floor, Phase 2 style):
            penalty = SL_PENALTY_GAIN * (SL_FLOOR - mean_network_SL) ** 2
                      if mean_network_SL < SL_FLOOR else 0
        If ``per_node_sl`` is True (Phase 2.6 per-node constraint):
            penalty = Σ_s SL_PENALTY_GAIN * (SL_FLOOR - SL_s) ** 2
                      over stores s with SL_s < SL_FLOOR
        In both cases:
            fitness = -(raw_profit - penalty)

    The per-node variant prevents CMA-ES from abandoning one store
    while meeting the floor on the network average — the Store-C
    reward-hack observed in Phase 2.

    ``uniform=True`` allows passing an 8-vec that is replicated to
    all 3 stores before evaluation.
    """
    theta26 = _expand_theta(theta24, uniform=uniform)
    parts   = split_theta26(theta26)
    thetas  = {'A': parts['A'], 'B': parts['B'], 'C': parts['C']}
    wh_s    = parts['warehouse_s']
    wh_S    = parts['warehouse_S']
    rng = np.random.default_rng(seed)
    L = len(demand_series)
    max_start = max(0, L - episode_length - 1)

    profits: List[float] = []
    sls: List[float] = []
    store_sl_per_ep: Dict[str, List[float]] = {'A': [], 'B': [], 'C': []}
    for ep in range(n_episodes):
        start_day = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        ep_seed = int(rng.integers(0, 2**31 - 1))
        net = SupplyChainNetwork(
            thetas=thetas,
            demand_series=demand_series,
            start_day=start_day,
            seed=ep_seed,
            warehouse_s_lower=wh_s,
            warehouse_s_upper=wh_S,
        )
        summary = net.simulate(n_steps=episode_length)
        profits.append(summary['network_total_profit'])
        sls.append(summary['service_level'])
        for sid in ('A', 'B', 'C'):
            d = net.stores[sid].metrics.demand_units
            s = net.stores[sid].metrics.sales_units
            store_sl_per_ep[sid].append((s / d) if d > 1e-9 else 1.0)

    raw_profit = float(np.mean(profits))
    mean_sl    = float(np.mean(sls))
    mean_store_sl = {sid: float(np.mean(store_sl_per_ep[sid]))
                     for sid in ('A', 'B', 'C')}

    if per_node_sl:
        penalty = 0.0
        for sid in ('A', 'B', 'C'):
            gap = SL_FLOOR - mean_store_sl[sid]
            if gap > 0:
                penalty += SL_PENALTY_GAIN * gap ** 2
    else:
        if mean_sl < SL_FLOOR:
            penalty = SL_PENALTY_GAIN * (SL_FLOOR - mean_sl) ** 2
        else:
            penalty = 0.0

    fitness = -(raw_profit - penalty)

    if return_detail:
        return fitness, {
            'raw_profit':    raw_profit,
            'mean_sl':       mean_sl,
            'mean_store_sl': mean_store_sl,
            'penalty':       penalty,
        }
    return fitness


# ───────────────── training driver ─────────────────

def run_cmaes(
    run_seed: int = 42,
    max_iter: int = 100,
    popsize: int = 16,
    sigma0: float = 5.0,
    n_eval_episodes: int = DEFAULT_EPISODES,
    episode_length: int = DEFAULT_EP_LENGTH,
    train_split: float = 0.8,
    data_path: str = 'data/processed/m5_clean.csv',
    scaler_path: Optional[str] = None,
    save: bool = True,
    verbose: bool = True,
    uniform: bool = False,
    per_node_sl: bool = False,
    robust: bool = False,
    val_check_every: int = VAL_CHECK_EVERY,
    val_patience: int = VAL_PATIENCE,
) -> Dict:
    import cma   # local import so module import doesn't fail if cma is missing

    random.seed(run_seed)
    np.random.seed(run_seed)

    full_series = _load_m5_series(data_path=data_path, scaler_path=scaler_path)
    L_full = len(full_series)
    if robust:
        # Fix 3: 60/20/20 split. Train drives CMA-ES fitness; validation
        # gates early-stopping; test is untouched until the very end.
        train_end    = int(L_full * 0.6)
        val_end      = int(L_full * 0.8)
        train_series = full_series[:train_end]
        val_series   = full_series[train_end:val_end]
        test_series  = full_series[val_end:]
    else:
        split_idx    = int(L_full * train_split)
        train_series = full_series[:split_idx]
        val_series   = None
        test_series  = full_series[split_idx:]

    if uniform:
        # Phase 2.8: shared 8-vec store policy + 2 warehouse raw controls.
        # Default store theta from DEFAULT_THETA; warehouse raw=(0,0)
        # which transforms to (s=400, S=1000) — leaner than legacy
        # fixed (1000, 3000).
        store_x0 = default_theta24().astype(np.float64)[:8]
        wh_x0    = np.zeros(2, dtype=np.float64)
        x0 = np.concatenate([store_x0, wh_x0])
    else:
        # Phase 2.8: full 26-vec (24 store + 2 warehouse).
        x0 = default_theta26().astype(np.float64)

    # Per-node mode tightens the feasible region (every store must
    # simultaneously clear SL_FLOOR). sigma0=5 puts almost every sample
    # outside that region when starting from the known-feasible default
    # theta, so CMA-ES never finds a running-best. Reduce sigma0 to 1.5
    # — standard practice when starting CMA-ES from a feasible
    # incumbent — and raise IMPROVE_EPS so we do not waste patience on
    # noise-level deltas.
    improve_eps_used = IMPROVE_EPS
    if per_node_sl:
        if sigma0 == 5.0:
            sigma0 = 1.5
            if verbose:
                print(f"  Per-node mode: reducing sigma0 from 5.0 to "
                      f"1.5 for stable exploration near feasible "
                      f"incumbent.")
        improve_eps_used = 100.0
        if verbose:
            print(f"  Per-node mode: IMPROVE_EPS raised to "
                  f"${improve_eps_used:.0f} to ignore noise-level deltas.")

    opts = {
        'maxiter':         max_iter,
        'popsize':         popsize,
        'seed':            run_seed,
        'tolx':            1e-4,
        'tolfun':          1e-6,
        'tolfunhist':      1e-6,
        # Default tolflatfitness=1 triggers after a single flat best
        # generation on noisy fitness landscapes. Use our own
        # EARLY_STOP_PATIENCE instead.
        'tolflatfitness':  EARLY_STOP_PATIENCE,
        'verbose':         -9,
    }

    es = cma.CMAEvolutionStrategy(x0, sigma0, opts)

    log: List[dict] = []
    val_history: List[dict] = []
    # Sentinel: no feasible generation yet. Real profits are bounded by
    # ~1e6 in this problem, so -1e12 is effectively -inf for comparison
    # but JSON-safe.
    best_profit      = -1e12
    best_theta       = x0.copy()
    no_improve_cnt   = 0
    # Robust-mode validation bookkeeping. val_best_fitness is the CMA-ES
    # fitness (to minimise) on the validation split for the best theta
    # seen so far at a validation check.
    val_best_fitness = float('inf')
    val_best_theta: Optional[np.ndarray] = None
    val_no_improve   = 0
    val_check_count  = 0
    gen              = 0
    t0               = time.time()

    sl_mode_label = 'per-node' if per_node_sl else 'mean'

    if verbose:
        print(f"\n{'='*104}")
        print(f"  Network CMA-ES  |  popsize={popsize}  |  sigma0={sigma0}  "
              f"|  seed={run_seed}  |  mode={'uniform' if uniform else 'segmented'}")
        print(f"  episodes/eval={n_eval_episodes}  episode_len={episode_length}  "
              f"train_days={len(train_series)}")
        if robust:
            assert val_series is not None
            print(f"  robust=True  val_days={len(val_series)}  "
                  f"test_days={len(test_series)}  "
                  f"val_check_every={val_check_every}  "
                  f"val_patience={val_patience}")
        print(f"  SL floor={SL_FLOOR:.0%}  penalty_gain={SL_PENALTY_GAIN:,.0f}  "
              f"floor_mode={sl_mode_label}")
        print(f"{'='*104}")
        print(f"  {'Gen':>4}  {'AdjFit':>12}  {'RawProfit':>12}  "
              f"{'SL':>6}  {'SL_A':>6}  {'SL_B':>6}  {'SL_C':>6}  "
              f"{'Penalty':>10}  {'RunBest':>12}  {'Sigma':>7}")
        print(f"  {'-'*92}")

    while not es.stop():
        solutions = es.ask()
        eval_seed = (gen * 17 + run_seed) % 100_000

        fits_and_info = [
            evaluate_network(
                theta24=np.asarray(theta),
                demand_series=train_series,
                n_episodes=n_eval_episodes,
                episode_length=episode_length,
                seed=eval_seed,
                return_detail=True,
                uniform=uniform,
                per_node_sl=per_node_sl,
            )
            for theta in solutions
        ]
        fitnesses = [fi[0] for fi in fits_and_info]
        infos     = [fi[1] for fi in fits_and_info]
        es.tell(solutions, fitnesses)

        best_idx        = int(np.argmin(fitnesses))
        adj_best_fit    = -float(fitnesses[best_idx])  # -(−(raw-penalty)) = raw-penalty
        gen_mean_fit    = -float(np.mean(fitnesses))
        gen_best_raw    = float(infos[best_idx]['raw_profit'])
        gen_best_sl     = float(infos[best_idx]['mean_sl'])
        gen_best_store_sl = dict(infos[best_idx]['mean_store_sl'])
        gen_best_pen    = float(infos[best_idx]['penalty'])
        sigma_now       = float(es.sigma)

        # Feasibility depends on which floor mode we are enforcing. In
        # per-node mode every store must clear the floor; in mean mode
        # the network average must clear it. We never let running_best
        # advance into an infeasible region.
        if per_node_sl:
            feasible = all(gen_best_store_sl[sid] >= SL_FLOOR
                           for sid in ('A', 'B', 'C'))
        else:
            feasible = (gen_best_sl >= SL_FLOOR)
        if feasible and gen_best_raw > best_profit + improve_eps_used:
            best_profit = gen_best_raw
            best_theta  = np.asarray(solutions[best_idx]).copy()
            no_improve_cnt = 0
        else:
            no_improve_cnt += 1

        log.append({
            'generation':          gen + 1,
            'adjusted_fitness':    adj_best_fit,
            'raw_profit':          gen_best_raw,
            'mean_service_level':  gen_best_sl,
            'store_service_level': gen_best_store_sl,
            'sl_penalty':          gen_best_pen,
            'mean_adj_fit':        gen_mean_fit,
            'sigma':               sigma_now,
            'running_best_profit': best_profit,
            'no_improve_count':    no_improve_cnt,
            'feasible':            feasible,
        })

        if verbose:
            print(f"  {gen+1:>4}  {adj_best_fit:>+12,.0f}  "
                  f"{gen_best_raw:>+12,.0f}  {gen_best_sl:>5.1%}  "
                  f"{gen_best_store_sl['A']:>5.1%}  "
                  f"{gen_best_store_sl['B']:>5.1%}  "
                  f"{gen_best_store_sl['C']:>5.1%}  "
                  f"{gen_best_pen:>10,.0f}  "
                  f"{best_profit if np.isfinite(best_profit) else 0:>+12,.0f}  "
                  f"{sigma_now:>7.4f}", flush=True)

        # Robust-mode validation check: every val_check_every generations,
        # re-evaluate the current running-best theta on the validation
        # split. We track val_best_theta independently of best_theta so
        # the deployed policy is the one that best generalized to val,
        # not the one that best fit train. Standard ML protocol.
        if robust and val_series is not None and (gen + 1) % val_check_every == 0:
            val_check_count += 1
            val_seed = 9001 + (gen * 31) % 100_000
            val_fit, val_info = evaluate_network(
                theta24=best_theta,
                demand_series=val_series,
                n_episodes=n_eval_episodes,
                episode_length=episode_length,
                seed=val_seed,
                return_detail=True,
                uniform=uniform,
                per_node_sl=per_node_sl,
            )
            improved_val = val_fit < val_best_fitness - improve_eps_used
            if improved_val:
                val_best_fitness = float(val_fit)
                val_best_theta   = best_theta.copy()
                val_no_improve   = 0
            else:
                val_no_improve += 1

            val_history.append({
                'generation':     gen + 1,
                'check_index':    val_check_count,
                'val_fitness':    float(val_fit),
                'val_profit':     float(val_info['raw_profit']),
                'val_mean_sl':    float(val_info['mean_sl']),
                'val_store_sl':   dict(val_info['mean_store_sl']),
                'val_penalty':    float(val_info['penalty']),
                'val_best_fit':   val_best_fitness,
                'no_improve':     val_no_improve,
                'improved':       improved_val,
            })

            if verbose:
                print(f"    VAL check {val_check_count:>3} "
                      f"(gen {gen+1:>3}): "
                      f"fit={val_fit:>+10,.0f}  "
                      f"profit={val_info['raw_profit']:>+10,.0f}  "
                      f"SL={val_info['mean_sl']:>5.1%} "
                      f"(A={val_info['mean_store_sl']['A']:.1%} "
                      f"B={val_info['mean_store_sl']['B']:.1%} "
                      f"C={val_info['mean_store_sl']['C']:.1%})  "
                      f"val_best={val_best_fitness:>+10,.0f}  "
                      f"no_imp={val_no_improve}/{val_patience}"
                      f"{'  **IMPROVED**' if improved_val else ''}",
                      flush=True)

            if val_no_improve >= val_patience:
                if verbose:
                    print(f"\n  Val early-stop: no val improvement over "
                          f"{val_patience} checks "
                          f"({val_check_every}×{val_patience}="
                          f"{val_check_every * val_patience} gens of slack).")
                break

        # Train-side patience only applies in non-robust mode; robust
        # mode stops on validation plateau, not training plateau.
        if not robust and no_improve_cnt >= EARLY_STOP_PATIENCE:
            if verbose:
                print(f"\n  Early stop: no improvement over "
                      f"{EARLY_STOP_PATIENCE} consecutive generations.")
            break

        gen += 1
        if gen >= max_iter:
            break

    elapsed = time.time() - t0

    # Deployed theta: in robust mode, use the theta that validated best
    # (val_best_theta). If we never saw a validation improvement we fall
    # back to the training-best. In non-robust mode deploy is train-best.
    if robust and val_best_theta is not None:
        deploy_theta  = val_best_theta
        deploy_source = 'val_best'
    else:
        deploy_theta  = best_theta
        deploy_source = 'train_best'

    # Determine stop reason for the result payload.
    if robust and val_no_improve >= val_patience:
        stop_reason = 'val_early'
    elif not robust and no_improve_cnt >= EARLY_STOP_PATIENCE:
        stop_reason = 'train_early'
    else:
        stop_reason = 'max_iter'

    mode = 'uniform' if uniform else 'segmented'
    # Phase 2.8: also expose the deploy theta in its 26-vec form so
    # downstream tools (analyze_segmentation, evaluators) get a single
    # canonical layout regardless of uniform/segmented training.
    deploy_theta26 = _expand_theta(deploy_theta, uniform=uniform)
    deploy_parts   = split_theta26(deploy_theta26)
    deploy_warehouse = {
        'raw_s':       float(deploy_theta26[24]),
        'raw_S':       float(deploy_theta26[25]),
        'warehouse_s': float(deploy_parts['warehouse_s']),
        'warehouse_S': float(deploy_parts['warehouse_S']),
    }
    result: Dict = {
        'run_seed':           run_seed,
        'mode':               mode,
        'sl_floor_mode':      'per-node' if per_node_sl else 'mean',
        'robust':             robust,
        'best_profit':        best_profit,
        'best_theta':         best_theta.tolist(),
        'val_best_fitness':   (val_best_fitness
                               if robust and np.isfinite(val_best_fitness)
                               else None),
        'val_best_theta':     (val_best_theta.tolist()
                               if val_best_theta is not None else None),
        'deploy_source':      deploy_source,
        'deploy_theta':       deploy_theta.tolist(),
        'deploy_theta26':     deploy_theta26.tolist(),
        'deploy_warehouse':   deploy_warehouse,
        'theta_dim_raw':      int(deploy_theta.size),
        'theta_dim_canonical': int(deploy_theta26.size),
        'generations':        len(log),
        'stopped':            stop_reason,
        'elapsed_seconds':    elapsed,
        'popsize':            popsize,
        'sigma0':             sigma0,
        'n_eval_episodes':    n_eval_episodes,
        'episode_length':     episode_length,
        'val_check_every':    val_check_every if robust else None,
        'val_patience':       val_patience    if robust else None,
        'log':                log,
        'val_history':        val_history,
    }

    if save:
        os.makedirs(MODELS_DIR, exist_ok=True)
        prefix = 'uniform' if uniform else 'network'
        suffix_parts: List[str] = []
        if per_node_sl:
            suffix_parts.append('pernode')
        if robust:
            suffix_parts.append('robust')
        suffix = ('_' + '_'.join(suffix_parts)) if suffix_parts else ''

        theta_path = os.path.join(MODELS_DIR, f'{prefix}_best_theta{suffix}.npy')
        log_path   = os.path.join(MODELS_DIR, f'{prefix}_training_log{suffix}.json')
        eval_path  = os.path.join(MODELS_DIR, f'{prefix}_evaluation{suffix}.json')

        # Save the deploy theta in its canonical 26-vec form (val-best in
        # robust mode, train-best otherwise). The raw-dim train-best is
        # preserved inside the JSON log under 'best_theta'. Keeping the
        # .npy at 26-dim means analyze_segmentation and other consumers
        # can load any of these files without knowing whether the run
        # was uniform or segmented.
        np.save(theta_path, deploy_theta26)
        with open(log_path, 'w') as f:
            json.dump(result, f, indent=2)

        # End-of-training evaluation on the held-out test split.
        eval_summary: Optional[Dict] = None
        if len(test_series) > episode_length + 1:
            eval_summary = _final_eval(deploy_theta, test_series,
                                       episode_length=episode_length,
                                       n_episodes=max(8, n_eval_episodes // 2),
                                       uniform=uniform,
                                       per_node_sl=per_node_sl)
            eval_summary['deploy_source'] = deploy_source
            # In robust mode, also record the val profit of deploy theta
            # so we can compute a clean generalization gap = val - test.
            if robust and val_series is not None:
                val_fit_deploy, val_info_deploy = evaluate_network(
                    theta24=deploy_theta,
                    demand_series=val_series,
                    n_episodes=max(8, n_eval_episodes // 2),
                    episode_length=episode_length,
                    seed=20260419,
                    return_detail=True,
                    uniform=uniform,
                    per_node_sl=per_node_sl,
                )
                eval_summary['val_profit_deploy']   = float(val_info_deploy['raw_profit'])
                eval_summary['val_sl_deploy']       = float(val_info_deploy['mean_sl'])
                eval_summary['val_store_sl_deploy'] = dict(val_info_deploy['mean_store_sl'])
                eval_summary['generalization_gap'] = (
                    float(val_info_deploy['raw_profit'])
                    - eval_summary['mean_network_profit']
                )
            eval_summary['deploy_warehouse'] = deploy_warehouse
            with open(eval_path, 'w') as f:
                json.dump(eval_summary, f, indent=2)

        if verbose:
            print(f"\n  Deploy source    -> {deploy_source}")
            print(f"  Deploy warehouse -> s={deploy_warehouse['warehouse_s']:.0f} "
                  f"S={deploy_warehouse['warehouse_S']:.0f}  "
                  f"(raw=({deploy_warehouse['raw_s']:+.3f}, "
                  f"{deploy_warehouse['raw_S']:+.3f}))")
            print(f"  Saved theta      -> {theta_path}")
            print(f"  Saved log        -> {log_path}")
            if eval_summary is not None:
                print(f"  Saved eval       -> {eval_path}")
                if robust and 'generalization_gap' in eval_summary:
                    print(f"  Val  profit (deploy) = "
                          f"{eval_summary['val_profit_deploy']:>+12,.0f}")
                    print(f"  Test profit (deploy) = "
                          f"{eval_summary['mean_network_profit']:>+12,.0f}")
                    print(f"  Generalization gap   = "
                          f"{eval_summary['generalization_gap']:>+12,.0f} "
                          f"(val − test)")

    return result


def _final_eval(theta24: np.ndarray,
                test_series: np.ndarray,
                episode_length: int = DEFAULT_EP_LENGTH,
                n_episodes: int = 16,
                uniform: bool = False,
                per_node_sl: bool = False) -> Dict:
    theta26 = _expand_theta(theta24, uniform=uniform)
    parts   = split_theta26(theta26)
    thetas  = {'A': parts['A'], 'B': parts['B'], 'C': parts['C']}
    wh_s    = parts['warehouse_s']
    wh_S    = parts['warehouse_S']
    rng = np.random.default_rng(20260419)
    L = len(test_series)
    max_start = max(0, L - episode_length - 1)

    profits, service = [], []
    store_profits = {'A': [], 'B': [], 'C': []}
    store_sl = {'A': [], 'B': [], 'C': []}
    for _ in range(n_episodes):
        start = int(rng.integers(0, max_start + 1)) if max_start > 0 else 0
        ep_seed = int(rng.integers(0, 2**31 - 1))
        net = SupplyChainNetwork(
            thetas=thetas,
            demand_series=test_series,
            start_day=start,
            seed=ep_seed,
            warehouse_s_lower=wh_s,
            warehouse_s_upper=wh_S,
        )
        s = net.simulate(n_steps=episode_length)
        profits.append(s['network_total_profit'])
        service.append(s['service_level'])
        for sid in ('A', 'B', 'C'):
            store_profits[sid].append(s['store_profits'][sid])
            d = net.stores[sid].metrics.demand_units
            sales = net.stores[sid].metrics.sales_units
            store_sl[sid].append((sales / d) if d > 1e-9 else 1.0)

    mean_sl = float(np.mean(service))
    mean_profit = float(np.mean(profits))
    mean_store_sl = {sid: float(np.mean(store_sl[sid]))
                     for sid in ('A', 'B', 'C')}

    warnings_list: List[str] = []
    # Network-level warning preserved for continuity with the Phase 2
    # reporting format.
    if mean_sl < SL_WARN_FLOOR:
        warnings_list.append(
            f"WARNING: held-out network fill rate {mean_sl:.1%} is below "
            f"the {SL_WARN_FLOOR:.0%} acceptable floor."
        )
    # Per-store floor check — this is the hard constraint we set out to
    # enforce under per-node mode, but we report it in both modes.
    abandoned = [sid for sid in ('A', 'B', 'C') if mean_store_sl[sid] < SL_FLOOR]
    if abandoned:
        warnings_list.append(
            f"WARNING: stores {abandoned} have held-out fill rate below "
            f"SL_FLOOR={SL_FLOOR:.0%}: "
            + ", ".join(f"{sid}={mean_store_sl[sid]:.1%}" for sid in abandoned)
            + ". Policy is abandoning these stores."
        )
    sl_warning = " | ".join(warnings_list) if warnings_list else None
    if sl_warning:
        print(f"\n  {sl_warning}")

    return {
        'n_episodes':           n_episodes,
        'episode_length':       episode_length,
        'mode':                 'uniform' if uniform else 'segmented',
        'sl_floor_mode':        'per-node' if per_node_sl else 'mean',
        'mean_network_profit':  mean_profit,
        'std_network_profit':   float(np.std(profits)),
        'mean_service_level':   mean_sl,
        'mean_store_service_level': mean_store_sl,
        'sl_warning':           sl_warning,
        'mean_store_profits': {sid: float(np.mean(v))
                               for sid, v in store_profits.items()},
        'best_theta':           theta24.tolist(),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-iter', type=int, default=100)
    parser.add_argument('--episodes', type=int, default=DEFAULT_EPISODES)
    parser.add_argument('--episode-length', type=int, default=DEFAULT_EP_LENGTH)
    parser.add_argument(
        '--uniform',
        action='store_true',
        help='Optimise one shared 8-vec across all 3 stores (MEIO baseline) '
             'instead of the segmented 24-vec.',
    )
    parser.add_argument(
        '--per-node-sl',
        action='store_true',
        help='Enforce the SL_FLOOR individually for each store (fill rate '
             'per node) rather than on the network mean. Fixes the Store-C '
             'reward hack observed under mean-floor training.',
    )
    parser.add_argument(
        '--robust',
        action='store_true',
        help='Robust training protocol: 60/20/20 train/val/test split, '
             'validation early-stopping (every 5 gens, patience 15 '
             'checks). Deploy the theta that validated best, not the '
             'theta that fit train best. Phase 2.7.',
    )
    args = parser.parse_args()
    run_cmaes(
        run_seed=args.seed,
        max_iter=args.max_iter,
        n_eval_episodes=args.episodes,
        episode_length=args.episode_length,
        uniform=args.uniform,
        per_node_sl=args.per_node_sl,
        robust=args.robust,
    )
    print("Training complete.")
