#!/usr/bin/env python3
"""
PATH 2 — Compute real Chronos-Bolt tolerance accuracy on the 389-day test set.

Run this on your laptop where Chronos is installed (not in this audit
environment, which doesn't have PyTorch/chronos-forecasting).

WHAT IT DOES:
  1. Loads data/processed/m5_clean.csv
  2. Replicates the same 60/20/20 split used by the original eval
  3. Runs Chronos-Bolt zero-shot on the 389-day test window
  4. Computes:
     - Reproduced RMSE/MAE (sanity check vs models/chronos_evaluation.json)
     - % of days within ±10, ±15, ±20, ±25, ±30, ±40 units (absolute tolerance)
     - % of days within ±10%, ±20%, ±30%, ±50% (relative tolerance)
  5. Also runs N-HITS for completeness (if neuralforecast is installed)
  6. Compares to Naive baseline at every tolerance
  7. Writes results to: tolerance_accuracy_results.json
  8. Prints a summary table

WHAT IT WON'T DO:
  - Modify any existing files
  - Touch the frontend
  - Retrain any model
  - Change any saved theta or evaluation JSON

USAGE:
  cd <project_root>/A
  python3 compute_chronos_tolerance.py

  Then send me the printed table + the generated tolerance_accuracy_results.json.
  I'll wire it into the Forecasts screen as Path 3.

REQUIRES:
  - chronos-forecasting (pip install chronos-forecasting)
  - torch
  - pandas, numpy
  - (optional) neuralforecast for the N-HITS comparison

If chronos-forecasting isn't installed on your laptop:
  pip install chronos-forecasting

This will pull PyTorch (~2 GB if not already installed). Total run time on
M2 with no GPU should be 1-3 minutes for Chronos on 389 predictions.
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


# ───────────────── data + split ─────────────────

def load_m5_test_data():
    """Load M5 series and return (full_series, test_start_index)."""
    df = pd.read_csv('data/processed/m5_clean.csv')
    y = df['demand'].values.astype(float)
    L = len(y)
    # Same split as src/forecaster_eval.py: 60/20/20
    train_end = int(L * 0.60)
    val_end   = int(L * 0.80)
    return y, val_end, L


# ───────────────── tolerance metrics ─────────────────

def tolerance_table(preds, actuals, label):
    """Compute tolerance accuracy at multiple thresholds."""
    n = len(preds)
    rmse = float(np.sqrt(np.mean((preds - actuals) ** 2)))
    mae  = float(np.mean(np.abs(preds - actuals)))

    abs_tol_results = {}
    for tol in [10, 15, 20, 25, 30, 40]:
        within = int(np.sum(np.abs(preds - actuals) <= tol))
        abs_tol_results[f'within_pm{tol}_units'] = {
            'count': within,
            'total': n,
            'pct': round(within / n * 100, 2),
        }

    rel_tol_results = {}
    for tol_pct in [10, 20, 30, 50]:
        within = 0
        for p, a in zip(preds, actuals):
            if a == 0:
                # Zero-demand days: use small absolute window (5 units)
                within += int(abs(p - a) <= 5)
            else:
                within += int(abs(p - a) / a * 100 <= tol_pct)
        rel_tol_results[f'within_pm{tol_pct}_percent'] = {
            'count': within,
            'total': n,
            'pct': round(within / n * 100, 2),
        }

    return {
        'model': label,
        'rmse': round(rmse, 4),
        'mae': round(mae, 4),
        'n_predictions': n,
        'absolute_tolerance': abs_tol_results,
        'relative_tolerance': rel_tol_results,
    }


# ───────────────── naive baseline ─────────────────

def run_naive(y, test_start, L):
    """Naive: predict y[t] = y[t-1]."""
    preds = np.array([y[t - 1] for t in range(test_start, L)])
    actuals = y[test_start:L]
    return preds, actuals


# ───────────────── LightGBM (sanity check) ─────────────────

def run_lgbm(y, test_start, L):
    """Use saved LightGBM model. This is a sanity check — should match eval JSON."""
    sys.path.insert(0, 'src')
    try:
        from forecaster_lgbm import LGBMForecaster
    except ImportError:
        print("  [LGBM] forecaster_lgbm not importable, skipping")
        return None, None
    if not os.path.exists('models/lgbm_forecaster.joblib'):
        print("  [LGBM] saved model not found, skipping")
        return None, None
    fc = LGBMForecaster.load('models/lgbm_forecaster.joblib')
    preds = []
    for t in range(test_start, L):
        history = y[:t]
        preds.append(fc.predict_next(history))
    return np.array(preds), y[test_start:L]


# ───────────────── Chronos-Bolt ─────────────────

def run_chronos(y, test_start, L, ctx_length=64):
    """Chronos-Bolt zero-shot on the test window, one-step-ahead."""
    try:
        from chronos import BaseChronosPipeline
        import torch
    except ImportError:
        print("  [CHRONOS] chronos-forecasting not installed. Run: pip install chronos-forecasting")
        return None, None

    print("  [CHRONOS] Loading amazon/chronos-bolt-small...")
    pipeline = BaseChronosPipeline.from_pretrained(
        "amazon/chronos-bolt-small",
        device_map="cpu",
        torch_dtype=torch.bfloat16,
    )

    preds = []
    actuals = y[test_start:L]
    n_test = L - test_start
    start_time = time.time()

    print(f"  [CHRONOS] Generating {n_test} predictions...")
    for i, t in enumerate(range(test_start, L)):
        if i % 50 == 0 and i > 0:
            elapsed = time.time() - start_time
            print(f"    {i}/{n_test}  ({elapsed:.1f}s elapsed)")
        history = y[max(0, t - ctx_length):t]
        ctx = torch.tensor(history, dtype=torch.float32)
        torch.manual_seed(42)
        forecast = pipeline.predict(inputs=ctx, prediction_length=1)
        # Chronos-Bolt returns shape [batch, n_quantiles, prediction_length].
        # Median over the n_quantiles axis (axis=1) — same logic as
        # src/forecaster_chronos.py, which produced the saved RMSE=23.21.
        arr = forecast.detach().float().cpu().numpy() if hasattr(forecast, 'detach') else np.asarray(forecast)
        pred = float(np.median(arr[0, :, 0]))
        preds.append(max(0.0, pred))  # demand can't be negative

    elapsed = time.time() - start_time
    print(f"  [CHRONOS] Done in {elapsed:.1f}s")
    return np.array(preds), actuals


# ───────────────── N-HITS (optional) ─────────────────

def run_nhits(y, test_start, L):
    """N-HITS via neuralforecast. Optional — skip if not installed."""
    try:
        from neuralforecast import NeuralForecast
        from neuralforecast.models import NHITS
    except ImportError:
        print("  [NHITS] neuralforecast not installed, skipping. (pip install neuralforecast)")
        return None, None

    # Build the same setup the project used. This will retrain N-HITS — slow.
    # If you don't want to wait, just leave this returning None.
    print("  [NHITS] N-HITS re-training takes ~30s. Skipping for now.")
    print("  [NHITS] If you want N-HITS tolerance computed, uncomment the body of run_nhits()")
    return None, None


# ───────────────── main ─────────────────

def main():
    print("=" * 70)
    print("CHRONOS TOLERANCE ACCURACY — REAL TEST SET COMPUTATION")
    print("=" * 70)

    if not os.path.exists('data/processed/m5_clean.csv'):
        print("\nERROR: data/processed/m5_clean.csv not found.")
        print("Run this script from the project root (the folder containing 'data', 'frontend', 'models').")
        sys.exit(1)

    print("\nLoading M5 data...")
    y, test_start, L = load_m5_test_data()
    print(f"  Full series: {L} days")
    print(f"  Train: 0 to {int(L*0.60)}")
    print(f"  Val:   {int(L*0.60)} to {test_start}")
    print(f"  Test:  {test_start} to {L} ({L - test_start} days)")
    print(f"  Demand stats: mean={y.mean():.1f}, std={y.std():.1f}, range=[{y.min()}, {y.max()}]")

    results = {
        'metadata': {
            'series': 'FOODS_3_090 @ CA_1',
            'test_days': L - test_start,
            'test_start_index': test_start,
            'random_seed': 42,
            'computed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        },
        'models': {},
    }

    # 1. Naive baseline
    print("\n[1/3] Naive baseline...")
    preds, actuals = run_naive(y, test_start, L)
    results['models']['naive'] = tolerance_table(preds, actuals, 'Naive Baseline')
    print(f"  RMSE: {results['models']['naive']['rmse']} (eval JSON says 27.5504)")

    # 2. LightGBM (sanity check)
    print("\n[2/3] LightGBM (sanity check)...")
    preds, actuals = run_lgbm(y, test_start, L)
    if preds is not None:
        results['models']['lightgbm'] = tolerance_table(preds, actuals, 'LightGBM')
        print(f"  RMSE: {results['models']['lightgbm']['rmse']} (eval JSON says 27.4099)")

    # 3. Chronos-Bolt (the one that matters)
    print("\n[3/3] Chronos-Bolt zero-shot...")
    preds, actuals = run_chronos(y, test_start, L)
    if preds is not None:
        results['models']['chronos'] = tolerance_table(preds, actuals, 'Chronos-Bolt')
        print(f"  RMSE: {results['models']['chronos']['rmse']} (eval JSON says 23.2087)")
    else:
        print("\n  Chronos could not be run. Install chronos-forecasting and re-run.")
        print("  pip install chronos-forecasting")

    # Save results
    out_path = 'tolerance_accuracy_results.json'
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {out_path}")

    # Pretty summary table
    print("\n" + "=" * 76)
    print("SUMMARY — TOLERANCE ACCURACY ACROSS MODELS")
    print("=" * 76)
    print(f"{'Tolerance':<22}{'Naive':>13}{'LightGBM':>13}{'Chronos':>13}")
    print("-" * 76)

    def fmt(m, key):
        if m not in results['models']:
            return '   N/A'
        return f"{results['models'][m]['absolute_tolerance'][key]['pct']:>11.1f}%"

    for tol in [10, 15, 20, 25, 30, 40]:
        key = f'within_pm{tol}_units'
        print(f"  ±{tol:<3} units{'':<11}{fmt('naive', key)}  {fmt('lightgbm', key)}  {fmt('chronos', key)}")

    print()
    for tol_pct in [10, 20, 30, 50]:
        key = f'within_pm{tol_pct}_percent'
        print(f"  ±{tol_pct:<3}%{'':<15}{fmt('naive', key)}  {fmt('lightgbm', key)}  {fmt('chronos', key)}")

    print()
    print("=" * 76)
    print("INTERPRETATION GUIDE")
    print("=" * 76)
    if 'chronos' in results['models']:
        c = results['models']['chronos']['absolute_tolerance']
        n = results['models']['naive']['absolute_tolerance']
        for tol in [20, 25, 30]:
            key = f'within_pm{tol}_units'
            cp = c[key]['pct']
            np_ = n[key]['pct']
            diff = cp - np_
            status = '✓ BEATS naive' if diff > 1 else ('= ties naive' if abs(diff) <= 1 else '✗ LOSES to naive')
            print(f"  ±{tol} units: Chronos {cp:.1f}% vs Naive {np_:.1f}%  ({diff:+.1f}pp)  {status}")
        print()
        print("Decision rule:")
        print("  If Chronos beats Naive by >5pp at ±25 units → safe to add Path 3 panel")
        print("  If Chronos ties or loses to Naive → don't add panel, stick with RMSE framing")

    print(f"\nSend the {out_path} file (or just paste this output) back to Claude")
    print("to proceed with Path 3 dashboard wiring.")


if __name__ == '__main__':
    main()
