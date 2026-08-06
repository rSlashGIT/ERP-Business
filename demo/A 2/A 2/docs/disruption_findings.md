# Phase 4.5 — Disruption Findings (3-way honest interpretation)

## Setup recap

Three policies, six disruption scenarios, 50 paired episodes per
(scenario, policy) cell, 90-day episodes, all on the held-out 20 % of
`data/processed/m5_clean.csv`, base seed 42.

| Key | Source | Warehouse `(s, S)` | Store theta |
|---|---|---|---|
| `default_classical` | builtin (legacy) | `(1000, 3000)` | `default_theta24` per store |
| `grid_tuned_classical` | [models/grid_tuned_classical_theta.npy](../models/grid_tuned_classical_theta.npy) | `(200, 300)` | shared, `s_base = 0.08`, `S_base = 2.08` (encodes physical `s = 120`, action-index `2 = 150` u order) |
| `cmaes_pernode_robust` | [models/network_best_theta_pernode_robust.npy](../models/network_best_theta_pernode_robust.npy) | `(305.1, 940.6)` | per-node theta26 from Phase 2.8 |

`grid_tuned_classical` was selected by exhaustive search over 1,728
candidates (8 × 6 × 6 × 6) on the **first 60 %** of `m5_clean.csv` only,
maximising mean training-split profit subject to the same 80 %
per-store SL floor that the CMA-ES policy was trained under. The grid
search code is [src/grid_tuned_baseline.py](../src/grid_tuned_baseline.py)
and the full grid record is in
[models/grid_tuned_classical.json](../models/grid_tuned_classical.json).

## Pairwise scenario wins out of 6

```
  cmaes      vs default     : profit 6/6   tail 6/6
  cmaes      vs grid_tuned  : profit 0/6   tail 0/6     ← cmaes is dominated
  grid_tuned vs default     : profit 6/6   tail 6/6
```

## What this actually says

Phase 4 reported "CMA-ES Pareto-dominates classical 6 / 6 on every
scenario, including `major_supplier` and `compound_crisis`" — and on
the legacy fixed-threshold baseline that was technically true. **It is
not the right finding.** Once we replace the mis-calibrated default
with a grid-tuned classical policy that has seen the *same* training
data the CMA-ES model trained on, the picture inverts:

- `grid_tuned_classical` wins **mean profit in 6 / 6 scenarios** vs
  CMA-ES, by a margin of **+ $3,491 to + $4,465** per 90-day episode
  (worst margin under `demand_spike`; best under `major_supplier`).
- `grid_tuned_classical` wins **tail risk (5th-percentile) in 6 / 6
  scenarios** vs CMA-ES, by a margin of **+ $4,343 to + $9,072**, with
  the largest gaps concentrated in the supply-side scenarios:
  `major_supplier` (+ $9,072) and `compound_crisis` (+ $8,399).
- `grid_tuned_classical` also runs at **~ 99.7 % calm service level**,
  vs CMA-ES at **90.9 %**, so the grid policy is also dominant on the
  feasibility metric the CMA-ES robust constraint was meant to enforce.

The "expected vs tail" Pareto trade-off the original Phase 3 / Phase 4
PRD anticipated does not appear here either; **grid-tuned dominates
CMA-ES on both axes simultaneously and across every scenario**, so
there is no frontier separating the two policies in this experiment.

## Why does CMA-ES lose to a grid?

This is not the result the Phase-2.8 production lock anticipated. The
mechanics most consistent with the data:

1. **The warehouse `(s, S) = (200, 300)` cell sits at the corner of
   the feasible polytope.** It is the leanest combination the grid
   searched (warehouse `s = 200`, gap = `100`, which clamps to the
   `WH_GAP_FLOOR = 100` floor). CMA-ES, optimising in the
   theta26 raw space with `transform_warehouse_params` mapping
   `(0, 0) → (400, 1000)` as its prior, drifted *toward* the corner
   (`(305.1, 940.6)`) but did not reach it. The robust-CMA penalty
   structure that drove the Phase 2.8 ≥ 80 % per-store SL floor
   discouraged the search from approaching the boundary further, even
   though the test data shows the corner is still feasible at ~ 99 %
   SL.
2. **The 6 context-weight slots in the per-node CMA-ES theta look like
   they cost more than they earn.** The grid policy zeroes those 6
   weights for every store and ends up with three identical 8-vec
   thetas; CMA-ES uses ~ 18 free parameters to fit per-store
   demand-forecast / demand-std / lead-time slopes, but each non-zero
   weight introduces day-to-day order variance that the warehouse pays
   for in holding cost without buying meaningful additional sales on
   clean M5 data.
3. **The CMA-ES robust loss aggregated over training disruptions, the
   grid loss did not.** Grid was tuned only on calm training data; it
   had no incentive to over-buffer for unseen disruptions. The clean
   M5 series is benign enough that "not over-buffering" turned out to
   be the better disruption strategy — disruption tail differences of
   ~ $4 k are small compared to the ~ $4 k / episode holding-cost gap
   between the two policies, so any policy that wins on holding cost
   in calm conditions also wins on tail under most disruptions.

## What this means for the project

The honest reading is that **Phase 2.8's locked production model is
strictly Pareto-dominated by a simple grid-tuned classical policy on
this evaluation harness**, and the cost-vs-tail-risk Pareto trade-off
the project intended to surface is **not visible** in clean-M5 data
under the current evaluation setup. The "CMA-ES wins on robustness"
narrative from Phase 4 was an artefact of the legacy default's
mis-calibrated thresholds, not a property of CMA-ES vs (s, S) policies
in general.

This does not mean CMA-ES has no value — it means the value is not
visible *here*, on this dataset, against this baseline. Plausible next
steps that would either restore the CMA-ES advantage or honestly
retire it in the final report:

- **Re-train the CMA-ES policy with the grid winner as warm start.**
  Initialise the search at `transform_warehouse_params⁻¹(200, 300)`
  and per-store theta `[0.08, 2.08, 0, 0, 0, 0, 0, 0]`, then let CMA-ES
  search outward. This isolates whether CMA-ES can find the corner if
  given a feasible initial point.
- **Stress-test on noisier or non-stationary demand.** Clean M5 for
  FOODS_3_090 is very stable (CV ≈ 0.4); the CMA-ES context weights
  may pay off on a series with structural shifts where a flat
  classical policy can't adapt.
- **Drop the `default_classical` baseline from the final report** and
  use `grid_tuned_classical` as the published baseline. Anchoring the
  CMA-ES win against a mis-calibrated baseline is not a defensible
  framing.
- **If the goal is a defensible publishable Pareto trade-off, accept
  the current result and report that on this data the classical
  family Pareto-dominates the learned policy, then either (a) re-scope
  Phase 5 toward a stronger learned baseline, or (b) report the
  negative result as the project's contribution.**
