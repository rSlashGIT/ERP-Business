#!/usr/bin/env python3
"""LightGBM one-step-ahead demand forecaster for the M5 series.

Model class: gradient-boosted trees (Ke et al. 2017). Feature set follows
the Grupo Bimbo retail study of Nguyen, Dang & Le (2024): lag features,
rolling statistics, and calendar proxies.

This is NOT an LSTM, RNN, or DRL component. It is a tree-based regressor
trained once on the 60% training split, hyper-validated with early
stopping on the 20% validation split, then frozen and evaluated on the
20% test split using the rolling_one_step_eval protocol in
src/forecaster_eval.py.

Note: the processed M5 slice in data/processed/processed_data_m5.csv has
Event_None=True and a single store / category for every row (only one
store-SKU was retained in preprocessing). We therefore DROP the SNAP /
Event flags listed in the Bimbo study — they carry zero information in
this slice — and keep lag, rolling, and calendar-proxy features only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from forecaster_eval import (                                 # noqa: E402
    evaluate_baselines,
    format_table,
    load_m5_and_split,
)


MODELS_DIR    = 'models'
MODEL_PATH    = os.path.join(MODELS_DIR, 'lgbm_forecaster.joblib')
MIN_HISTORY   = 30   # must hold longest lag window
LAGS          = [1, 2, 3, 4, 5, 6, 7]
ROLL_WINDOWS  = [7, 14, 30]


# ───────────────── feature engineering ─────────────────

def _make_features_for_day(history: np.ndarray, t: int) -> Dict[str, float]:
    """Build the feature vector for predicting y[t] given y[:t].

    history must include at least `t` observations (i.e. indices 0..t-1
    must be defined). We only read history[:t] — no leakage from t or
    beyond.
    """
    if t < MIN_HISTORY:
        raise ValueError(f"t={t} < MIN_HISTORY={MIN_HISTORY}")
    feats: Dict[str, float] = {}
    for lag in LAGS:
        feats[f'lag_{lag}'] = float(history[t - lag])
    for w in ROLL_WINDOWS:
        feats[f'roll_mean_{w}']  = float(np.mean(history[t - w:t]))
        feats[f'roll_std_{w}']   = float(np.std(history[t - w:t]))
    # Calendar proxies. The processed M5 slice has no true date column,
    # so we use the row index as a synthetic day-of-week / month-of-year
    # cycle. Day-of-week (mod 7) captures the weekly retail pattern; the
    # month proxy is a 30/365 approximation, documented honestly.
    feats['dow']   = int(t % 7)
    feats['month'] = int((t % 365) // 30)
    return feats


def build_xy(y_full: np.ndarray, start: int, end: int
             ) -> Tuple[pd.DataFrame, np.ndarray, List[str]]:
    """Build (X_df, y, feat_names) over one-step-ahead targets y[start:end].

    X is returned as a pandas DataFrame so that LightGBM preserves feature
    names and categorical dtypes across fit / predict.
    """
    start = max(start, MIN_HISTORY)
    rows: List[Dict[str, float]] = []
    targets: List[float] = []
    for t in range(start, end):
        rows.append(_make_features_for_day(y_full, t))
        targets.append(float(y_full[t]))
    X = pd.DataFrame(rows)
    X['dow']   = X['dow'].astype('category')
    X['month'] = X['month'].astype('category')
    feat_names = list(X.columns)
    return X, np.asarray(targets, dtype=np.float64), feat_names


# ───────────────── trainer ─────────────────

def train_lgbm(
    y_full: np.ndarray,
    train_end: int,
    val_end: int,
    num_leaves: int = 31,
    learning_rate: float = 0.05,
    n_estimators: int = 200,
    early_stopping_rounds: int = 20,
    random_state: int = 42,
    verbose: bool = True,
) -> Dict:
    """Train a LightGBM regressor. Returns dict with model + metadata."""
    import lightgbm as lgb

    X_tr, y_tr, feat_names = build_xy(y_full, MIN_HISTORY, train_end)
    X_va, y_va, _          = build_xy(y_full, train_end, val_end)

    if verbose:
        print(f"  train: X={X_tr.shape}  y={y_tr.shape}")
        print(f"  val:   X={X_va.shape}  y={y_va.shape}")
        print(f"  features: {feat_names}")

    model = lgb.LGBMRegressor(
        objective='regression',
        metric='rmse',
        num_leaves=num_leaves,
        learning_rate=learning_rate,
        n_estimators=n_estimators,
        random_state=random_state,
        verbose=-1,
    )
    t0 = time.time()
    model.fit(
        X_tr, y_tr,
        eval_set=[(X_va, y_va)],
        eval_metric='rmse',
        callbacks=[lgb.early_stopping(stopping_rounds=early_stopping_rounds,
                                      verbose=verbose),
                   lgb.log_evaluation(period=0)],
    )
    train_seconds = time.time() - t0

    return {
        'model':              model,
        'feature_names':      feat_names,
        'train_seconds':      train_seconds,
        'best_iteration':     int(model.best_iteration_ or n_estimators),
        'train_end':          int(train_end),
        'val_end':            int(val_end),
        'min_history':        MIN_HISTORY,
        'hyperparameters': {
            'num_leaves':            num_leaves,
            'learning_rate':         learning_rate,
            'n_estimators':          n_estimators,
            'early_stopping_rounds': early_stopping_rounds,
        },
    }


# ───────────────── predictor wrapper for rolling eval ─────────────────

class LGBMForecaster:
    """Wraps a trained LightGBM model so it plugs into rolling_one_step_eval."""

    def __init__(self, model, feature_names: List[str], min_history: int = MIN_HISTORY):
        self.model         = model
        self.feature_names = feature_names
        self.min_history   = min_history

    def predict_next(self, history: np.ndarray) -> float:
        t = len(history)
        if t < self.min_history:
            # Defer to naive when we have no features; rolling_one_step_eval
            # already enforces min_history=30 by default so this is just a
            # safety net for tests that poke the predictor directly.
            return float(history[-1]) if len(history) else 0.0
        feats = _make_features_for_day(
            np.concatenate([history, np.zeros(1)]), t
        )
        X = pd.DataFrame([feats], columns=self.feature_names)
        X['dow']   = X['dow'].astype('category')
        X['month'] = X['month'].astype('category')
        y_hat = self.model.predict(X)
        return float(max(0.0, y_hat[0]))

    def save(self, path: str = MODEL_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump({
            'model':         self.model,
            'feature_names': self.feature_names,
            'min_history':   self.min_history,
        }, path)

    @classmethod
    def load(cls, path: str = MODEL_PATH) -> 'LGBMForecaster':
        blob = joblib.load(path)
        return cls(model=blob['model'],
                   feature_names=blob['feature_names'],
                   min_history=int(blob.get('min_history', MIN_HISTORY)))


# ───────────────── CLI: train + report ─────────────────

def run(verbose: bool = True, save: bool = True) -> Dict:
    from forecaster_eval import rolling_one_step_eval

    y_tr, y_va, y_te, y_full = load_m5_and_split()
    train_end = len(y_tr)
    val_end   = len(y_tr) + len(y_va)
    test_end  = len(y_full)

    if verbose:
        print("=" * 88)
        print(f"  LightGBM one-step forecaster on M5")
        print(f"  train days [{0},{train_end})  val [{train_end},{val_end})  "
              f"test [{val_end},{test_end})")
        print("=" * 88)

    info = train_lgbm(y_full, train_end, val_end, verbose=verbose)
    forecaster = LGBMForecaster(model=info['model'],
                                feature_names=info['feature_names'])

    # Test evaluation.
    t0 = time.time()
    test_metrics = rolling_one_step_eval(
        y_full=y_full,
        test_start=val_end,
        test_end=test_end,
        predict_fn=forecaster.predict_next,
        min_history=MIN_HISTORY,
    )
    test_metrics['eval_seconds'] = time.time() - t0

    # Baselines for the comparison table.
    base_metrics = evaluate_baselines(y_full, val_end, test_end)

    rows = []
    for name, m in base_metrics.items():
        rows.append({'model': name,
                     'mae':   f"{m['mae']:.3f}",
                     'rmse':  f"{m['rmse']:.3f}",
                     'smape': f"{m['smape']:.3f}",
                     'train_s': '—',
                     'eval_s':  '—'})
    rows.append({'model': 'lightgbm',
                 'mae':   f"{test_metrics['mae']:.3f}",
                 'rmse':  f"{test_metrics['rmse']:.3f}",
                 'smape': f"{test_metrics['smape']:.3f}",
                 'train_s': f"{info['train_seconds']:.2f}",
                 'eval_s':  f"{test_metrics['eval_seconds']:.2f}"})

    if verbose:
        print()
        print(format_table(rows, ['model', 'mae', 'rmse', 'smape',
                                   'train_s', 'eval_s']))

    # Gate: LightGBM must beat every baseline on RMSE. If not, report
    # loudly and do not save.
    best_base_rmse = min(m['rmse'] for m in base_metrics.values())
    beats_baselines = test_metrics['rmse'] < best_base_rmse
    if verbose:
        print()
        print(f"  best baseline RMSE = {best_base_rmse:.3f}")
        print(f"  lightgbm RMSE      = {test_metrics['rmse']:.3f}")
        print(f"  beats baselines?   = {beats_baselines}")

    result = {
        'model':        'lightgbm',
        'train_end':    train_end,
        'val_end':      val_end,
        'test_end':     test_end,
        'best_iteration':  info['best_iteration'],
        'hyperparameters': info['hyperparameters'],
        'train_seconds':   info['train_seconds'],
        'test_metrics':    test_metrics,
        'baselines':       base_metrics,
        'beats_baselines': beats_baselines,
    }

    if save:
        if not beats_baselines:
            print("\n  WARNING: LightGBM did not beat all baselines; "
                  "refusing to save over the production model file.")
        else:
            forecaster.save(MODEL_PATH)
            with open(os.path.join(MODELS_DIR, 'lgbm_evaluation.json'), 'w') as f:
                json.dump(result, f, indent=2)
            if verbose:
                print(f"\n  Saved model -> {MODEL_PATH}")
                print(f"  Saved eval  -> models/lgbm_evaluation.json")

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-save', action='store_true')
    args = parser.parse_args()
    run(verbose=True, save=not args.no_save)
