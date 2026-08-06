#!/usr/bin/env python3
"""Chronos-Bolt zero-shot demand forecaster for the M5 series.

Uses Amazon's pre-trained time-series foundation model (Ansari et al.,
2024 — arXiv:2403.07815) WITHOUT any fine-tuning. We load
``amazon/chronos-bolt-small`` (50M params, CPU-feasible on an M2) and
forecast one step at a time over the held-out test window.

Protocol: same 60/20/20 split as LightGBM and N-HITS. Because Chronos
is zero-shot we skip the 'training' phase entirely — no training time
column in the final report — but we still respect the train / val
boundaries by never feeding the model any observations past the
cut-off of the day it is predicting.

CPU-only. We try bfloat16 first; if the host does not support bf16
matmul the pipeline falls back to float32 (we catch the exception
explicitly rather than running a silent GPU path).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import warnings
from typing import Dict, Optional

import numpy as np

warnings.filterwarnings('ignore')
logging.getLogger('transformers').setLevel(logging.ERROR)
os.environ.setdefault('TRANSFORMERS_VERBOSITY', 'error')
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')

sys.path.insert(0, os.path.dirname(__file__))

from forecaster_eval import (                                 # noqa: E402
    evaluate_baselines,
    format_table,
    load_m5_and_split,
    rolling_one_step_eval,
)


MODELS_DIR       = 'models'
EVAL_PATH        = os.path.join(MODELS_DIR, 'chronos_evaluation.json')
DEFAULT_VARIANT  = 'amazon/chronos-bolt-small'
FALLBACK_VARIANT = 'amazon/chronos-bolt-tiny'
CONTEXT_LENGTH   = 64
MIN_HISTORY      = 30


# ───────────────── pipeline loader ─────────────────

_PIPELINE_CACHE: Dict[str, object] = {}


def load_pipeline(variant: str = DEFAULT_VARIANT):
    """Load a Chronos-Bolt pipeline once and cache it. Falls back to the
    tiny variant if the requested one cannot be loaded (e.g. download
    failure, OOM). Prefer bfloat16, degrade to float32 on exception."""
    import torch
    from chronos import BaseChronosPipeline

    if variant in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[variant], variant

    try:
        pipe = BaseChronosPipeline.from_pretrained(
            variant,
            device_map='cpu',
            torch_dtype=torch.bfloat16,
        )
        loaded_variant = variant
    except Exception as bf16_err:
        try:
            pipe = BaseChronosPipeline.from_pretrained(
                variant,
                device_map='cpu',
                torch_dtype=torch.float32,
            )
            loaded_variant = variant
            print(f"  (bfloat16 load failed: {bf16_err}; using float32)")
        except Exception as small_err:
            if variant == FALLBACK_VARIANT:
                raise
            print(f"  (could not load {variant}: {small_err}; "
                  f"falling back to {FALLBACK_VARIANT})")
            pipe = BaseChronosPipeline.from_pretrained(
                FALLBACK_VARIANT,
                device_map='cpu',
                torch_dtype=torch.float32,
            )
            loaded_variant = FALLBACK_VARIANT

    _PIPELINE_CACHE[loaded_variant] = pipe
    return pipe, loaded_variant


# ───────────────── predictor wrapper ─────────────────

class ChronosForecaster:
    """Zero-shot one-step-ahead predictor. Reuses a single cached
    pipeline across every call so the model weights load once."""

    def __init__(self, pipe, variant: str,
                 context_length: int = CONTEXT_LENGTH,
                 min_history: int = MIN_HISTORY):
        self.pipe            = pipe
        self.variant         = variant
        self.context_length  = context_length
        self.min_history     = min_history

    def predict_next(self, history: np.ndarray) -> float:
        import torch
        torch.manual_seed(42)  # Chronos-Bolt quantile heads are deterministic; seed for auditability

        if len(history) < self.min_history:
            return float(history[-1]) if len(history) else 0.0
        n = min(self.context_length, len(history))
        context = torch.tensor(history[-n:], dtype=torch.float32)
        out = self.pipe.predict(inputs=context, prediction_length=1)
        # Chronos-Bolt returns a tensor of shape
        #   [batch, n_quantiles_or_samples, prediction_length]
        # We collapse over axis 1 via median (works both for sample- and
        # quantile-flavoured outputs) and take step 0.
        arr = out.detach().float().cpu().numpy() if hasattr(out, 'detach') else np.asarray(out)
        med = float(np.median(arr[0, :, 0]))
        return float(max(0.0, med))


# ───────────────── CLI: evaluate + report ─────────────────

def run(variant: str = DEFAULT_VARIANT,
        verbose: bool = True,
        save: bool = True) -> Dict:
    y_tr, y_va, y_te, y_full = load_m5_and_split()
    train_end = len(y_tr)
    val_end   = len(y_tr) + len(y_va)
    test_end  = len(y_full)

    if verbose:
        print("=" * 88)
        print(f"  Chronos-Bolt zero-shot forecaster on M5 (CPU-only)")
        print(f"  train [{0},{train_end})  val [{train_end},{val_end})  "
              f"test [{val_end},{test_end})")
        print("=" * 88)

    t0 = time.time()
    pipe, loaded_variant = load_pipeline(variant)
    setup_seconds = time.time() - t0
    if verbose:
        print(f"  loaded: {loaded_variant}  (setup {setup_seconds:.2f}s)")

    forecaster = ChronosForecaster(pipe=pipe, variant=loaded_variant)

    t0 = time.time()
    test_metrics = rolling_one_step_eval(
        y_full=y_full,
        test_start=val_end,
        test_end=test_end,
        predict_fn=forecaster.predict_next,
        min_history=MIN_HISTORY,
    )
    test_metrics['eval_seconds'] = time.time() - t0
    per_call_s = test_metrics['eval_seconds'] / max(1, test_metrics['n_predictions'])
    if verbose:
        print(f"  389-day rolling eval: {test_metrics['eval_seconds']:.2f}s "
              f"({per_call_s*1000:.1f} ms/prediction)")

    base_metrics = evaluate_baselines(y_full, val_end, test_end)

    # Pull LightGBM / N-HITS rows for the final head-to-head table.
    rows = []
    for name, m in base_metrics.items():
        rows.append({'model': name,
                     'mae':   f"{m['mae']:.3f}",
                     'rmse':  f"{m['rmse']:.3f}",
                     'smape': f"{m['smape']:.3f}",
                     'train_s': '—',
                     'eval_s':  '—',
                     'gpu':     'no'})

    for key, label in (('lgbm_evaluation.json', 'lightgbm'),
                       ('nhits_evaluation.json', 'nhits')):
        path = os.path.join(MODELS_DIR, key)
        if not os.path.exists(path):
            continue
        with open(path) as f:
            blob = json.load(f)
        tm = blob['test_metrics']
        rows.append({
            'model':   label,
            'mae':     f"{tm['mae']:.3f}",
            'rmse':    f"{tm['rmse']:.3f}",
            'smape':   f"{tm['smape']:.3f}",
            'train_s': f"{blob.get('train_seconds', 0):.2f}",
            'eval_s':  f"{tm.get('eval_seconds', 0):.2f}",
            'gpu':     'no',
        })

    rows.append({
        'model':   'chronos_bolt',
        'mae':     f"{test_metrics['mae']:.3f}",
        'rmse':    f"{test_metrics['rmse']:.3f}",
        'smape':   f"{test_metrics['smape']:.3f}",
        'train_s': f"{setup_seconds:.2f}*",  # * = zero-shot setup, not fit
        'eval_s':  f"{test_metrics['eval_seconds']:.2f}",
        'gpu':     'no',
    })

    if verbose:
        print()
        print(format_table(rows, ['model', 'mae', 'rmse', 'smape',
                                   'train_s', 'eval_s', 'gpu']))
        print("  (* = zero-shot model-load time, not training)")

    naive_rmse     = base_metrics['naive']['rmse']
    best_base_rmse = min(m['rmse'] for m in base_metrics.values())
    beats_naive    = test_metrics['rmse'] < naive_rmse
    beats_best_baseline = test_metrics['rmse'] < best_base_rmse
    if verbose:
        print()
        print(f"  naive RMSE          = {naive_rmse:.3f}")
        print(f"  best baseline RMSE  = {best_base_rmse:.3f}")
        print(f"  chronos RMSE        = {test_metrics['rmse']:.3f}")
        print(f"  beats naive?        = {beats_naive}  (hard gate)")
        print(f"  beats best baseline?= {beats_best_baseline}  (success goal)")

    result = {
        'model':            'chronos_bolt',
        'variant':          loaded_variant,
        'context_length':   CONTEXT_LENGTH,
        'train_end':        train_end,
        'val_end':          val_end,
        'test_end':         test_end,
        'setup_seconds':    setup_seconds,
        'test_metrics':     test_metrics,
        'baselines':        base_metrics,
        'beats_naive':      beats_naive,
        'beats_best_baseline': beats_best_baseline,
    }

    if save:
        if not beats_naive:
            raise RuntimeError(
                f"Chronos RMSE {test_metrics['rmse']:.3f} did not beat "
                f"naive RMSE {naive_rmse:.3f}. Refusing to save."
            )
        os.makedirs(MODELS_DIR, exist_ok=True)
        with open(EVAL_PATH, 'w') as f:
            json.dump(result, f, indent=2)
        if verbose:
            print(f"\n  Saved eval -> {EVAL_PATH}")

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', default=DEFAULT_VARIANT,
                        choices=[DEFAULT_VARIANT, FALLBACK_VARIANT])
    parser.add_argument('--no-save', action='store_true')
    args = parser.parse_args()
    run(variant=args.variant, verbose=True, save=not args.no_save)
