# HANDOFF — ERP + SmartStock

**Read this file and you can explain, operate and continue the project without opening any
other file.** Written for a fresh model or engineer picking this up cold.

| | |
|---|---|
| **Status** | SmartStock FROZEN (bug fixes only). Engineering effort now on the ERP. |
| **Last verified** | 2026-08-04 |
| **Engine version** | `2.2.1` — **FROZEN**, see `services/smartstock/FROZEN.md` |
| **Test suite** | `make test` — 44 engine + 17 adapter + 21 no-mocks + 35 security + 58 tenancy + migration verifier, all passing |
| **Headline result** | 40% lower total cost than a classical (s,S) policy on held-out demand, at +1.15pp fill rate. Forecast MAE +19.3% vs the v2.0 baseline. |

---

## 1. What this is

A cloud-native ERP with Inventory, Procurement and Dashboard modules, integrated with
**SmartStock** — an inventory-optimisation microservice that generates draft Purchase Orders
using a CMA-ES-tuned continuous (s,S) policy under stochastic lead times.

The ERP is the system of record. SmartStock is a stateless advisor: it holds no business data,
receives everything it needs per request, and returns recommendations. **No AI recommendation
ever becomes a purchase order without a human decision.**

### The pipeline in one line

```
ERP tables → nightly Celery task → SmartStock /v1/recommendations:generate
          → Draft POs → human approval screen → PurchaseOrder rows
          → goods receipts → lead-time observations → next fit
```

That last arrow is the point: the system measures its own suppliers and gets better.

---

## 2. The four required upgrades — what changed and where

The legacy SmartStock (`A 2.zip`, `src/`) was a single-store research prototype driven by
batch CSVs. All four limitations named in the brief are resolved.

### 2.1 Multi-SKU / multi-echelon at scale ✅

**Was:** `multi_sku_network.py` looped in Python over SKUs × stores × days and carried
`PARAMS_PER_SKU = 10`, giving "30 SKUs × 10 = 300 dims".

**Problem with just scaling that up:** CMA-ES is O(n²) per generation with an O(n³) eigen
decomposition. At 10,000 SKUs you would need 100,000 dimensions. That is not a big
optimisation problem, it is an impossible one. Separately, most SKUs do not carry enough
demand signal to identify 10 free parameters — you would be fitting noise per SKU.

**Now:** two changes.

1. **Vectorised simulator** (`core/network.py`). The whole tensor `(population, sku, node, day)`
   is NumPy. The day loop is the only Python loop; population, SKU and node are vectorised axes.
   Measured throughput: **~720,000 SKU-days/second**.
2. **Segment-level parameter sharing** (`core/segmentation.py`). Policy parameters are fitted
   per *segment*, not per SKU. Segments come from Syntetos-Boylan demand classification on
   (ADI, CV²) crossed with a volume tercile:

   | | CV² < 0.49 | CV² ≥ 0.49 |
   |---|---|---|
   | **ADI < 1.32** | SMOOTH | ERRATIC |
   | **ADI ≥ 1.32** | INTERMITTENT | LUMPY |

   × {LOW, MID, HIGH} volume = **at most 12 segments × 10 params = 120 dimensions, forever.**
   Per-SKU behaviour still differs because the policy consumes each SKU's own forecast mean,
   forecast σ, lead-time mean and lead-time σ. Thin segments merge upward into their demand
   class, then into `global:default`.

   Asserted by test `test_segment_dimension_is_constant`: 50 → 500 → 5,000 SKUs all produce
   ≤ 120 dimensions.

### 2.2 Dynamic stochastic lead times ✅

**Was:** `environment.py: 'Lead times': 2.0`, `multi_echelon.py: LEAD_SUPPLIER_TO_WAREHOUSE = 5`,
`LEAD_WAREHOUSE_TO_STORE = 2`. Hard-coded constants.

**Now:** `core/leadtime.py`. Lead time is a fitted distribution per (supplier, SKU, node),
estimated from the ERP's own `lead_time_observations` table, which is materialised from
`goods_receipts` (`received_at − ordered_at`).

- **Shrinkage.** Real ERPs have 2–3 receipts per supplier-SKU. A raw sample σ over 2 points is
  noise. James-Stein style weight `w = n / (n + 5)` blends the empirical estimate toward the
  supplier's contractual lead time. `source` is reported as `empirical | shrunk | contract`
  and is shown in the UI, so a buyer can see when the model is guessing.
- **Variance enters the policy.** This is the substantive part:

  ```
  σ_DL = sqrt( E[L]·σ_d²  +  E[d]²·σ_L² )
  ```

  The second term is what fixing L threw away. For a supplier swinging 3–21 days it dominates,
  and a policy blind to it stocks out every time the supplier is late. (Silver, Pyke & Peterson,
  ch. 7.)
- **Sampling.** Moment-matched gamma, discretised to whole days, vectorised across the whole
  (SKU × node) grid.
- **Dirty data.** Negative lead times (receipt predating the PO) are *dropped*, not clamped —
  clamping silently biases the mean downward.

### 2.3 Continuous action space ✅

**Was:**
```python
return int(np.clip(round(qty), 0, 7))      # hybrid_policy.py — an ACTION INDEX
action_map = {0:0, 1:50, 2:150, 3:300, 4:500, 5:750, 6:1000, 7:1500}
```
The model could not say "order 214". It said "bucket 3", meaning 300. Two consequences:
quantisation error up to ±250 units on every order, and a **step-function fitness landscape** —
small parameter changes produced zero fitness change until a bucket boundary was crossed, which
is the landscape evolution strategies handle worst.

**Now:** `core/policy.py` emits a real-valued quantity, then projects business constraints:

```
raw   = max(0, S − inventory_position)   when IP ≤ s
     → cap at max_inventory_position (capacity / shelf life)
     → round UP to order_multiple (case pack)
     → MOQ: raise to MOQ only if shortfall ≥ ½·MOQ, else drop to 0
     → clip at max_order_qty
     → round to integer units
```
Verified live: raw `1227.85` → **1236** (103 × 12 case pack); raw `753.21` → **775** (31 × 25);
raw `2094.89` → **8** (capacity-bound).

The MOQ rule matters: blindly rounding a 1-unit shortfall up to a 1000-unit MOQ is how systems
accumulate dead stock.

### 2.4 Real-time API ✅

**Was:** read `data/processed/*.csv`, write `frontend/data/*.json`, static page reads files.
No way for another system to ask a question.

**Now:** `api/main.py` (FastAPI).

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/recommendations:generate` | Synchronous batch draft POs. **The nightly ERP call.** |
| POST | `/v1/policy:optimize` | Async CMA-ES refit → job handle |
| GET | `/v1/jobs/{id}` | Poll fit progress |
| GET | `/v1/policy` | Inspect fitted parameters (explainability) |
| PUT | `/v1/policy` | Hot-swap parameters, atomic write-then-rename |
| POST | `/v1/simulate` | What-if against a candidate policy |
| GET | `/healthz` `/readyz` `/metrics` | Ops |

**Concurrency:** generation runs in a threadpool (`run_in_threadpool`) so a 50k-line request
cannot block the event loop and starve health checks. Fits run in a `ProcessPoolExecutor`
addressed by job id — never inline a minutes-long fit into a request handler, because the ERP's
Celery task has a timeout and will retry, and you end up with four concurrent fits of the
same catalogue.

---

## 3. Additional defects found and fixed

Not in the brief, found while building. Each was a genuine bug.

1. **Policy used `on_hand`, not inventory position.** Legacy `hybrid_policy.act()` compared raw
   inventory to the reorder point (`position = inv`, with a comment "inventory_on_order not
   tracked by env"). With any lead time > 0 this re-orders stock that is already in transit —
   the classic double-ordering bug. Now `IP = on_hand + on_order − backorder`.
   Test: `test_inventory_position_not_on_hand`.
2. **Scatter into a non-contiguous view.** `pipeline[:, :, 1:, :].reshape(...)` copies, so
   `np.add.at` wrote into a discarded temporary. Every replenishment order silently vanished and
   fill rate sat at 1%. Fixed by scattering into the base array with pre-built index grids.
3. **No common random numbers.** Each candidate policy drew its own lead-time realisation, so
   two *identical* parameter vectors scored differently. CMA-ES is rank-based, so this was pure
   comparison noise. Fixing it (one shared draw per generation) improved out-of-sample cost from
   **12,068 → 9,841**. Found by a test asserting determinism.
4. **`cma` dependency removed.** CMA-ES reimplemented in ~200 lines of NumPy
   (`optim/cmaes.py`): weighted recombination, rank-1 + rank-μ covariance update, CSA step-size
   control, lazy eigen refresh, NaN-tolerant `tell()`. Validated: Rosenbrock-10D → 7.9e-20,
   sphere-50D → 5.8e-19.

---

## 4. Architecture and tech stack

```
┌────────────────────┐     REST/JSON      ┌──────────────────────┐
│  apps/web (React)  │ ─────────────────► │  services/erp-api    │
│  PO Approval UI    │                    │  FastAPI + SQLAlchemy│
└────────────────────┘                    └──────┬───────────────┘
                                                 │ asyncpg
                                          ┌──────▼──────┐
                                          │ PostgreSQL16│  system of record
                                          └─────────────┘
        ┌──────────────┐  Celery/Redis           │
        │ beat + worker│ ◄───────────────────────┘
        └──────┬───────┘
               │ httpx (retry + circuit breaker)
        ┌──────▼──────────────────┐
        │ services/smartstock     │  stateless
        │ FastAPI + NumPy         │  holds only fitted policy params
        └─────────────────────────┘
```

| Layer | Choice | Why |
|---|---|---|
| Frontend | React 18 + TypeScript + Vite | Approval screen is form-heavy and state-heavy; TS catches contract drift at build time |
| ERP API | FastAPI + SQLAlchemy 2.0 (async) | Same language as the engine; async matters because this service is I/O-bound |
| Database | PostgreSQL 16 | `NUMERIC` for money, `JSONB` for rationale, real constraints |
| Queue | Celery + Redis | `acks_late` + idempotent tasks; beat for the nightly cadence |
| Engine | FastAPI + NumPy, **no other deps** | Deployable air-gapped; no `cma`, no scipy, no torch |

**Why SmartStock is a separate service:** different scaling axis (CPU-bound vs I/O-bound),
different deploy cadence (policy changes weekly, ERP changes daily), and hard failure
isolation — if the engine dies the ERP keeps working and procurement falls back to manual POs.

**Communication:** synchronous HTTP for generation (sub-second, must be transactional with the
run record); async job + polling for fits (minutes). `clients/smartstock.py` wraps every call
with timeout, jittered exponential backoff on retryable statuses only, and a circuit breaker
(5 consecutive failures → open, 60s half-open probe).

---

## 5. Database schema

`services/erp-api/app/db/models.py`. Design rules, each enforced:

1. **Money and quantities are `NUMERIC(18,4)`, never float.** A float unit cost compounds into
   a wrong PO total and procurement stops trusting the system.
2. **Stock truth is an append-only ledger.** `stock_movements` is immutable (no `updated_at`,
   no soft delete); corrections are compensating rows. `inventory_levels` is a *materialised
   cache*, reconciled every 15 minutes. Drift is logged as an alert, not silently patched.
3. **`lead_time_observations` is a table, not a view.** SmartStock reads it every run; it must
   be O(1) per (supplier, SKU), not a join across full PO history.
4. **Provenance is retained forever.** `ai_recommended_qty` sits beside `ordered_qty` on every
   PO line. `policy_parameters` is versioned and append-only so any historical recommendation
   can be re-derived.

| Table | Role |
|---|---|
| `product_styles` | Apparel style: the parent of a size x colour grid. HSN lives here; the GST *rate* does not — Indian garments are 5% below Rs 2,500/piece and 18% above, so the slab is derived per variant at billing time |
| `products` | The sellable VARIANT and the atomic stock-keeping unit. One row = one size/colour = one barcode = one stock balance. `style_id`/`size`/`colour`/`barcode` are nullable so non-apparel tenants are unaffected |
| `locations` | Network nodes. `parent_id` self-reference *is* the echelon topology |
| `suppliers` | Contract lead days + CV (the **prior**) |
| `supplier_products` | MOQ, order multiple, max order, preferred flag |
| `inventory_levels` | on_hand / on_order / reserved / backorder + s, S, safety stock |
| `stock_movements` | **Append-only ledger. Source of truth.** |
| `demand_history` | Daily buckets. `was_stocked_out` flags censored demand |
| `purchase_orders` / `_lines` | With `ai_recommended_qty` vs `ordered_qty` |
| `goods_receipts` | Source of every lead-time observation |
| `lead_time_observations` | Materialised (supplier, product) lead-time facts |
| `replenishment_runs` | One nightly invocation; unique on (run_date, triggered_by) |
| `recommendations` | AI advice + human decision, kept even when rejected |
| `policy_parameters` | Versioned segment parameters |
| `audit_log` | Who changed what, when |

**On `demand_history.was_stocked_out`:** demand ≠ sales. If you were out of stock, sales
understate demand, and a model trained on sales learns to stay out of stock. Censoring must be
flagged.

---

## 6. API contracts

Single source of truth: `services/smartstock/smartstock/contracts.py`.

### ERP → SmartStock

```jsonc
POST /v1/recommendations:generate
{
  "run_id": "3f2a...", "as_of_date": "2026-08-04",
  "review_period_days": 1, "service_level_target": 0.95,
  "items": [{
    "sku_id": "FOODS_3_090", "node_id": "DC-01",
    "on_hand": 120, "on_order": 0, "backorder": 0,
    "unit_cost": 1.20, "unit_price": 2.00,
    "demand_history": [31, 28, 0, 44, ...],   // OR forecast_mean + forecast_sigma
    "supplier": { "supplier_id": "SUP-ACME", "contract_lead_days": 5,
                  "contract_lead_cv": 0.35 },
    "lead_time_observations": [5, 7, 6, 9, 6, 8, 14, 6],   // days, from goods receipts
    "constraints": { "moq": 100, "order_multiple": 12,
                     "max_order_qty": null, "max_inventory_position": 250000,
                     "shelf_life_days": 90 }
  }]
}
```

If both `demand_history` and `forecast_mean` are supplied, **the forecast wins**; history is
used only for segmentation. A forecast with no `forecast_sigma` gets σ = √mean (Poisson-ish)
rather than 0 — σ = 0 drives safety stock to zero and guarantees stockouts.

### SmartStock → ERP

```jsonc
{
  "run_id": "3f2a...", "policy_version": "fit-a91c2e4d",
  "engine_version": "2.0.0", "generated_at": "2026-08-04T02:00:11Z",
  "draft_purchase_orders": [{
    "draft_po_id": "DPO-3f2a-SUP-ACME-DC-01",
    "supplier_id": "SUP-ACME", "node_id": "DC-01",
    "expected_delivery_date": "2026-08-09", "total_value": 1491.20, "line_count": 2,
    "lines": [{
      "sku_id": "FOODS_3_090", "node_id": "DC-01",
      "recommended_qty": 1236,            // EXACT units — continuous action space
      "unconstrained_qty": 1227.85,       // pre-MOQ / pre-rounding, for transparency
      "unit_cost": 1.20, "line_value": 1483.20,
      "urgency": "critical",              // critical|high|medium|low|none
      "action": "order",                  // order|hold|review
      "confidence": 0.80,
      "rationale": {
        "reorder_point": 334.2, "order_up_to": 1348.1,
        "safety_stock": 149.3, "cycle_stock": 1013.9,
        "inventory_position": 120.0,
        "demand_over_leadtime": 213.1, "sigma_demand_over_leadtime": 57.0,
        "lead_time_mean_days": 7.0, "lead_time_std_days": 2.6,
        "lead_time_source": "shrunk",     // empirical|shrunk|contract|default
        "implied_service_level": 0.9951,
        "days_of_cover_before": 3.9, "days_of_cover_after": 44.6,
        "projected_stockout_day": 3, "segment": "smooth:high",
        "binding_constraint": "order_multiple",
        "explanation": "Demand 30.4/day (sigma 4.8); supplier lead 7.0+/-2.6d from shrunk (8 receipts)..."
      },
      "warnings": []
    }]
  }],
  "skipped": [{"sku_id": "", "reason": "missing sku_id or node_id"}],
  "stats": {"items_received": 90, "lines_recommended": 61, "items_held": 28,
            "draft_po_count": 10, "total_value": 31811.4, "critical_lines": 33}
}
```

**`rationale` is mandatory, not decorative.** An unexplained AI quantity gets rejected wholesale
the second time a buyer sees it. Every field above is rendered in the approval screen.

### ERP internal

```
GET  /api/v1/procurement/recommendations?status=pending&urgency=critical
POST /api/v1/procurement/recommendations/decide   {decisions:[{recommendation_id,action,final_qty,note}], actor}
GET  /api/v1/procurement/purchase-orders
GET  /api/v1/procurement/variance                 # AI vs human override report
GET  /api/v1/inventory?location_code=DC-01&below_reorder=true
POST /api/v1/inventory/adjustments
GET  /api/v1/dashboard
```

---

## 7. Background task loop

`services/erp-api/app/tasks/celery_app.py`

| Task | Schedule | Purpose |
|---|---|---|
| `erp.replenishment.nightly` | `02:00` daily | ERP state → SmartStock → draft POs |
| `erp.policy.refit` | `03:30` Sunday | CMA-ES refit on 12 months of demand |
| `erp.inventory.reconcile` | every 15 min | Rebuild `inventory_levels` from the ledger |
| `erp.leadtime.materialise` | hourly | New goods receipts → lead-time observations |

**02:00** is after end-of-day close (today's demand is in `demand_history`) and before buyers
arrive (the queue is waiting for them). The refit runs at a different hour so a minutes-long fit
never collides with the nightly generate.

**Idempotency is mandatory.** `acks_late=True` + `reject_on_worker_lost=True` means a task
survives a worker kill and *will* be redelivered. `run_replenishment` is unique on
`(run_date, triggered_by)` and returns the existing run rather than double-creating
recommendations. Duplicated draft POs destroy trust in the queue immediately.

---

## 8. The policy — exact math

Ten parameters per segment, all human-readable (this is what makes the "why?" panel possible
without a post-hoc explainer model):

| # | Name | Range | Meaning |
|---|---|---|---|
| 0 | `z_safety` | 0–4 | Safety factor on σ_DL (≈ service level) |
| 1 | `lt_bias` | 0.5–2 | Multiplier on E[L]; learns systematic supplier optimism |
| 2 | `w_sigma_lt` | 0–2 | Extra safety per unit of lead-time CV |
| 3 | `w_trend` | −1–2 | Responsiveness to demand trend |
| 4 | `cover_days` | 1–45 | Cycle stock in days of demand |
| 5 | `w_cover_cv` | −1–2 | Cover adjustment by demand CV |
| 6 | `w_intermittency` | −1–2 | Adjustment for zero-heavy demand |
| 7 | `max_cover_days` | 7–120 | Hard cap on inventory position |
| 8 | `w_holding` | 0–2 | Leanness response to holding/stockout ratio |
| 9 | `review_bias` | −0.5–1.5 | Coverage of the review period R |

```
L_eff  = lt_bias · E[L] + review_bias · R
σ_DL   = sqrt( L_eff · σ_d²  +  d̂² · σ_L² )
z_eff  = z_safety + w_sigma_lt · CV_L + w_intermittency · (1 − nonzero_frac)
s      = L_eff · d̂ · (1 + w_trend·trend) + z_eff · σ_DL
S      = s + cover_days·(1 + w_cover_cv·CV_d)/(1 + w_holding·holding_ratio) · d̂
S      = min(S, max_cover_days · d̂)
q      = constrain( max(0, S − IP) )   when IP ≤ s
```

CMA-ES searches **unbounded** ℝ¹⁰ per segment; `unpack()` maps through a logistic squash into
the bounded ranges, so the optimiser never hits a wall (preserving CMA's invariance properties).

**`theta_0` is a textbook classical (s,S)** — z = 1.645 (95% CSL), 14 days cycle stock, no
learned adjustments. CMA-ES starts there, so a failed fit degrades to defensible textbook
behaviour rather than to nonsense.

**Objective** = holding + stockout + ordering + DC backlog + capacity overflow, plus a quadratic
service-level barrier summed *per SKU* (so one starved SKU cannot hide inside a good network
average). Fitting uses a train window; all reported metrics come from a disjoint test window.

---

## 9. Benchmark results

### 9.1 Forecast accuracy (v2.2)

`python3 scripts/forecast_bench.py` — walk-forward one-step, 30 M5 SKUs x 120 held-out days:

| class | v2.0 rolling mean | v2.2 routed | delta |
|---|---:|---:|---:|
| smooth | 6.594 | 5.166 | **+21.7%** |
| intermittent | 6.561 | 4.388 | **+33.1%** |
| erratic | 6.847 | 6.472 | **+5.5%** |
| lumpy | 5.681 | 5.696 | **−0.3%** |
| **overall MAE** | **6.467** | **5.221** | **+19.3%** |
| **overall RMSE** | **12.642** | **10.091** | **+20.2%** |

**Lumpy is 0.3% worse and stays that way.** Six models were measured against it
(moving average, Croston-SBA, Holt-Winters, Theta, bagged median, and three ensembles)
and a plain 28-day moving average won. It is therefore what `auto_select` routes lumpy
demand to. Reporting a fancier model here would be a regression dressed as progress.

Model selection table that produced the routing (same command):

| class | moving_avg | croston | seasonal_hw | theta | bagged | ens(hw,theta,bag) |
|---|---:|---:|---:|---:|---:|---:|
| smooth | 6.594 | 6.610 | **5.166** | 6.462 | 6.413 | 6.116 |
| intermittent | 6.561 | 7.573 | **4.388** | 5.388 | 5.992 | 5.223 |
| erratic | 6.847 | 7.895 | 6.950 | 6.602 | 6.691 | **6.550** |
| lumpy | **5.681** | 5.780 | 6.096 | 5.828 | 5.704 | 5.731 |

Three results that contradict the textbook:

1. **Croston-SBA loses on every class**, including the intermittent one it exists for.
   M5's "intermittent" SKUs at ADI ≥ 1.32 are only ~24% zeros — not sparse enough for
   Croston to pay for the seasonality it discards. It is retained only for ADI ≥ 2.0.
2. **Erratic only improves 5.5%**, and only via a three-model ensemble. Regular timing
   with wild size variation defeats every individual model.
3. **Calendar features are not universally good.** Applying dow + snap factors on top
   of the routed model: erratic +1.2%, lumpy +0.6%, intermittent −8.2%,
   **smooth −22.6%**. Smooth collapses because Holt-Winters already models weekly
   seasonality and a day-of-week multiplier double-counts it. Calendar is therefore
   gated to erratic and lumpy only.

**Sigma quality — still the biggest single win:**

| sigma source | claimed | actual coverage |
|---|---|---|
| v2.0 `sqrt(mean)` Poisson proxy | 95% | **60.7%** |
| v2.2 measured residual (MAD) | 95% | **98.0%** |

Safety stock is `z · sigma_DL`, so a sigma that covers 61% of outcomes while claiming
95% undersized every order line in v2.0.

**Censored demand:** on a synthetic 60-day stockout block (true mean 4.72, recorded
sales 1.18) the first implementation "corrected" to 8.63 — worse than doing nothing.
Fixed with evidence-weighted shrinkage toward the observation (a valid lower bound);
now recovers **73% of the bias**.

### 9.2 Policy performance (v2.2)

`python3 demo/run_demo.py --fit --generations 50` — 30 M5 SKUs, held-out window,
1 DC + 2 stores, 45 generations to convergence in 5.4 s:

| Policy | Total cost | Fill rate | Worst SKU | Avg inventory |
|---|---:|---:|---:|---:|
| **SmartStock (CMA-ES)** | **8,650** | **99.81%** | **97.24%** | 32,563 |
| Classical (s,S), z=1.645 | 14,432 | 98.67% | 95.47% | 17,379 |
| Naive 7-day cover | 76,543 | 78.43% | 55.18% | 8,490 |

**vs classical: −40.1% cost, +1.15pp fill rate.**

**The gap keeps narrowing as the forecast improves, and that is correct:**

| forecaster | SmartStock | classical | gap |
|---|---:|---:|---:|
| v2.0 rolling mean | 7,936 | 17,380 | −54% |
| v2.1 seasonal | 7,904 | 14,127 | −44% |
| v2.2 routed | 8,650 | 14,432 | **−40%** |

A real share of the AI's original advantage was compensating for a bad forecast.
Improving the forecast transfers value to the simple policies. **Quote −40%.** Anyone
still citing −54% is citing a stale measurement against a crippled baseline.

SmartStock holds *more* inventory (32.6k vs 17.4k units) and is still cheaper because
stockout penalty is 1.5x unit price while holding is ~22%/yr of unit cost — roughly
11:1 in favour of holding. Change `holding_rate_per_day` / `stockout_multiple` in
`NetworkConfig` and refit if your economics differ. Never quote the cost delta without
the cost assumptions attached.

### 9.3 Scale (measured)

| SKUs | segments | CMA-ES dims | forecast prep | sim/generation | peak RSS |
|---:|---:|---:|---:|---:|---:|
| 500 | 6 | 60 | 0.2 s | 0.64 s | 73 MB |
| 2,000 | 5 | 50 | 0.6 s | 2.90 s | 184 MB |
| 10,000 | 7 | **70** | 3.3 s | 14.71 s | 830 MB |

Dimensionality bounded across a 20x catalogue increase. Simulation time and memory are
**linear** in SKU count — the pipeline tensor is `17 x 10000 x 4 x 61 x 8B ≈ 332 MB` at
10k SKUs. Above ~10k, `sku_batch_size=300–500` is **required**.

Serving: **3,012 SKU/s** (3,000 SKUs in 1.00 s), so 50k SKUs ≈ 17 s. Profiling found 35%
of serving time in `typing.get_type_hints`, called on every model construction in the
stdlib contracts shim; caching it per class took serving from 2,068 to 3,012 SKU/s.

### 9.4 Migrations, auth and the web app

| Artefact | Verified how | Result |
|---|---|---|
| Migration chain | `python3 scripts/verify_migration.py` — executes upgrade → downgrade → upgrade against SQLite via an `op` shim | 15 tables, 14 indexes; FK check clean; downgrade is a true inverse |
| Migration coverage | AST cross-check models.py vs migration | 15/15 tables, 188/188 columns, 13/13 indexes, 12/12 uniques, 12/12 checks |
| Auth + tenancy | `make test-security` | 30 assertions incl. forged signature, wrong key, `alg:none`, expiry, cross-tenant read, buyer self-approval |
| Legacy forecaster adapter | `make test-adapter` | 17 assertions incl. raises / NaN / inf / non-numeric / empty history / dead ensemble member |
| apps/web | import-graph resolver + JSON parse | 11 files, 25 import specifiers all resolve; **`npm run build` and `tsc --noEmit` NOT run — registry returns 403** |

## 10. Running it

### Zero-install demo (works with only Python 3.10+ and NumPy)

```bash
python3 demo/run_demo.py            # seed + fit + run + serve
open http://127.0.0.1:8099/
```

Also: `--reseed`, `--fit --generations 60`, `--run`, `--serve`, `--port N`.

`demo/run_demo.py` runs the **same engine and same JSON contracts** on stdlib `http.server` +
`sqlite3`. It exists because the production stack needs `pip install` and `npm install`; it is
a demo harness, not the production server (single-threaded per request, no auth, no migrations).

Seeded from **real M5 Walmart data**: 30 SKUs × 1,574 days, 3 locations, 4 suppliers with
deliberately different reliability — including one (`SUP-GLOBAL`) whose contract says 10 days
and whose true distribution is 13.8 ± 5.2, so the shrinkage estimator has something real to
discover.

### Production stack

```bash
make up          # docker compose: postgres, redis, smartstock, erp-api, worker, beat, web
make migrate
make logs
```

### Tests

```bash
make test        # 44 assertions, no pytest required
```

---

## 11. File map

```
services/smartstock/smartstock/
  contracts.py            wire contracts; Pydantic v2 with a stdlib fallback shim
  config.py               12-factor env settings
  optim/cmaes.py          pure-NumPy CMA-ES (replaces the `cma` package)
  core/segmentation.py    ADI/CV² classification → bounded parameter dimensionality
  core/leadtime.py        stochastic lead-time fitting, shrinkage, gamma sampler, σ_DL
  core/policy.py          CONTINUOUS (s,S); constraint projection; 10 params/segment
  core/network.py         vectorised (pop, sku, node, day) multi-echelon simulator
  core/recommend.py       request → draft POs with full rationale
  training/fit.py         CMA-ES objective, train/test split, baselines
  training/jobs.py        async job registry, ERP-payload → simulator adapter
  api/main.py             FastAPI service
  tests/test_engine.py    44 assertions

services/erp-api/app/
  db/models.py            13 tables, SQLAlchemy 2.0
  db/session.py           async engine + session factory
  clients/smartstock.py   retry + circuit breaker
  services/replenishment.py  orchestration, idempotent runs
  api/v1/{procurement,inventory,dashboard}.py
  tasks/celery_app.py     beat schedule + 4 tasks
  main.py

apps/web/src/features/procurement/
  PurchaseOrderApproval.tsx   443-line React approval screen
  types.ts  api.ts

apps/console/index.html   zero-build console the demo serves
demo/run_demo.py          the runnable demo
infra/docker-compose.yml
```

---

## 12. Approval screen design rules

`apps/web/.../PurchaseOrderApproval.tsx` and `apps/console/index.html` implement the same rules:

1. **Nothing is auto-approved.** Every line needs an explicit act.
2. **The model shows its working.** Reorder point, safety stock, lead-time distribution and the
   binding constraint are one click away on every row.
3. **Overrides are first-class.** Editing a quantity is one field; the delta vs AI shows
   immediately; **a reason is required past 20% deviation** — that reason is the training signal.
4. **Risk before value.** Sorted urgency-first: a critical $200 line that stops a line outranks
   a routine $80,000 one.
5. **Low confidence is visible.** "24% sure" is more useful than a confident wrong number.
6. **Bulk actions are scoped** to the current filter and state count and value before acting.

`GET /api/v1/procurement/variance` reports how often and in which direction humans override the
model. A persistent one-directional bias means the **cost model** is miscalibrated — feed it
back into `holding_rate_per_day` / `stockout_multiple` rather than letting buyers correct it by
hand forever.

---

## 13. Known limitations — say these out loud

1. **The three ML forecasters have never been run.** `LegacyModelAdapter` is written and
   has 17 passing contract tests against mocks, but LGBM / N-HiTS / Chronos were never
   loaded: `joblib`, `lightgbm`, `neuralforecast`, `chronos` and `torch` are all absent,
   the pip proxy returns 403, and `demo/A 2/A 2/models/` contains no serialised LGBM
   model to load even if joblib existed. The adapter contract is verified; the models are not.
2. **`apps/web` has never been compiled.** All config and source exist and every import
   resolves, but `npm install` fails with a 403 from the registry, so `npm run build` and
   `tsc --noEmit` were not executed. Type errors are possible. The zero-build console at
   `apps/console/index.html` is what actually runs today.
3. **Migrations have never touched Postgres.** Verified by executing the op sequence
   against SQLite plus an AST coverage cross-check. SQLite does not enforce enum
   membership, cannot drop constraints, and needs ≥3.35 to drop columns. Run
   `alembic upgrade head` on real Postgres before deploying.
4. ~~Auth is wired into procurement only.~~ **CLOSED.** `inventory.py` (2 routes,
   3 scope_query, 2 writes tenant-stamped) and `dashboard.py` (1 route, all 7
   aggregates scoped) are now tenant-scoped. `AuditLog` gained `tenant_id` so the
   adjustment audit write is tenant-correct. Proven by `make test-tenancy`:
   34 assertions executing the real route functions against a two-tenant dataset,
   plus a negative control confirming the harness catches a reintroduced leak.
   `scripts/` and the demo runner still bypass auth by design.
5. **Lumpy demand is 0.3% worse than a plain moving average** and six models failed to beat it.
6. **Two echelons only** (DC → stores); no lateral transshipment between stores.
7. **Cost model is linear** — no quantity price breaks, no truckload economics.
8. **Scale unverified above 10,000 SKUs.**
9. **No token revocation.** JWTs are stateless with an 8-hour TTL; a compromised token is
   valid until expiry. Add a `jti` denylist in Redis before handling real money.

## 14. Resume-here

v2.2 complete. Next, in value order:

1. **On a machine with network:** `npm install && npm run build` in `apps/web`, and
   `pip install alembic sqlalchemy psycopg2-binary && alembic upgrade head` against
   Postgres. These are the two "shipped but never executed" artefacts — fix any errors
   they surface before anything else.
2. **Extend auth to `inventory.py` and `dashboard.py`.** Procurement is scoped; the other
   two routers are not, which means today's tenancy is incomplete and therefore not real.
3. **Load the real LGBM / N-HiTS / Chronos weights** through `LegacyModelAdapter` and
   re-run `scripts/forecast_bench.py`. The plumbing is done and tested.
4. **Token revocation list** in Redis, keyed on `jti`.
5. **Lateral transshipment** and a third echelon in `network.py`.
6. **Quantity price breaks** in the cost model.

## 15. Quick reference

```bash
python3 demo/run_demo.py                      # everything
python3 demo/run_demo.py --fit --generations 60   # refit + benchmark table
cd services/smartstock && python3 tests/test_engine.py
curl localhost:8099/v1/policy                 # learned parameters per segment
curl localhost:8099/api/v1/procurement/variance
```

```bash
make test                      # engine + adapter + security + migration verifier
make bench-forecast            # walk-forward forecast backtest
make migrate-verify            # execute the migration chain against SQLite
python3 demo/run_demo.py       # cold start: seed, fit, run, serve
```

Key numbers: **−40.1% cost vs classical (s,S)** at 99.81% fill / 97.24% worst-SKU,
**forecast MAE +19.3%**, **sigma coverage 61% → 98%**, **≤70 CMA-ES dimensions at
10,000 SKUs**, **3,012 SKU/s serving**.

Always pair the cost delta with the cost assumptions (stockout 1.5x price vs holding
22%/yr). Note that each forecaster improvement has NARROWED the gap (54% → 44% → 40%)
by helping the baselines — that is the honest trend, not a regression.
