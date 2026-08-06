# SmartStock — Fixes Applied (May 14, 2026)

This version of the project contains fixes to 4 bugs and 3 issues identified
during a deep frontend audit. The Python ML code, models, and data pipeline
are untouched.

## Bugs Fixed

### Bug #1: Hardcoded "+3.2% vs yesterday" on Dashboard
- **File:** `frontend/js/screens/dashboard.js`
- **Change:** Replaced string literal with `inventoryDeltaLabel(totalValue)`
  helper that stores yesterday's inventory in localStorage and computes
  the real delta on subsequent visits.

### Bug #2: Stress test color map mismatch
- **File:** `frontend/js/screens/stress.js`
- **Change:** `POLICY_COLORS` now includes the full key
  `'CMA-ES Per-Node (Robust)'` matching the data file. CMA-ES line
  renders in intended green color.

### Bug #3: Inventory "↓ X" label was misleading
- **File:** `frontend/js/screens/inventory.js`
- **Change:** Caption changed from `↓ ${reorder_point}` to
  `Reorder: ${reorder_point}` for clarity.

### Bug #4: accuracy_pct was mathematically nonsense
- **Files:** `frontend/data/comparison.json`, `frontend/js/screens/forecasts.js`,
  `frontend/js/screens/demo.js`
- **Old formula:** `accuracy_pct = 100 - rmse` (meaningless, RMSE has units)
- **New formula:** `accuracy_pct = (naive_rmse - model_rmse) / naive_rmse * 100`
- **Results now correctly show:**
  - Naive Baseline: 0.00% (by definition)
  - LightGBM: +0.51% (fails 5% gate)
  - N-HITS: +12.78% (passes)
  - Chronos-Bolt: +15.76% (passes — the winner)
- Model card hero numbers now show test RMSE (defensible primary metric)
  with subtitle showing improvement % vs naive.

## Issues Fixed

### Issue #1: SKU names were raw M5 IDs
- **Files:** `frontend/data/skus.json`, `frontend/data/alerts.json`,
  `frontend/data/history_seed.json`
- **Change:** All 30 SKUs now have friendly product names
  ("White Bread Loaf 400g", "Basmati Rice 5kg", etc.). Raw M5 IDs preserved
  in `id` and `m5_id` fields for traceability. Alerts and history use
  friendly names with parenthetical M5 IDs.

### Issue #2: Dead `overview.json` file
- **File:** `frontend/data/overview.json` (deleted)
- **Reason:** Dashboard computes its KPIs live; this file was loaded by
  nothing.

### Issue #3: Lower confidence values (61% vs older screenshot's 86%)
- **Decision:** Left honest values intact. Did NOT fabricate higher
  confidence to match older version. Lower values reflect genuine
  model uncertainty (50-200% spread between forecasting models on many
  SKUs).

## What Was NOT Changed

- All Python code in `src/`
- All models in `models/`
- All training and evaluation logic
- Real forecast data in `frontend/data/forecasts.json`
- Real inventory data in `frontend/data/inventory_today.json`
- Real disruption results
- The science. Only how it's displayed.

## What's Removed From This Archive (And Why)

To keep zip size manageable, these were excluded:

- `data/m5/` — Raw M5 dataset (430 MB). Re-download from Kaggle.
  See `data/m5/README_M5_DATA.txt` for instructions.
- `supply-chain-idss/node_modules/` — Reinstallable via `npm install`.
- `.git/` history — Re-clone the repo if needed.
- macOS metadata (`._*` and `.DS_Store` files)
- `.pytest_cache/`

Everything else needed to run the frontend, reproduce model evaluations,
and demo the project is included.

## How to Test

```bash
cd frontend
python -m http.server 8080
# Open http://localhost:8080 in browser
```

Walk through all 7 screens:
1. Dashboard — KPI shows "Current snapshot" on first load
2. Inventory — friendly product names, "Reorder: X" labels
3. Orders — friendly names in left pane
4. History — friendly names in table
5. Forecasts — RMSE as hero number, improvement % as subtitle,
   LightGBM correctly fails 5% gate
6. Stress Test — CMA-ES line in green (not purple)
7. Settings — no changes

If issues, restore from backup. Detailed audit report and changelog
included separately as txt files in the project root.
