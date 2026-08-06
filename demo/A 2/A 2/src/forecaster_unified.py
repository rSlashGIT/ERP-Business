#!/usr/bin/env python3
"""Unified demand forecaster interface — PRD 1 (Videh's pillar).

Exposes all three forecasters through a single predict_demand() function
so the inventory team can swap models without changing call sites.

Usage:
    from src.forecaster_unified import predict_demand, load_forecaster

    # Load once, predict many times
    fc = load_forecaster('chronos')      # or 'lgbm' or 'nhits'
    y_hat = predict_demand(fc, history)  # history: 1-D numpy array of past demand
"""

from __future__ import annotations

import os
import sys
from typing import Literal

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

ModelName = Literal['lgbm', 'nhits', 'chronos']

LGBM_MODEL_PATH  = os.path.join('models', 'lgbm_forecaster.joblib')
NHITS_CKPT_DIR   = os.path.join('models', 'nhits_forecaster')
CHRONOS_VARIANT  = 'amazon/chronos-bolt-small'


def load_forecaster(model: ModelName):
    """Load and return a forecaster object by name.

    Parameters
    ----------
    model : 'lgbm' | 'nhits' | 'chronos'

    Returns
    -------
    A forecaster object with a .predict_next(history: np.ndarray) -> float method.
    """
    if model == 'lgbm':
        from forecaster_lgbm import LGBMForecaster
        return LGBMForecaster.load(LGBM_MODEL_PATH)

    if model == 'nhits':
        from neuralforecast import NeuralForecast
        from forecaster_nhits import NHITSForecaster
        nf = NeuralForecast.load(path=NHITS_CKPT_DIR)
        return NHITSForecaster(nf=nf)

    if model == 'chronos':
        from forecaster_chronos import ChronosForecaster, load_pipeline
        pipe, variant = load_pipeline(CHRONOS_VARIANT)
        return ChronosForecaster(pipe=pipe, variant=variant)

    raise ValueError(f"Unknown model '{model}'. Choose from: lgbm, nhits, chronos")


def predict_demand(forecaster, history: np.ndarray) -> float:
    """Predict next-day demand given a 1-D array of past demand values.

    Parameters
    ----------
    forecaster : object returned by load_forecaster()
    history    : np.ndarray, shape (T,), daily demand for days 0..T-1

    Returns
    -------
    float — predicted demand for day T (clipped to >= 0)
    """
    history = np.asarray(history, dtype=np.float64)
    return float(max(0.0, forecaster.predict_next(history)))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Unified forecaster demo')
    parser.add_argument('--model', choices=['lgbm', 'nhits', 'chronos'],
                        default='lgbm')
    parser.add_argument('--n-predictions', type=int, default=5)
    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from forecaster_eval import load_m5_and_split

    y_tr, y_va, y_te, y_full = load_m5_and_split()
    val_end = len(y_tr) + len(y_va)

    print(f'Loading {args.model} forecaster ...')
    fc = load_forecaster(args.model)
    print(f'Loaded. Running {args.n_predictions} sample predictions on test set.')
    print()
    print(f"{'Day':>5}  {'Actual':>8}  {'Predicted':>10}")
    print('-' * 28)
    for i in range(args.n_predictions):
        t = val_end + i
        y_hat = predict_demand(fc, y_full[:t])
        print(f'{t:>5}  {y_full[t]:>8.0f}  {y_hat:>10.2f}')
