# SmartStock — FROZEN

**Status: stable module. Bug fixes only.**
**Frozen at: v2.2.1 · 2026-08-04**

SmartStock has passed end-to-end verification on real retail data. Engineering
effort moves to the ERP. This module now accepts **only**:

- fixes for reproducible correctness bugs
- fixes for production incidents
- contract changes the ERP genuinely requires (with a version bump)

It does **not** accept: forecasting accuracy work, refactors, style changes,
speculative features, or new models. Current accuracy is adequate and was
explicitly declared out of scope.

---

## Verified baseline — regressions are measured against these numbers

Reproduce all of it with `make verify` from the repository root.

| Check | Command | Result |
|---|---|---|
| Engine assertions | `make test-engine` | 44/44 pass |
| Legacy-adapter contract | `make test-adapter` | 17/17 pass |
| Real-model runtime proof | `make test-nomocks` | 21/21 pass |
| Auth / roles / tenancy | `make test-security` | 35/35 pass |
| Cross-tenant route isolation | `make test-tenancy` | 34/34 pass |
| Migration chain | `make migrate-verify` | 15 tables, 14 indexes, upgrade→downgrade→upgrade clean |
| E2E, M5 Walmart 30 SKUs | `make validate-m5` | all pass, lag-1 autocorr +0.357 |
| E2E, different schema | `make validate-clean` | all pass |
| E2E, dirty apparel extract | `make validate-dirty` | all pass (2 expected warnings) |
| Inventory flow, live API | `make validate-flow` | 12/12 pass |

**Policy benchmark** (`python3 demo/run_demo.py --fit --generations 40`,
30 M5 SKUs, held-out window):

| Policy | Total cost | Fill rate | Worst SKU |
|---|---:|---:|---:|
| SmartStock (CMA-ES) | 8,383 | 99.90% | 95.32% |
| Classical (s,S) | 14,457 | 98.98% | 95.47% |
| Naive 7-day | 64,055 | 80.53% | 64.47% |

−42.0% cost vs classical at +0.92pp fill. Run-to-run spread across seeds is
roughly −40% to −44%; treat anything outside that band as a regression.

**Forecast accuracy** (`make bench-forecast`): MAE 5.221 vs 6.467 for a 28-day
rolling mean (+19.3%). Not to be improved further under the freeze.

---

## Fixes applied at freeze time

Three real defects surfaced by the end-to-end validation, all fixed:

1. **σ collapsing to zero** (`core/forecast.py`). M5 SKU `FOODS_3_448` sells on
   68.6% of days across 1,900 days but had a dead final month, so
   `MovingAverage(28)` returned σ = 0. Safety stock is `z · σ_DL`, so σ = 0
   meant **zero safety stock** and a guaranteed stockout when demand resumed.
   Fixed with a floor at half the 180-day spread when the short window is flat.

2. **Order-up-to collapsing onto the reorder point** (`core/policy.py`). On a
   slow mover behind an unreliable supplier (`FOODS_3_808`: 0.35/day, lead
   17.2 ± 5.8 → s = 142.7 against a 60-day cap of 21) the `max_cover_days`
   clamp forced `S == s`. An (s,S) policy with S == s orders exactly to its own
   trigger, so the next review re-triggers it — a stream of tiny orders each
   paying the fixed ordering cost. Fixed by guaranteeing one review period of
   cycle stock.

3. **Zero-demand guard** (`core/policy.py`). Fix 1 introduced this: a
   discontinued SKU got d̂ = 0 but σ > 0, producing s = z·σ_DL > 0 and
   **ordering 16 units of a dead line**. Now both targets collapse to zero when
   d̂ = 0. Verified that a live SKU and a resumed SKU still order.

---

## What is NOT verified

State these plainly; do not let them be assumed away.

1. **PostgreSQL has never run.** Migrations were verified by executing the op
   sequence against SQLite plus an AST coverage cross-check. SQLite does not
   enforce enum membership and cannot drop constraints. `alembic upgrade head`
   against real Postgres remains outstanding.
2. **`apps/web` has never been compiled.** `npm install` fails with a 403 from
   the registry in this environment. Every import resolves statically, but type
   errors are possible. `apps/console/index.html` is what actually runs.
3. **LightGBM / N-HiTS / Chronos have never been executed.** `joblib`,
   `lightgbm`, `neuralforecast`, `chronos` and `torch` are all absent and pip
   is blocked; `models/` holds no serialised LGBM file regardless.
   `LegacyModelAdapter` is contract-tested against mocks only. **This does not
   affect production forecasting** — see below.
4. **No new public dataset could be downloaded.** All outbound network is
   blocked. Validation used the M5 Walmart data already vendored in the repo
   (a real Kaggle competition dataset), a second differently-shaped file, and a
   synthetic dirty extract.
5. **Scale unverified above 10,000 SKUs.**
6. **No token revocation.** JWTs are stateless with an 8-hour TTL.

## Clarification: forecasting is not mocked

`tests/test_adapter.py` drives `LegacyModelAdapter` with mocks, which can look
like SmartStock forecasts through a mock. It does not.
`core/recommend.py` calls `forecast_for` → `auto_select` → concrete models
(`SeasonalDamped`, `Theta`, `BaggedMedian`, `CrostonSBA`, `MovingAverage`).
`LegacyModelAdapter` is an optional plug-in and is never constructed unless a
caller passes one via `extra_models`.

`tests/test_no_mocks.py` proves this at runtime: it instruments
`LegacyModelAdapter.__init__`, runs `generate()`, and asserts the constructor
was called **zero** times while real model `.predict()` bodies executed.
