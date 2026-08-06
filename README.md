# Apparel ERP

A working GST ERP for Indian apparel retailers. Billing, inventory by size and colour,
customers, receivables, AI reordering, reports — and an importer that reads the shop's
existing stock list so they can actually leave their old system.

---

## Run it

**Windows** — double-click **`START-ERP.bat`**. Your browser opens at `http://127.0.0.1:8500`.

**Mac / Linux**

```bash
python3 demo/erp_server.py --open
```

Needs Python 3.10 or newer and nothing else. No install, no database server, no internet.
If Python isn't on the machine, get it from <https://www.python.org/downloads/> and tick
**"Add Python to PATH"** on the first screen of the installer.

Leave the console window open while you use the ERP — closing it stops the server.

---

## What's in it

Two demo businesses are loaded: **Kurta House** (Bengaluru) and **Denim Depot** (Mumbai).
Switch between them in the top-right. They deliberately share barcodes, style codes and
location codes, and neither can see the other's data.

| Screen | What it does |
|---|---|
| **Dashboard** | Sales today and this month, stock value, who owes you, what needs reordering |
| **Billing** | Scan or search, add lines, GST works itself out, print a tax invoice |
| **Invoices** | Every bill raised, with the printable original |
| **Customers** | Who buys from you and what they owe |
| **Receivables** | Money owed, aged into buckets, oldest first |
| **Inventory** | Stock by size and colour, with low-stock flags |
| **Styles & Variants** | The catalogue as a size × colour grid |
| **Bring in your stock** | Paste the shop's existing list and load it |
| **Price Advisor** | What each style should sell for, worked out from your own bills |
| **Tax & Compliance** | GSTR-1 and GSTR-3B working papers, month by month |
| **Receive stock** | Book in a delivery: partial, rejected lines, cost re-averaged |
| **Returns** | Take a garment back — restock it or write it off, GST reversed correctly |
| **Reorder & POs** | AI suggests quantities, you approve, purchase orders are created |
| **Reports** | Sales by style, size curve, GST summary, dead stock |

### GST

Garments up to ₹2,500 a piece are 5%, above that 18% — so one invoice legitimately mixes
slabs, and every line is worked out separately. A discount that takes a garment under ₹2,500
moves it to 5% on its own, which is why the rate is never stored on the product master.
CGST+SGST or IGST is chosen from the customer's state.

### Price Advisor — and exactly how far to trust it

Every suggestion is labelled with what it rests on, because the three kinds are not equally
reliable:

| Basis | What it is | How much to trust it |
|---|---|---|
| **arithmetic** | The GST slab boundary. Selling below cost. | Provable on a calculator. Cannot be wrong. |
| **observed** | Clearance maths from your own sell-through and stock. | Needs only that discounts sell more. Safe. |
| **estimated** | Anything resting on a fitted price elasticity. | **Not validated.** Offered as an experiment, never with a rupee figure. |

That last row is the honest one. Tested against two real public retail datasets:

- **M5 (Walmart, 30 SKUs, 1,895 days)** — prices barely move. The median SKU's price varies
  9.3% over five years and several never change at all, leaving only **13 usable price-change
  events across the whole dataset** (8 called correctly, p = 0.29 — chance). Elasticity is not
  identifiable, and no amount of pooling, seasonality control or outlier trimming changes that;
  `make diagnose-elasticity` walks the ladder.
- **BigMart (India, 2013, 514 products, 10 outlets)** — prices vary hugely, but *between*
  products rather than over time. Dearer products did **not** sell fewer units: 48% of 1,693
  held-out pairs, indistinguishable from chance. Product quality confounds price.

So the product does not claim to predict the profit-maximising price. It flags the arithmetic
you can verify yourself, does the clearance maths from your own numbers, and treats price
experiments as experiments. `make validate-pricing` and `make validate-bigmart` re-run both.

### The GST dead zone

That 5%-to-18% step is the sharpest thing in the product. Because it is a **step, not a slope**:

| Taxable value | GST | Customer pays |
|---|---|---|
| ₹2,500.00 | 5% | **₹2,625** |
| ₹2,500.01 | 18% | **₹2,950** |

**No shelf price between ₹2,625 and ₹2,950 exists.** A shop tagging a kurta at ₹2,800 is
charging the customer ₹175 more than ₹2,625 while keeping *less* — the difference is tax.
Shops sit in that band constantly, because they price on the tag and the slab is decided by
the taxable value. Price Advisor flags every style in it, on the first screen.

---

## Loading a real shop's stock

**Bring in your stock** → paste their list straight out of Excel, header row included →
**Read my sheet**.

It matches their column names to ours, cleans the values (`Rs. 1,450.00`, `0.18`, `MED`,
`Nos`, `(4)`), groups the sizes of one garment into a single style, and shows every row with
anything wrong with it. **Nothing is saved until you press the second button.**

Re-importing a corrected sheet updates those items — it never doubles their stock.

No file to hand? **Try a deliberately messy sample** shows the same thing.

---

## Showing it to a shop

Get their stock export beforehand if you can — Tally, the old ERP, or whatever spreadsheet the
counter actually uses.

1. **Bring in your stock** — paste their real file and import it. Every screen below now shows
   *their* shop, not a demo.
2. **Dashboard** — the numbers are theirs.
3. **Billing** — sell one of their items plus something over ₹2,500. Two GST slabs, one bill.
   Print it.
4. **Inventory** — the line you just sold has dropped.
5. **Receivables** — the bill is in the ageing list. Take a payment against it.
6. **Reorder** — approve two suggestions, a purchase order appears.
7. **Switch business, top-right** — completely separate books.

---

## Housekeeping

```bash
python3 demo/erp_server.py --reseed      # start over with fresh demo data
python3 demo/erp_server.py --port 8600   # if 8500 is already in use
```

Data lives in `demo/erp_demo.db`. Copy that file to back it up; delete it to start clean.

---

## Honest limitations

Say these out loud rather than letting a customer find them:

- **No e-invoice IRN or e-way bill.** No GSTN API integration.
- **No stock transfers between locations.** The warehouse and the shop floor are separate
  locations, but nothing moves stock from one to the other except a sale or a delivery.
- **No supplier bills or payables.** You can receive goods and see what they cost, but there is
  no record of what you owe the supplier or when you paid them — the mirror image of
  Receivables, and the next real gap.
- **No stocktake / physical count.** Every shop counts its shelves and finds a discrepancy.
  There is no screen to enter a count and post the adjustment, so shrinkage has nowhere to go.
- **Cost basis is weighted-average only.** No FIFO and no batch costing, so margin reports are
  directional rather than audited.
- **The GST exports are working papers, not filings.** The figures are right and the tables
  carry the portal's section names, but no live upload has been tested — the portal takes JSON
  or its own Excel template. This is what you hand your accountant.
- **No login on this local build.** The production API has JWT auth with roles; this one does
  not ask anyone to sign in.
- **One machine.** It is a local app. Multi-device means running the PostgreSQL build, which
  has never been executed — see `AGENTS.md`.
- **The PostgreSQL build has never been run anywhere.** There is no Postgres binary and no
  docker in the dev sandbox, so `alembic upgrade head` against a real server is still
  outstanding. Two static audits (`make audit-pg`, `make audit-upgrade`) cover the failure
  modes SQLite hides, and one of them caught a real bug — but they are not the real thing.
- **`apps/web` has never been compiled.** `npm install` is blocked. The source typechecks clean
  against shims (`make typecheck-web`), but no `dist/` has ever been produced or served.

None of these stop you demoing or running a pilot.

---

## SmartStock

The reorder suggestions come from **SmartStock**, an inventory optimiser using a CMA-ES-tuned
continuous (s,S) policy under stochastic lead times. On 30 SKUs of real M5 retail data over a
held-out window:

| Policy | Total cost | Fill rate |
|---|---:|---:|
| **SmartStock** | **7,904** | **99.98%** |
| Classical (s,S) | 14,127 | 98.84% |
| Naive 7-day cover | 76,701 | 78.38% |

−44% cost at a higher fill rate than classical. It holds *more* stock and still costs less,
because stockouts cost roughly 11× what holding does under this cost model — change
`holding_rate_per_day` / `stockout_multiple` and refit if your economics differ.

It is frozen: bug fixes only. `make demo-smartstock` runs it standalone on the M5 data.

---

## Layout

```
START-ERP.bat            double-click launcher for Windows
demo/erp_server.py       the ERP you run and demo
apps/console/erp.html    the whole UI, one file, no build step
demo/erp/                billing, GST posting, importer, seed data
services/erp-api/        production FastAPI + SQLAlchemy + PostgreSQL build
services/smartstock/     the optimiser (NumPy only)
AGENTS.md                architecture, conventions, and how to pick the work back up
```

For anything beyond running it — architecture, decisions, what's verified and what isn't —
read **`AGENTS.md`**.
