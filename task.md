# Task list

> **This file did not exist before 2026-08-05.** It was created from the priority list
> in `AGENTS.md` §8 and the actual state of the repository, not reconstructed from
> memory. Items marked done are backed by a named command that passes; anything not
> backed by a command is listed as outstanding rather than assumed.

---

## Done

- [x] **1 · Core ERP** — billing with live per-line GST, invoices, customers, receivables
      ageing, inventory by size/colour variant, styles, reorder suggestions, reports.
      `make demo-check` · 83/83
- [x] **2 · Supplier bills and payables** — bills posted from goods receipts, payments
      settle oldest-first, per-supplier open balance, cross-tenant isolation.
- [x] **3 · Stocktake / physical count** — count a location, variance adjusted and written
      to the stock ledger as an `adjustment` carrying the DIFFERENCE, not the count.
      `make verify-inventory` · 27/27
- [x] **4 · Stock transfers between locations** — both legs in one transaction,
      `transfer_out` at source and `transfer_in` at destination, total stock conserved.
      `make verify-inventory` · 27/27
- [x] Goods receiving (GRN) — partial deliveries, rejected lines, weighted-average cost.
- [x] Credit notes / returns — GST reversed at the original line's rate, restock or write off.
- [x] Master-data importer — reads a shop's existing stock list out of Excel.
- [x] Price Advisor — every suggestion labelled `arithmetic` / `observed` / `estimated`.
- [x] **Migration regenerated** — `migrate-verify` was red on pickup (payables and the four
      new tables were in `models.py` but not in the migration). Now 32 tables / 449 columns.
- [x] **5 · Barcode label printing (EAN-13)** — hand-rolled symbology, Avery grids, bad check
      digits corrected and flagged. `make demo-check`
- [x] **6 · GSTR-1 / GSTR-3B export** — working papers, slabs reconciling, 1 and 3B agreeing
      to the rupee. `make demo-check`
- [x] Migration upgrade-path guard — `make audit-upgrade`, after a regenerated `0001_initial`
      was found to silently skip every live database.

## Outstanding

- [ ] **Take it to a shop.** Still the bottleneck. Nothing here has been in front of a
      real shopkeeper.
- [ ] **Run `make export-prod`** and do the three blocked jobs on a networked machine.
- [ ] **A real GST portal upload.** The exports are working papers; nobody has fed one to the
      offline utility or the portal, which needs a GSTIN and a login this project has not got.
- [ ] **`alembic upgrade head` against real PostgreSQL.** No `postgres` binary, no docker,
      `pip`/`apt` are 403 in this sandbox. Two static audits (`make audit-pg`,
      `make audit-upgrade`) cover what SQLite hides, but the real run has never happened.
- [ ] **`npm run build` in `apps/web`.** `npm install` is 403. The source typechecks clean
      against hand-written shims (`make typecheck-web`) but no `dist/` has ever existed.
- [ ] **BigMart on all 8,523 rows.** Currently 612 (7.2%) — the fetch tool caps at ~62 KB.

- [ ] Token revocation; per-tenant company profile so seller state is not hard-coded.

## Known rough edge, not yet decided

- [ ] **A clean count cannot be recorded.** `post_stocktake` raises *"no discrepancies found,
      nothing to adjust"* when every line matches. That is defensible — there is nothing to
      adjust — but a shop that counts a rack and finds it perfect has done real work and gets
      no record of it, so next quarter there is no evidence the count happened. Deciding
      whether a zero-variance stocktake should be storable is a product call, not a bug fix,
      so it has been left alone and flagged here.

## Do not pick up

- SmartStock forecast accuracy. Frozen and adequate.
- Re-gating the elasticity confidence thresholds. The constraint is the data, not the code —
  see `AGENTS.md` §4.
