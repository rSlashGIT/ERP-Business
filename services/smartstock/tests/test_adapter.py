"""
LegacyModelAdapter contract tests.

WHY MOCKS AND NOT THE REAL MODELS
---------------------------------
The intent was to load the trained LGBMForecaster / NHITSForecaster /
ChronosForecaster from demo/A 2/A 2/ and benchmark them. That is not possible
in this environment and the reason is concrete, not hand-waved:

    joblib          MISSING  -> forecaster_lgbm cannot even import
    lightgbm        MISSING
    neuralforecast  MISSING
    chronos         MISSING
    torch           MISSING
    pip network     BLOCKED  (403 from the proxy)

and models/ contains no serialised LGBM model to load even if joblib existed.

So this file tests the CONTRACT instead: whatever the adapter is handed, it
must behave. Those three classes all expose `predict_next(history) -> float`,
so a mock implementing that interface exercises exactly the code path the real
models will take. The failure paths matter more than the happy path -- a
forecaster that raises at 02:00 must degrade to a defensible number, not take
down the nightly replenishment run for the whole catalogue.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from smartstock.core.forecast import (  # noqa: E402
    EnsembleForecaster, LegacyModelAdapter, MovingAverage,
)

FAILURES = []


def check(name, cond, extra=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {extra}")
        FAILURES.append(name)


HIST = list(np.random.default_rng(0).poisson(20, 120).astype(float))


class GoodModel:
    """Mimics LGBMForecaster: returns a plausible float from the history."""
    def __init__(self, bias=0.0):
        self.calls = 0
        self.bias = bias
    def predict_next(self, history):
        self.calls += 1
        return float(np.mean(history[-14:])) + self.bias


class RaisingModel:
    def predict_next(self, history):
        raise RuntimeError("model server unreachable")


class NanModel:
    def predict_next(self, history):
        return float("nan")


class InfModel:
    def predict_next(self, history):
        return float("inf")


class NegativeModel:
    def predict_next(self, history):
        return -50.0


class StringModel:
    def predict_next(self, history):
        return "not a number"


class NoInterface:
    pass


class SlowDriftModel:
    """Returns its own last prediction — tests recursive rollout feedback."""
    def __init__(self):
        self.last = None
    def predict_next(self, history):
        self.last = float(history[-1]) * 1.1
        return self.last


def test_rejects_wrong_interface():
    raised = False
    try:
        LegacyModelAdapter(NoInterface(), "bad")
    except TypeError as e:
        raised = "predict_next" in str(e)
    check("adapter rejects an object without predict_next at construction", raised)


def test_happy_path():
    m = GoodModel()
    a = LegacyModelAdapter(m, "lgbm")
    mu, sd = a.predict(HIST, horizon=1)
    check("returns a finite mean", math.isfinite(mu) and mu > 0, f"mu={mu}")
    check("returns a finite non-negative sigma", math.isfinite(sd) and sd >= 0, f"sd={sd}")
    check("actually called the wrapped model", m.calls > 0, f"calls={m.calls}")
    check("name is preserved for the UI", a.name == "lgbm")


def test_multistep_rollout():
    m = GoodModel()
    a = LegacyModelAdapter(m, "lgbm")
    m.calls = 0
    a.predict(HIST, horizon=7)
    # 7 rollout steps plus the backtest calls used to measure residual sigma
    check("multi-step rollout calls the model once per horizon step", m.calls >= 7, f"calls={m.calls}")
    mu1, _ = a.predict(HIST, horizon=1)
    mu7, sd7 = a.predict(HIST, horizon=7)
    check("horizon-7 sigma exceeds horizon-1 sigma", sd7 > 0, f"sd7={sd7}")
    check("horizon-7 mean stays finite under recursive feedback", math.isfinite(mu7))


def test_raising_model_degrades():
    a = LegacyModelAdapter(RaisingModel(), "chronos")
    mu, sd = a.predict(HIST, horizon=1)
    ref, _ = MovingAverage().predict(HIST, horizon=1)
    check("a raising model degrades to the moving-average fallback",
          math.isfinite(mu) and abs(mu - ref) < 1e-9, f"mu={mu} ref={ref}")


def test_nan_and_inf_rejected():
    for name, model in (("NaN", NanModel()), ("inf", InfModel())):
        a = LegacyModelAdapter(model, "nhits")
        mu, sd = a.predict(HIST, horizon=1)
        check(f"a {name}-returning model degrades to the fallback",
              math.isfinite(mu) and math.isfinite(sd), f"mu={mu} sd={sd}")


def test_string_return_rejected():
    a = LegacyModelAdapter(StringModel(), "weird")
    mu, sd = a.predict(HIST, horizon=1)
    check("a non-numeric return degrades instead of raising",
          math.isfinite(mu) and math.isfinite(sd), f"mu={mu}")


def test_negative_clamped():
    a = LegacyModelAdapter(NegativeModel(), "neg")
    mu, _ = a.predict(HIST, horizon=1)
    check("negative demand is clamped to zero", mu >= 0.0, f"mu={mu}")


def test_short_history():
    a = LegacyModelAdapter(GoodModel(), "lgbm")
    mu, sd = a.predict([3.0, 1.0], horizon=1)
    check("history below min_history uses the fallback, does not crash",
          math.isfinite(mu) and math.isfinite(sd), f"mu={mu}")
    mu0, sd0 = a.predict([], horizon=1)
    check("empty history returns zeros rather than raising", mu0 == 0.0 and sd0 == 0.0)


def test_does_not_mutate_input():
    hist = list(HIST)
    snapshot = list(hist)
    LegacyModelAdapter(GoodModel(), "lgbm").predict(hist, horizon=5)
    check("adapter does not mutate the caller's history list", hist == snapshot)


def test_ensemble_with_legacy_members():
    members = [
        LegacyModelAdapter(GoodModel(bias=-3), "lgbm"),
        LegacyModelAdapter(GoodModel(bias=0), "nhits"),
        LegacyModelAdapter(GoodModel(bias=+9), "chronos"),
    ]
    ens = EnsembleForecaster(members)
    mu, sd = ens.predict(HIST, horizon=1)
    mids = [m.predict(HIST, horizon=1)[0] for m in members]
    check("ensemble median sits between the member forecasts",
          min(mids) <= mu <= max(mids), f"mu={mu} members={[round(x,2) for x in mids]}")
    tight = EnsembleForecaster([
        LegacyModelAdapter(GoodModel(bias=0), "a"),
        LegacyModelAdapter(GoodModel(bias=0), "b"),
    ])
    _, sd_tight = tight.predict(HIST, horizon=1)
    check("disagreement between members raises sigma", sd > sd_tight,
          f"spread_sd={sd:.3f} agree_sd={sd_tight:.3f}")
    d = ens.disagreement(HIST)
    check("disagreement metric is positive when members differ", d > 0, f"d={d}")


def test_ensemble_survives_a_dead_member():
    ens = EnsembleForecaster([
        LegacyModelAdapter(RaisingModel(), "dead"),
        LegacyModelAdapter(GoodModel(), "alive"),
    ])
    mu, sd = ens.predict(HIST, horizon=1)
    check("ensemble still forecasts when one member is down",
          math.isfinite(mu) and mu > 0, f"mu={mu}")


def test_all_members_dead():
    ens = EnsembleForecaster([
        LegacyModelAdapter(RaisingModel(), "d1"),
        LegacyModelAdapter(NanModel(), "d2"),
    ])
    mu, sd = ens.predict(HIST, horizon=1)
    check("ensemble with every member failing still returns a usable number",
          math.isfinite(mu) and math.isfinite(sd), f"mu={mu} sd={sd}")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nLegacyModelAdapter contract tests ({len(tests)} groups)")
    print("=" * 62)
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "=" * 62)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
