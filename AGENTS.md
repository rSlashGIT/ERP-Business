# AGENTS.md

**Read this before touching anything. Rewrite it — don't append to it — as the last step
of every session, so it always describes the current state rather than the journey.**

Last updated: 2026-08-05 · ERP schema 32 tables, 449 columns · SmartStock `2.2.1` (frozen)

---

## 1. What this is

A multi-tenant apparel ERP for Indian SME retailers — billing with live GST, inventory by
size/colour variant, customers, receivables, goods receiving, returns, **supplier bills and
payables**, AI-assisted reordering and reports — plus **SmartStock**, a frozen
inventory-optimisation service that turns demand history and supplier lead times into draft
purchase orders a human approves.

The commercial goal is a **generalised skeleton you can demo to any apparel business**. When one
signs, you customise on top of it rather than starting over.

---

## 2. What works right now

`make demo` → `http://127.0.0.1:8500`. Python 3.10+, nothing else. `make demo-check` and
`make verify-ui` prove it works before you show anyone.

- **Stock goes both ways.** Receive a delivery against a purchase order — partial, with rejected
  lines — and the cost basis re-averages instead of being overwritten. Take a garment back and
  choose per line whether it goes on the shelf or is written off.
- **Count the shelves and move stock between them.** A stocktake adjusts to what you actually
  counted and records the variance in the ledger, so shrinkage is a number rather than a
  mystery. A transfer moves pieces from the warehouse to the shop floor in one transaction,
  writing both legs — the total never changes, only where it sits.
- **Bring the shop's existing stock list in.** Paste it out of Excel or their old system.
  Columns matched, values cleaned, sizes grouped into styles, every problem shown before
  anything is written. Re-importing updates rather than doubling.
- **Bill in seconds** with live per-line GST, and print a compliant tax invoice.
- **Receivables age into buckets**; payments settle oldest-first.
- **Payables track what you owe suppliers.** Bills are posted from goods receipts, payments
  settle oldest-first (mirroring receivables), and each supplier's open balance is visible on
  a dedicated screen. Cross-tenant isolation is tested.
- **Two businesses share barcodes, style codes, location and PO numbers** and neither can see
  the other's data.
- **Print price tags.** Real EAN-13 barcodes as SVG on Avery label stock, straight out of the
  browser. A stored barcode with a wrong check digit is corrected and flagged rather than
  printed — an unscannable tag is discovered at the till, after four hundred have been made.
- **GSTR-1 and GSTR-3B working papers.** Month by month, slabs reconciling to the header,
  credit notes netted off, input credit from supplier bills, offset head by head. **Not a
  filing** — see §5.
- **Price Advisor**, with each suggestion labelled by how much it can be trusted — see §4.

### Caught defects worth knowing about

**`replenishment_runs` capped the platform at one tenant.** `UNIQUE(run_date, triggered_by)`
was global. Every tenant's scheduler writes the same pair, so the first tenant's nightly run
each day would succeed and every other tenant's would fail on insert, silently.

**Guards that passed for the wrong reason — four times now.** Deleting billing's tenant
predicate still returned 422 (the stock check caught it afterwards, and leaked the other
business's garment name). Deleting `ORDER BY size_seq` left the size *header* right and
scrambled the *grid*. A cross-tenant return "passed" via a 500 crash further down the function.
And a cost-averaging assertion passed on an empty shelf, where the weighted average and the
invoice price are the same number. **Assert the reason, and prove the assertion can fail.**

**A mutation harness that mutated nothing.** `sys.exit(...) or p.write_text(...)` — `sys.exit`
raises, so the write never ran and every mutation reported "tests still pass". It read as proof
the suites were strong. `scripts/mutate_check.py` now fails if a mutation does not reach disk.

**The Price Advisor modal rendered raw HTML as text.** `openM(title, subtitle, body, footer)`
called as `openM(title, body)`. Every API test passed. `make verify-ui` now drives the real
render functions through a hand-written DOM.

**"disk I/O error" on every screen at once.** SQLite's WAL is unsupported on network
filesystems and the `PRAGMA` *succeeds* there before every later write fails. `connect()` now
proves a journal mode with a real write, read-back and commit before trusting it.

**A duplicate `goods_receipts` table.** A minimal line-level receipt already existed, feeding
SmartStock's lead-time learning. Adding a GRN header with the same name would have collided;
the old model was absorbed into the new header/line pair and `celery_app.py` repointed.

**The price engine claimed Rs 628,913 a year from one style.** Three stacked modelling faults —
see §4.

**A killed mutation run left a planted bug in the working tree.** `mutate_check.py` edits a
source file, runs a suite, then restores it. A run cut short by a timeout never reached the
restore, and `gst_export.py` was left treating credit notes as POSITIVE outward supply — a
wrong tax return, sitting silently in the repo. It now stashes every original before the first
edit and restores on `atexit` plus SIGINT/SIGTERM/SIGHUP, and `--only <substring>` lets the
suite run in chunks rather than being killed mid-edit.

**Two mutations passed because the assertions were weak, not because the code was right.**
A month with no credit notes in it cannot detect a credit-note sign error, and "A's input
credit differs from B's" is satisfied perfectly by a mutation that SWAPS the two tenants'
credit. Both are now asserted against an independent recomputation — the credit note is checked
in the month it was actually raised, and input credit is reconciled to the payables ledger
**to the rupee**, because both shops are seeded with the same number of bills and the counts
matched by coincidence.

**The migration had silently drifted from the models — again.** `migrate-verify` was already
RED when stocktakes and transfers were picked up: payables had been added to `models.py` without
regenerating the migration, so eight tables (`supplier_bills`, `supplier_payments`, their lines
and allocations, plus the four stocktake/transfer tables) existed in the ORM and nowhere in the
schema. The header of this file claimed 28 tables while the VERIFIED table below it said 24,
which is exactly what that drift looks like from the outside. Regenerated: 32 tables, 449
columns. **Run `make migrate-gen` in the same commit as any model change** — the generator is
not automatic and nothing else will notice.

**A regenerated `0001_initial` would have broken every live database.**
`gen_migration.py` rewrites `0001_initial` from `models.py`. That is right for a fresh install
and silently wrong for one that already exists: the database is stamped `0003_size_seq`, so
`alembic upgrade head` finds nothing to do and exits 0, and the nine tables added since —
customers, sales invoices, payments, allocations, goods receipts and credit notes — are never
created. The app then dies on first use with `relation "goods_receipts" does not exist`, after
a deploy that reported clean. `verify_migration.py` could not see it because it always builds
from an EMPTY database, which is the one starting point where the bug does not exist.
Fixed by `0004_reconcile_schema`, which converges whatever is there towards the metadata and is
a no-op on a fresh install. Guarded by `make audit-upgrade`, negative-controlled: remove 0004
and it reports 24 unreachable tables.

---

## 3. Architecture

| Layer | Choice | Where |
|---|---|---|
| ERP API | FastAPI + SQLAlchemy 2.0 async + PostgreSQL 16 | `services/erp-api/` |
| Domain logic | Pure Python, framework-free | `app/domain/` — `gst.py`, `importing.py`, `pricing.py` |
| Demo server | stdlib `http.server` + sqlite | `demo/erp_server.py` |
| Demo domain | sqlite-backed flows | `demo/erp/` — `billing`, `receiving`, `returns`, `importer`, `prices`, `payables` |
| UI | single-file HTML, zero build | `apps/console/erp.html` |
| AI service | NumPy only | `services/smartstock/` — frozen |

### Load-bearing decisions

**`products.id` IS the atomic SKU.** One Product = one size/colour = one barcode = one stock
balance. `product_styles` sits above it and does not replace it.

**GST rate is NEVER stored on the master.** `gst.py` derives it per line from taxable value per
piece. **And a credit note copies the rate off the invoice line it reverses** — a garment sold
at 18% is refunded at 18% even if the shop has since marked it under Rs 2,500.

**GSTR-1 and GSTR-3B are built from ONE query, not two.** `_outward()` feeds both: 1 groups
those rows, 3B sums them. Computing the two returns separately is how a shop ends up filing
figures that disagree with each other, which is what gets it a notice. `demo-check` asserts
3B's outward tax equals 1's tax total to the rupee.

**Input credit is offset head by head.** IGST credit cannot wipe out an SGST liability without
the ordering rule the portal applies itself, so netting one grand total would understate what is
actually payable.

**A stocktake ledgers the VARIANCE, not the count.** Posting the counted quantity into
`stock_movements` would make every count look like a huge stock injection and destroy the
movement history's ability to reconcile to the balance. The difference is also the number the
shop actually wants — it is the shrinkage.

**A transfer conserves stock.** Both legs are written in one transaction, so the tenant's total
on-hand is identical before and after. `make verify-inventory` asserts that total explicitly;
a transfer that only credited the destination would otherwise look fine on both screens.

**Receiving re-averages cost, never overwrites it.** 10 @ 600 plus 30 @ 700 is 675. Overwriting
with the latest invoice silently falsifies every margin report downstream.

**Rejected goods are a separate column, not a negative.** Only accepted stock exists and only
accepted stock is paid for.

**`size_seq` uses banded scales.** Alpha 10–100, one-size 500, numeric 1000+n.

**Tenant identity comes from the signed token, never request input.** `scope_query()` is the
only sanctioned filter.

**Payables mirror receivables exactly.** `supplier_bills` mirrors `sales_invoices`,
`supplier_payments` mirrors `payments`, allocation is oldest-first in both directions.
`goods_receipt_id` is an optional FK on `supplier_bills` so a bill can link to one GRN or
stand alone.

### Environment constraints — measured, not assumed

Re-verified this session rather than taken on trust:

| Thing | State |
|---|---|
| `docker` | not installed; daemon unreachable |
| `postgres` / `psql` / `initdb` | no binaries anywhere on the box |
| `psycopg2`, `psycopg`, `sqlalchemy`, `alembic` | none importable |
| `pip install` | `403 Forbidden` via the proxy |
| `npm install` | `403 Forbidden` from the registry |
| `apt-get download` | `403 Forbidden` |
| `node` | v22.22.3 available |
| `tsc` | **6.0.3 available globally** at `/usr/local/lib/node_modules_global/bin` |
| `web_fetch` | caps a response at ~62 KB; other URL-fetch methods are not permitted here |

So: migrations are verified by a SQLite op-shim plus two static audits; the UI harness is a
hand-written DOM; `apps/web` is typechecked against hand-written shims but never built.

---

## 4. Price Advisor: what is and is not validated

Every suggestion carries a `basis`, and they are not equally trustworthy:

| Basis | Rests on | Status |
|---|---|---|
| `arithmetic` | GST slab boundary, below-cost detection | **Certain.** Verifiable on a calculator. |
| `observed` | Sell-through rate, stock, days left | **Safe.** Needs only that discounts sell more. |
| `estimated` | Fitted price elasticity | **NOT VALIDATED.** Experiment only, never a rupee figure. |

**Neither public dataset supports a per-SKU elasticity claim, and this was tested properly.**

- **M5** — prices barely move: median 9.3% spread over 1,895 days, several SKUs never change
  price, daily demand CV ≈ 1.0. Across all 30 SKUs there are **13 usable price-change events**;
  8 were called correctly, exact binomial **p = 0.29**. An earlier version of this file
  advertised "67% direction correct" — that number was this, and it was retracted.
  `make diagnose-elasticity` walks pooling, day-of-week FE, outlier trimming and category
  shrinkage; none of them move it, because the information is not in the data.
- **BigMart** — 514 products, 10 outlets, 149% within-category price spread, split by
  `Item_Identifier` so no SKU crosses. Dearer products did not sell fewer units: **47.5% of
  1,693 held-out pairs, z = −2.02** — significant in the direction OPPOSITE to a price effect,
  which is what quality confounding looks like. (An earlier note here said z = −1.68; that came
  from rounding the rate to 48%. The precise figure is −2.02.)
  **This sample is not underpowered**: SE on the directional rate is 1.22 pp, so it could detect
  a 2.4 pp shift. Any effect big enough to price on (55–60%) is decisively excluded. Still only
  612 of 8,523 rows — see BLOCKED.

**Protocol divergences from M5, and why:** BigMart has no date column, so a forward-in-time
split is impossible — the holdout is by product. `Item_Outlet_Sales` is revenue, so units are
derived as sales ÷ MRP first (fitting revenue on price recovers ≈ +1 by construction). Category
replaces month as the fixed effect. The estimand is genuinely different: M5 asks "when THIS
product's price moved, what happened to ITS sales", BigMart asks "within a category, do dearer
products sell less".

`estimate_elasticity` now refuses to fit below a 10% within-period price spread and reports
`insufficient-variation`, which is the honest thing to tell a shop that never discounts.

---

## 5. Current status

### VERIFIED

| Area | Command | Result |
|---|---|---|
| GST engine | `make test-gst` | 48/48 |
| Importer | `make test-import` | 119/119 |
| Cross-tenant isolation | `make test-tenancy` | 70/70 |
| Auth / roles | `make test-security` | 35/35 |
| Route + uniqueness audits | `make audit` | 0 defects |
| Migrations | `make migrate-verify` | 32 tables, 449 cols, both drift directions |
| SmartStock | `make test-engine` `test-adapter` `test-nomocks` | 44 · 21 · 27 |
| **The demo** | `make demo-check` | **106/106**, reasons asserted not statuses |
| **Every screen and modal** | `make verify-ui` | **128/128** |
| **Stocktakes + transfers** | `make verify-inventory` | **27/27** end to end, DB read back directly |
| **Barcode labels (EAN-13)** | `make demo-check` | encoding round-trips; bad check digits corrected |
| **GSTR-1 / GSTR-3B** | `make demo-check` | slabs reconcile; 1 and 3B agree to the rupee |
| **The tests themselves** | `make mutate` | **45/45** injected defects caught |
| Price engine, 2 datasets | `make validate-pricing` `validate-bigmart` | reports honestly, refuses to over-claim |
| **Payables API** | ad-hoc integration test | **7/7** (summary, bills, partial pay, full pay, balance check, settle-out, cross-tenant) |

### NOT A FILING — say this out loud

The GST exports are **working papers**. The arithmetic is right and the tables carry the
portal's own section names, but nothing has been through a live upload, the portal ingests JSON
or its own Excel template rather than a bare CSV, and B2B/B2C is decided purely on whether a
customer has a GSTIN on file. The screen and both CSVs say so in as many words. Pitch it as
"this is what you hand your accountant", never "this files your return".

### BLOCKED

1. **PostgreSQL + Alembic never executed.** No `postgres` binary, no docker daemon, and
   pip/apt are 403, so `alembic upgrade head` against a real server has still never run.
   `make audit-pg` and `make audit-upgrade` are STATIC substitutes and say so themselves.
   Run the real thing before deploying.
2. **`apps/web` never compiled.** `npm install` is 403. A global `tsc` 6.0.3 exists, so the
   source WAS typechecked against hand-written shims (`make typecheck-web`): 379 errors dropped
   to 9, all of them callback parameters whose types come from library signatures the shims
   leave as `any`. **No real code defect found — but that is not a build.** `vite build` has
   never produced an artefact and nothing has been served from `dist/`.
3. **BigMart tested on 612 of 8,523 rows (7.2%).** The sanctioned fetch tool caps a response at
   ~62 KB and fetching URLs by other means is not permitted here. The power analysis above
   bounds what that costs; the full-file re-run is still outstanding.
4. No token revocation; scale unverified above 10,000 SKUs.

---

## 6. Defect-class checklist

| Class | Guard | State |
|---|---|---|
| Unscoped tenant queries | `scripts/audit_route_scoping.py` | 0 defects |
| Global uniqueness | `scripts/audit_uniqueness.py` | 0 defects |
| Schema drift | `gen_migration.py` + `verify_migration.py` | 0 drift |
| Guards passing for the wrong reason | `demo/verify_erp_demo.py` asserts the *reason* | 0 defects |
| Renders that silently break | `demo/verify_ui.js` | 128/128 |
| Tests that stopped asserting | `scripts/mutate_check.py` | 45/45 |
| Postgres-only DDL failures | `scripts/audit_pg_dialect.py` | 12/12 static |
| Migrations that only work from empty | `scripts/audit_upgrade_path.py` | sound |

**Negative-control anything you add — via `make mutate`, not by hand.** Six mutations in that
list were MISSED on first run and each exposed a genuinely weak assertion.

---

## 7. Running things

```bash
make demo                 # ← THE DEMO, http://127.0.0.1:8500
make verify               # everything below, in one go
make demo-check           # 83 assertions against a live demo
make verify-ui            # 90 render checks across every screen and modal
make mutate               # break the code on purpose, prove the tests notice
make validate-pricing     # M5
make validate-bigmart     # BigMart India
make diagnose-elasticity  # why the M5 fit is weak
```

---

## 8. Next steps, in priority order

**Still nobody has shown this to a shop.** That remains the bottleneck.

1. **Take it to the shop.** Run it on Windows, get their stock export, import it live.
2. ~~**Supplier bills and payables**~~ — **DONE.** Bills, payments, oldest-first allocation,
   UI screen, seeded demo data, cross-tenant isolation tested.
3. ~~**Stocktake / physical count**~~ — **DONE.** Count a location, variance is adjusted and
   written to the stock ledger as an `adjustment` carrying the DIFFERENCE, not the count.
4. ~~**Stock transfers between locations**~~ — **DONE.** Both legs in one transaction,
   `transfer_out` at the source and `transfer_in` at the destination, total stock conserved.
5. **On a machine with network:** `alembic upgrade head` against real PostgreSQL; `npm run
   build` in `apps/web`; re-run BigMart on all 8,523 rows.
6. ~~**Barcode label printing (EAN-13)**~~ — **DONE.** SVG symbology, Avery grids, check
   digits validated and corrected.
7. ~~**GSTR-1 / GSTR-3B export**~~ — **DONE**, as working papers. The remaining step is a real
   portal upload, which needs a GSTIN and a login this project does not have.

Do **not** pick up SmartStock forecast accuracy. It is frozen and adequate.
Do **not** re-gate the elasticity confidence thresholds again — the constraint is the data.
