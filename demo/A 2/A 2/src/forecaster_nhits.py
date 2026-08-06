#!/usr/bin/env python3
"""N-HITS one-step-ahead demand forecaster for the M5 series.

Architecture: Neural Hierarchical Interpolation for Time Series
(Challu et al., 2023 — AAAI). Multi-rate MLP, NOT an RNN / LSTM /
transformer / RL component — explicitly within Phase 4.5's allowed
model classes.

Protocol: the same 60/20/20 robust split as Phase 2.7 and the Phase 4.5
LightGBM forecaster. The model trains on the first 60% of the M5
series, uses the next 20% as neuralforecast's validation set for
early-stopping (val_check_steps=50, patience=5), and is evaluated
once on the final 20% via rolling one-step-ahead predictions that
reuse the trained model (no re-fit per step).

CPU-only: we force accelerator='cpu' and devices=1 so the user's M2
(8 GB RAM) does not try to spin up MPS. The whole training is a small
hierarchical MLP; it finishes well under the 10-minute guardrail.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import warnings
from typing import Dict, List

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
logging.getLogger('pytorch_lightning').setLevel(logging.ERROR)
logging.getLogger('lightning').setLevel(logging.ERROR)
logging.getLogger('lightning_fabric').setLevel(logging.ERROR)
logging.getLogger('pytorch_lightning.utilities.rank_zero').setLevel(logging.ERROR)
os.environ.setdefault('PYTORCH_DISABLE_PER_OP_PROFILING', '1')
os.environ.setdefault('NIXTLA_ID_AS_COL', '1')

sys.path.insert(0, os.path.dirname(__file__))

from forecaster_eval import (                                 # noqa: E402
    evaluate_baselines,
    format_table,
    load_m5_and_split,
    rolling_one_step_eval,
)


MODELS_DIR     = 'models'
CKPT_DIR       = os.path.join(MODELS_DIR, 'nhits_forecaster')
EVAL_PATH      = os.path.join(MODELS_DIR, 'nhits_evaluation.json')
MIN_HISTORY    = 30   # = input_size
SYNTHETIC_START = '2020-01-01'  # neuralforecast needs real timestamps


# ───────────────── data formatting ─────────────────

def _to_nf_df(y: np.ndarray, start: int = 0,
              unique_id: str = 'store_A') -> pd.DataFrame:
    """Turn a 1-D demand array (interpreted as days ``start, start+1, …``)
    into a neuralforecast-flavoured (unique_id, ds, y) frame."""
    ds = pd.date_range(SYNTHETIC_START, periods=10_000, freq='D')
    return pd.DataFrame({
        'unique_id': unique_id,
        'ds':        ds[start:start + len(y)],
        'y':         y.astype(np.float64),
    })


# ───────────────── predictor wrapper ─────────────────

class NHITSForecaster:
    """Wraps a trained neuralforecast NeuralForecast object so it plugs
    into rolling_one_step_eval. We slice the history down to the last
    ``input_size`` observations per call, build a one-row-per-day mini
    frame, and call nf.predict — the model is NOT re-fit between calls.
    """

    def __init__(self, nf, input_size: int = MIN_HISTORY):
        self.nf = nf
        self.input_size = input_size

    def predict_next(self, history: np.ndarray) -> float:
        if len(history) < self.input_size:
            return float(history[-1]) if len(history) else 0.0
        tail = history[-self.input_size:]
        # Use a stable synthetic start so ds lines up with training format.
        df = _to_nf_df(tail, start=0, unique_id='store_A')
        pred = self.nf.predict(df=df, verbose=False)
        # neuralforecast returns a DataFrame with column 'NHITS'.
        col = 'NHITS' if 'NHITS' in pred.columns else pred.columns[-1]
        y_hat = float(pred[col].iloc[0])
        return float(max(0.0, y_hat))


# ───────────────── trainer ─────────────────

def train_nhits(
    y_full: np.ndarray,
    train_end: int,
    val_end: int,
    input_size: int = MIN_HISTORY,
    horizon: int = 1,
    max_steps: int = 500,
    val_check_steps: int = 50,
    early_stop_patience_steps: int = 5,
    batch_size: int = 32,
    n_freq_downsample: List[int] = [7, 1, 1],
    random_seed: int = 42,
    verbose: bool = True,
) -> Dict:
    """Fit a single-series N-HITS model. Returns dict with nf + metadata."""
    from neuralforecast import NeuralForecast
    from neuralforecast.models import NHITS
    from neuralforecast.losses.pytorch import MAE

    # Build train+val frame; neuralforecast will carve out the last
    # val_size rows for its val monitor.
    y_trainval = y_full[:val_end]
    df = _to_nf_df(y_trainval)
    val_size = val_end - train_end

    if verbose:
        print(f"  train+val frame rows = {len(df)}  val_size = {val_size}")
        print(f"  N-HITS config: input_size={input_size}  h={horizon}  "
              f"n_freq_downsample={n_freq_downsample}  max_steps={max_steps}")

    model = NHITS(
        h=horizon,
        input_size=input_size,
        n_freq_downsample=n_freq_downsample,
        loss=MAE(),
        max_steps=max_steps,
        val_check_steps=val_check_steps,
        early_stop_patience_steps=early_stop_patience_steps,
        batch_size=batch_size,
        random_seed=random_seed,
        scaler_type='standard',
        accelerator='cpu',
        devices=1,
        enable_progress_bar=False,
        enable_model_summary=False,
        logger=False,
        enable_checkpointing=False,
    )
    nf = NeuralForecast(models=[model], freq='D')

    t0 = time.time()
    nf.fit(df=df, val_size=val_size, verbose=False)
    train_seconds = time.time() - t0

    return {
        'nf':            nf,
        'train_seconds': train_seconds,
        'train_end':     int(train_end),
        'val_end':       int(val_end),
        'val_size':      int(val_size),
        'input_size':    int(input_size),
        'horizon':       int(horizon),
        'hyperparameters': {
            'max_steps':                 max_steps,
            'val_check_steps':           val_check_steps,
            'early_stop_patience_steps': early_stop_patience_steps,
            'batch_size':                batch_size,
            'n_freq_downsample':         n_freq_downsample,
            'loss':                      'MAE',
            'scaler_type':               'standard',
        },
    }


# ───────────────── CLI: train + report ─────────────────

def run(verbose: bool = True, save: bool = True,
        max_steps: int = 500) -> Dict:
    y_tr, y_va, y_te, y_full = load_m5_and_split()
    train_end = len(y_tr)
    val_end   = len(y_tr) + len(y_va)
    test_end  = len(y_full)

    if verbose:
        print("=" * 88)
        print(f"  N-HITS one-step forecaster on M5 (CPU-only)")
        print(f"  train [{0},{train_end})  val [{train_end},{val_end})  "
              f"test [{val_end},{test_end})")
        print("=" * 88)

    info = train_nhits(y_full, train_end, val_end,
                       max_steps=max_steps, verbose=verbose)
    forecaster = NHITSForecaster(nf=info['nf'], input_size=info['input_size'])

    t0 = time.time()
    test_metrics = rolling_one_step_eval(
        y_full=y_full,
        test_start=val_end,
        test_end=test_end,
        predict_fn=forecaster.predict_next,
        min_history=MIN_HISTORY,
    )
    test_metrics['eval_seconds'] = time.time() - t0

    base_metrics = evaluate_baselines(y_full, val_end, test_end)

    # Merge with LightGBM metrics so the report shows the head-to-head
    # across paradigms if the LightGBM eval JSON exists.
    lgbm_row = None
    lgbm_path = os.path.join(MODELS_DIR, 'lgbm_evaluation.json')
    if os.path.exists(lgbm_path):
        with open(lgbm_path) as f:
            lgbm_eval = json.load(f)
        lm = lgbm_eval['test_metrics']
        lgbm_row = {
            'model':   'lightgbm',
            'mae':     f"{lm['mae']:.3f}",
            'rmse':    f"{lm['rmse']:.3f}",
            'smape':   f"{lm['smape']:.3f}",
            'train_s': f"{lgbm_eval['train_seconds']:.2f}",
            'eval_s':  f"{lm.get('eval_seconds', 0):.2f}",
        }

    rows = []
    for name, m in base_metrics.items():
        rows.append({'model': name,
                     'mae':   f"{m['mae']:.3f}",
                     'rmse':  f"{m['rmse']:.3f}",
                     'smape': f"{m['smape']:.3f}",
                     'train_s': '—',
                     'eval_s':  '—'})
    if lgbm_row is not None:
        rows.append(lgbm_row)
    rows.append({'model': 'nhits',
                 'mae':   f"{test_metrics['mae']:.3f}",
                 'rmse':  f"{test_metrics['rmse']:.3f}",
                 'smape': f"{test_metrics['smape']:.3f}",
                 'train_s': f"{info['train_seconds']:.2f}",
                 'eval_s':  f"{test_metrics['eval_seconds']:.2f}"})

    if verbose:
        print()
        print(format_table(rows, ['model', 'mae', 'rmse', 'smape',
                                   'train_s', 'eval_s']))

    best_base_rmse = min(m['rmse'] for m in base_metrics.values())
    naive_rmse     = base_metrics['naive']['rmse']
    beats_naive    = test_metrics['rmse'] < naive_rmse
    beats_best_baseline = test_metrics['rmse'] < best_base_rmse

    if verbose:
        print()
        print(f"  naive RMSE          = {naive_rmse:.3f}")
        print(f"  best baseline RMSE  = {best_base_rmse:.3f}")
        print(f"  nhits RMSE          = {test_metrics['rmse']:.3f}")
        print(f"  beats naive?        = {beats_naive}   (hard gate)")
        print(f"  beats best baseline?= {beats_best_baseline}   (success goal)")

    result = {
        'model':            'nhits',
        'train_end':        train_end,
        'val_end':          val_end,
        'test_end':         test_end,
        'hyperparameters':  info['hyperparameters'],
        'train_seconds':    info['train_seconds'],
        'test_metrics':     test_metrics,
        'baselines':        base_metrics,
        'beats_naive':      beats_naive,
        'beats_best_baseline': beats_best_baseline,
    }

    if save:
        if not beats_naive:
            # Hard gate per user spec: never silently save a model that
            # fails the naive baseline.
            raise RuntimeError(
                f"N-HITS test RMSE {test_metrics['rmse']:.3f} did not "
                f"beat naive RMSE {naive_rmse:.3f}. Refusing to save."
            )
        os.makedirs(CKPT_DIR, exist_ok=True)
        info['nf'].save(path=CKPT_DIR, overwrite=True,
                        save_dataset=False)
        with open(EVAL_PATH, 'w') as f:
            json.dump(result, f, indent=2)
        if verbose:
            print(f"\n  Saved model  -> {CKPT_DIR}")
            print(f"  Saved eval   -> {EVAL_PATH}")

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-save', action='store_true')
    parser.add_argument('--max-steps', type=int, default=500)
    args = parser.parse_args()
    run(verbose=True, save=not args.no_save, max_steps=args.max_steps)
