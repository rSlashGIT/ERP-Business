"""
Proves the RUNTIME forecast path executes real models, not test doubles.

WHY THIS TEST EXISTS
--------------------
tests/test_adapter.py drives LegacyModelAdapter with mock objects, which can
give the impression that SmartStock forecasts through a mock. It does not.
LegacyModelAdapter is an OPTIONAL plug-in for the LightGBM / N-HiTS / Chronos
models from the legacy repo; it is never constructed unless a caller explicitly
passes one in via `extra_models`.

This test walks the real path -- ReplenishmentRequest -> generate() ->
_derive_demand -> forecast_for -> auto_select -> model.predict -- with
instrumentation, and asserts on what actually got executed. If someone ever
wires a stub into the default path, this fails.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from smartstock.contracts import ReplenishmentRequest, SkuNodeState, SupplierRef  # noqa: E402
from smartstock.core import forecast as F  # noqa: E402
from smartstock.core.recommend import PolicyStore, generate  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {extra}")
        FAILURES.append(name)


REAL_MODELS = {"MovingAverage", "CrostonSBA", "SeasonalDamped", "Theta",
               "BaggedMedian", "EnsembleForecaster", "CalendarAdjusted"}


def _series(kind: str, n: int = 200) -> list:
    rng = np.random.default_rng(11)
    if kind == "smooth":
        return list((20 + 4 * np.sin(np.arange(n) / 7 * 2 * np.pi) + rng.normal(0, 2, n)).clip(0))
    if kind == "intermittent":
        d = rng.poisson(6, n).astype(float)
        d[rng.random(n) < 0.45] = 0.0
        return list(d)
    if kind == "lumpy":
        d = np.zeros(n)
        hits = rng.random(n) < 0.18
        d[hits] = rng.lognormal(2.4, 1.3, hits.sum())
        return list(d)
    if kind == "sparse":
        d = np.zeros(n)
        hits = rng.random(n) < 0.15
        d[hits] = rng.poisson(9, hits.sum())
        return list(d)
    d = rng.poisson(14, n).astype(float)          # erratic
    d[rng.random(n) < 0.25] *= rng.uniform(3, 7)
    return list(d)


def test_auto_select_returns_concrete_models():
    for kind in ("smooth", "intermittent", "erratic", "lumpy", "sparse"):
        fc = F.auto_select(_series(kind))
        cls = type(fc).__name__
        check(f"auto_select({kind}) -> a real model class ({cls})", cls in REAL_MODELS, cls)
        check(f"auto_select({kind}) is not a LegacyModelAdapter",
              not isinstance(fc, F.LegacyModelAdapter))
        mu, sd = fc.predict(_series(kind), horizon=1)
        check(f"{kind} model produces a finite forecast",
              np.isfinite(mu) and np.isfinite(sd) and mu >= 0, f"mu={mu} sd={sd}")


def test_generate_never_constructs_an_adapter():
    """Instrument LegacyModelAdapter.__init__ and assert it is never called."""
    calls = {"n": 0}
    original = F.LegacyModelAdapter.__init__

    def spy(self, model, name, min_history=F.MIN_HISTORY):
        calls["n"] += 1
        return original(self, model, name, min_history)

    F.LegacyModelAdapter.__init__ = spy  # type: ignore[method-assign]
    try:
        items = [
            SkuNodeState(sku_id=f"SKU-{k}", node_id="DC-01", on_hand=10.0,
                         unit_cost=4.0, unit_price=9.0, demand_history=_series(k),
                         supplier=SupplierRef(supplier_id="S1", contract_lead_days=6.0))
            for k in ("smooth", "intermittent", "erratic", "lumpy")
        ]
        resp = generate(
            ReplenishmentRequest(run_id="nomock", as_of_date="2026-08-04", items=items),
            PolicyStore(),
        )
    finally:
        F.LegacyModelAdapter.__init__ = original  # type: ignore[method-assign]

    check("generate() constructed zero LegacyModelAdapters", calls["n"] == 0, f"calls={calls['n']}")
    check("generate() produced real recommendations",
          resp.stats["lines_recommended"] > 0, resp.stats)


def test_models_actually_execute():
    """Spy on each model's predict to prove it ran, not that it merely existed."""
    executed = set()
    originals = {}
    for name in ("MovingAverage", "CrostonSBA", "SeasonalDamped", "Theta", "BaggedMedian"):
        klass = getattr(F, name)
        originals[name] = klass.predict

        def make(n, orig):
            def wrapped(self, history, horizon=1, *a, **kw):
                executed.add(n)
                return orig(self, history, horizon, *a, **kw)
            return wrapped

        klass.predict = make(name, originals[name])  # type: ignore[method-assign]
    try:
        for kind in ("smooth", "intermittent", "erratic", "lumpy"):
            F.forecast_for(_series(kind), horizon=1)
    finally:
        for name, orig in originals.items():
            getattr(F, name).predict = orig  # type: ignore[method-assign]

    check("real model .predict() bodies executed during forecast_for",
          len(executed) >= 3, f"executed={sorted(executed)}")
    check("SeasonalDamped executed (smooth/intermittent path)", "SeasonalDamped" in executed)


def test_forecast_varies_with_data():
    """A stub returning a constant would pass a smoke test; this catches it."""
    a, _, _ = F.forecast_for(_series("smooth"), horizon=1)
    b, _, _ = F.forecast_for([x * 3 for x in _series("smooth")], horizon=1)
    check("forecast scales with input magnitude (not a constant stub)",
          b > a * 2.0, f"a={a:.2f} b={b:.2f}")
    flat = [7.0] * 200
    m, s, _ = F.forecast_for(flat, horizon=1)
    check("constant series -> forecast near the constant", abs(m - 7.0) < 1.5, f"mu={m}")
    check("constant series -> near-zero sigma", s < 1.0, f"sd={s}")


def test_no_test_doubles_imported_at_runtime():
    import smartstock.core.recommend as R
    src = open(R.__file__).read()
    for banned in ("Mock", "MagicMock", "unittest.mock", "FakeForecaster", "stub"):
        check(f"recommend.py does not reference '{banned}'", banned not in src)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nReal-model runtime proof ({len(tests)} groups)")
    print("=" * 62)
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "=" * 62)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
