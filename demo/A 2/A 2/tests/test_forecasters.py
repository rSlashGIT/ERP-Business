"""Phase 4.5 tests for demand forecasters.

Run with:
    python -m pytest tests/test_forecasters.py -v
    python tests/test_forecasters.py         (standalone)

Six tests total when all three models are built. LightGBM is implemented
first (tests 1–2); N-HITS tests (3–4) and Chronos tests (5–6) are added
as each model lands and are skipped cleanly until then.
"""

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from forecaster_eval import (                    # noqa: E402
    evaluate_baselines,
    load_m5_and_split,
    rolling_one_step_eval,
)


def _load_splits():
    y_tr, y_va, y_te, y_full = load_m5_and_split()
    train_end = len(y_tr)
    val_end   = train_end + len(y_va)
    test_end  = len(y_full)
    return y_full, train_end, val_end, test_end


# ───────────────── LightGBM ─────────────────

def test_lgbm_trains_and_predicts():
    """LightGBM trains on the 60% split, produces finite numeric
    predictions over the test window, and saves a reloadable model."""
    from forecaster_lgbm import LGBMForecaster, train_lgbm, MODEL_PATH

    y_full, train_end, val_end, test_end = _load_splits()
    info = train_lgbm(y_full, train_end, val_end,
                      n_estimators=50, early_stopping_rounds=10,
                      verbose=False)
    fc = LGBMForecaster(model=info['model'],
                        feature_names=info['feature_names'])

    # Sanity-check prediction shape and value domain.
    for t in (val_end, val_end + 50, test_end - 1):
        y_hat = fc.predict_next(y_full[:t])
        assert isinstance(y_hat, float), f"non-float prediction at t={t}"
        assert y_hat >= 0.0, f"negative prediction {y_hat} at t={t}"
        assert y_hat < 1e6,  f"runaway prediction {y_hat} at t={t}"

    # Save / reload round-trip.
    test_path = MODEL_PATH + '.test.joblib'
    fc.save(test_path)
    fc2 = LGBMForecaster.load(test_path)
    assert fc2.predict_next(y_full[:val_end]) == pytest.approx(
        fc.predict_next(y_full[:val_end]), rel=1e-6), "reloaded model drifted"
    os.remove(test_path)


def test_lgbm_beats_naive_baseline():
    """LightGBM RMSE on the held-out test split is strictly lower than
    the best naive/seasonal-naive/moving-avg baseline."""
    from forecaster_lgbm import LGBMForecaster, train_lgbm

    y_full, train_end, val_end, test_end = _load_splits()
    info = train_lgbm(y_full, train_end, val_end,
                      n_estimators=200, early_stopping_rounds=20,
                      verbose=False)
    fc = LGBMForecaster(model=info['model'],
                        feature_names=info['feature_names'])

    lgbm_metrics = rolling_one_step_eval(
        y_full=y_full,
        test_start=val_end,
        test_end=test_end,
        predict_fn=fc.predict_next,
    )
    baselines = evaluate_baselines(y_full, val_end, test_end)
    best_base_rmse = min(m['rmse'] for m in baselines.values())

    assert lgbm_metrics['rmse'] < best_base_rmse, (
        f"LightGBM RMSE {lgbm_metrics['rmse']:.3f} did not beat best "
        f"baseline RMSE {best_base_rmse:.3f}"
    )


# ───────────────── N-HITS ─────────────────

@pytest.mark.skipif(
    importlib.util.find_spec('neuralforecast') is None,
    reason='neuralforecast not installed',
)
def test_nhits_trains_and_predicts():
    """N-HITS trains on the 60% split with a tiny step budget and
    produces finite numeric predictions over the test window. This
    test uses max_steps=20 to stay under ~20 s on CPU; the full
    500-step training lives in src/forecaster_nhits.py's run()."""
    from forecaster_nhits import NHITSForecaster, train_nhits

    y_full, train_end, val_end, test_end = _load_splits()
    info = train_nhits(y_full, train_end, val_end,
                       max_steps=20,
                       val_check_steps=10,
                       early_stop_patience_steps=2,
                       verbose=False)
    fc = NHITSForecaster(nf=info['nf'], input_size=info['input_size'])
    for t in (val_end, val_end + 25, test_end - 1):
        y_hat = fc.predict_next(y_full[:t])
        assert isinstance(y_hat, float), f"non-float prediction at t={t}"
        assert y_hat >= 0.0, f"negative prediction {y_hat} at t={t}"
        assert y_hat < 1e6,  f"runaway prediction {y_hat} at t={t}"


@pytest.mark.skipif(
    importlib.util.find_spec('neuralforecast') is None,
    reason='neuralforecast not installed',
)
def test_nhits_beats_naive_baseline():
    """N-HITS test RMSE must strictly beat the naive baseline. We use
    the saved evaluation JSON if present (avoids a 15+ second retrain
    under pytest); if missing we retrain on the spot with max_steps=100
    which is enough to clear the gate on this series."""
    import json
    from forecaster_eval import evaluate_baselines
    from forecaster_nhits import EVAL_PATH, NHITSForecaster, train_nhits

    y_full, train_end, val_end, test_end = _load_splits()
    baselines = evaluate_baselines(y_full, val_end, test_end)
    naive_rmse = baselines['naive']['rmse']

    if os.path.exists(EVAL_PATH):
        with open(EVAL_PATH) as f:
            blob = json.load(f)
        nhits_rmse = blob['test_metrics']['rmse']
    else:
        info = train_nhits(y_full, train_end, val_end,
                           max_steps=100,
                           val_check_steps=25,
                           early_stop_patience_steps=3,
                           verbose=False)
        fc = NHITSForecaster(nf=info['nf'], input_size=info['input_size'])
        metrics = rolling_one_step_eval(
            y_full=y_full,
            test_start=val_end,
            test_end=test_end,
            predict_fn=fc.predict_next,
        )
        nhits_rmse = metrics['rmse']

    assert nhits_rmse < naive_rmse, (
        f"N-HITS RMSE {nhits_rmse:.3f} did not beat naive RMSE {naive_rmse:.3f}"
    )


# ───────────────── Chronos-Bolt ─────────────────

@pytest.mark.skipif(
    importlib.util.find_spec('chronos') is None,
    reason='chronos-forecasting not installed',
)
def test_chronos_zeroshot_predicts():
    """Chronos-Bolt loads zero-shot and produces finite numeric
    predictions over the test window. No training step."""
    from forecaster_chronos import ChronosForecaster, load_pipeline

    y_full, train_end, val_end, test_end = _load_splits()
    pipe, loaded_variant = load_pipeline()
    fc = ChronosForecaster(pipe=pipe, variant=loaded_variant)
    for t in (val_end, val_end + 25, test_end - 1):
        y_hat = fc.predict_next(y_full[:t])
        assert isinstance(y_hat, float), f"non-float prediction at t={t}"
        assert y_hat >= 0.0, f"negative prediction {y_hat} at t={t}"
        assert y_hat < 1e6,  f"runaway prediction {y_hat} at t={t}"


@pytest.mark.skipif(
    importlib.util.find_spec('chronos') is None,
    reason='chronos-forecasting not installed',
)
def test_chronos_beats_naive_baseline():
    """Chronos-Bolt test RMSE must strictly beat the naive baseline.
    Prefers the saved evaluation JSON to avoid re-running the 389-day
    rolling loop under pytest; otherwise runs it fresh."""
    import json
    from forecaster_chronos import (
        ChronosForecaster, EVAL_PATH, MIN_HISTORY, load_pipeline,
    )

    y_full, train_end, val_end, test_end = _load_splits()
    baselines = evaluate_baselines(y_full, val_end, test_end)
    naive_rmse = baselines['naive']['rmse']

    if os.path.exists(EVAL_PATH):
        with open(EVAL_PATH) as f:
            blob = json.load(f)
        chronos_rmse = blob['test_metrics']['rmse']
    else:
        pipe, loaded_variant = load_pipeline()
        fc = ChronosForecaster(pipe=pipe, variant=loaded_variant)
        metrics = rolling_one_step_eval(
            y_full=y_full,
            test_start=val_end,
            test_end=test_end,
            predict_fn=fc.predict_next,
            min_history=MIN_HISTORY,
        )
        chronos_rmse = metrics['rmse']

    assert chronos_rmse < naive_rmse, (
        f"Chronos RMSE {chronos_rmse:.3f} did not beat naive RMSE {naive_rmse:.3f}"
    )


# ───────────────── standalone runner ─────────────────

if __name__ == '__main__':
    import traceback
    tests = [
        test_lgbm_trains_and_predicts,
        test_lgbm_beats_naive_baseline,
        test_nhits_trains_and_predicts,
        test_nhits_beats_naive_baseline,
        test_chronos_zeroshot_predicts,
        test_chronos_beats_naive_baseline,
    ]
    passed = failed = skipped = 0
    for t in tests:
        try:
            t()
            print(f'  PASS  {t.__name__}')
            passed += 1
        except pytest.skip.Exception as e:
            print(f'  SKIP  {t.__name__}  ({e})')
            skipped += 1
        except Exception:
            print(f'  FAIL  {t.__name__}')
            traceback.print_exc()
            failed += 1
    print(f'\n{passed} passed, {skipped} skipped, {failed} failed')
    sys.exit(0 if failed == 0 else 1)
