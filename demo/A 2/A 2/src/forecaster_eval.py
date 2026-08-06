#!/usr/bin/env python3
"""Common evaluation harness for Phase 4.5 demand forecasters.

Protocol — matches the Phase 2.7 robust 60/20/20 split on the M5 series
so downstream forecast-augmented CMA-ES training (Phase 4.6) sees the
same train/validation/test boundaries as the inventory policy.

    train: days [0           , 0.60 * L)   — fit model
    val:   days [0.60 * L    , 0.80 * L)   — early-stop / select hyperparams
    test:  days [0.80 * L    , L)          — reported once, never fit on

Baselines (all three SOTA models must beat these):

    naive:          y_hat[t+1] = y[t]
    seasonal_naive: y_hat[t+1] = y[t-7]
    moving_avg_7:   y_hat[t+1] = mean(y[t-6..t])

Metrics: MAE, RMSE, sMAPE (we use sMAPE rather than MAPE because M5
demand has days with y=0, which blows up MAPE. sMAPE is bounded and
is what the M5 competition itself reported as a supplementary metric.)
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from multi_echelon import _load_m5_series                    # noqa: E402


# ───────────────── data split ─────────────────

def load_m5_and_split(
    train_frac: float = 0.60,
    val_frac:   float = 0.20,
    data_path:   str = 'data/processed/m5_clean.csv',
    scaler_path: Optional[str] = None,
):
    """Return (train, val, test) 1-D demand arrays and the full series.

    test_frac is implied = 1 − train_frac − val_frac. Reads the clean
    integer-demand CSV produced by src/preprocess_m5_raw.py — real units
    (mean ≈66/day), NOT z-scored. `scaler_path` is retained for
    signature-compat with older callers and is ignored.
    """
    y = _load_m5_series(data_path=data_path, scaler_path=scaler_path)
    L = len(y)
    train_end = int(L * train_frac)
    val_end   = int(L * (train_frac + val_frac))
    return y[:train_end], y[train_end:val_end], y[val_end:], y


# ───────────────── metrics ─────────────────

def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Symmetric mean absolute percentage error, in percent.
    Safe when y_true==0 (denominator has a small floor)."""
    num = np.abs(y_true - y_pred)
    den = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    den = np.where(den < 1e-9, 1e-9, den)
    return float(100.0 * np.mean(num / den))


def metric_dict(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    return {
        'mae':   mae(y_true, y_pred),
        'rmse':  rmse(y_true, y_pred),
        'smape': smape(y_true, y_pred),
    }


# ───────────────── baselines ─────────────────

def naive_forecast(history: np.ndarray, horizon: int = 1) -> np.ndarray:
    """Return horizon-step predictions from last observed value."""
    return np.full(horizon, history[-1])


def seasonal_naive_forecast(history: np.ndarray,
                            horizon: int = 1,
                            season: int = 7) -> np.ndarray:
    """Predict each step as the value 'season' periods ago, rolling."""
    out = []
    for h in range(horizon):
        idx = -season + h
        if abs(idx) > len(history):
            idx = -1
        out.append(history[idx])
    return np.asarray(out, dtype=np.float64)


def moving_average_forecast(history: np.ndarray,
                            horizon: int = 1,
                            window: int = 7) -> np.ndarray:
    """Rolling mean of the last `window` observations, repeated."""
    if len(history) >= window:
        m = float(np.mean(history[-window:]))
    else:
        m = float(np.mean(history))
    return np.full(horizon, m)


BASELINES: Dict[str, Callable[[np.ndarray], np.ndarray]] = {
    'naive':           lambda h: naive_forecast(h, 1),
    'seasonal_naive':  lambda h: seasonal_naive_forecast(h, 1, season=7),
    'moving_avg_7':    lambda h: moving_average_forecast(h, 1, window=7),
}


# ───────────────── rolling one-step evaluation ─────────────────

def rolling_one_step_eval(
    y_full: np.ndarray,
    test_start: int,
    test_end: int,
    predict_fn: Callable[[np.ndarray], float],
    min_history: int = 30,
) -> Dict[str, float]:
    """Evaluate one-step-ahead predictions over y_full[test_start:test_end].

    For each target day t in [test_start, test_end), we give predict_fn
    all observations y_full[:t] (so the model never peeks at the target
    itself) and compare its scalar prediction against y_full[t].

    min_history is the shortest warm-up history the predict function is
    allowed to see; we clip test_start upward if needed.
    """
    start = max(test_start, min_history)
    preds = np.zeros(test_end - start)
    for i, t in enumerate(range(start, test_end)):
        preds[i] = float(predict_fn(y_full[:t]))
    truth = y_full[start:test_end]
    return {
        **metric_dict(truth, preds),
        'n_predictions': int(len(preds)),
        'start_index':   int(start),
        'end_index':     int(test_end),
    }


# ───────────────── public helpers ─────────────────

def evaluate_baselines(y_full: np.ndarray,
                       test_start: int,
                       test_end: int) -> Dict[str, Dict[str, float]]:
    """Evaluate all three baselines and return their metric dicts."""
    out: Dict[str, Dict[str, float]] = {}
    for name, fn in BASELINES.items():
        out[name] = rolling_one_step_eval(
            y_full=y_full,
            test_start=test_start,
            test_end=test_end,
            predict_fn=lambda h, fn=fn: float(fn(h)[0]),
            min_history=30,
        )
    return out


def format_table(rows: List[Dict[str, object]],
                 columns: List[str]) -> str:
    """Render a simple fixed-width comparison table for the report."""
    widths = {c: max(len(c), max(len(str(r.get(c, ''))) for r in rows))
              for c in columns}
    line = '  '.join(f"{c:<{widths[c]}}" for c in columns)
    sep  = '  '.join('-' * widths[c] for c in columns)
    body = '\n'.join(
        '  '.join(f"{str(r.get(c, '')):<{widths[c]}}" for c in columns)
        for r in rows
    )
    return '\n'.join([line, sep, body])


if __name__ == '__main__':
    y_tr, y_va, y_te, y_full = load_m5_and_split()
    test_start = len(y_tr) + len(y_va)
    test_end   = len(y_full)
    print(f"M5 demand series length = {len(y_full)}")
    print(f"  train: [0,{len(y_tr)}) = {len(y_tr)} days")
    print(f"  val:   [{len(y_tr)},{test_start}) = {len(y_va)} days")
    print(f"  test:  [{test_start},{test_end}) = {test_end - test_start} days")

    res = evaluate_baselines(y_full, test_start, test_end)
    rows = [{'model': k, **{m: f"{v[m]:.3f}" for m in ('mae', 'rmse', 'smape')}}
            for k, v in res.items()]
    print()
    print(format_table(rows, ['model', 'mae', 'rmse', 'smape']))
