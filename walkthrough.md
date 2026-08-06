# Walkthrough — showing this to a shop

> **This file did not exist before 2026-08-05.** Created from the demo script in
> `README.md` and the screens that actually exist today.

`make demo` → `http://127.0.0.1:8500`, or double-click `START-ERP.bat` on Windows.

Two businesses are loaded — **Kurta House** (Bengaluru) and **Denim Depot** (Mumbai). They
deliberately share barcodes, style codes and location codes, and neither can see the other's
data. The switcher is top-right.

---

## The fifteen-minute path

**1 · Bring in your stock** — paste their real file. Columns matched, `Rs. 1,450.00` cleaned,
`MED` normalised, sizes grouped into styles, every problem shown before anything is written.
Import it, and every screen below is now showing *their* shop.

**2 · Dashboard** — their numbers. Sales chart, stock value, who owes money, what needs
reordering.

**3 · Billing** — sell one item under ₹2,500 and one over it. Two GST slabs on one bill,
worked out per line. Print it.

**4 · Inventory** — the line you just sold has dropped.

**5 · Receivables** — the bill is in the ageing list. Take a payment; it settles oldest-first.

**6 · Receive stock** — book in a delivery against an open purchase order. Take part of it,
reject a piece as damaged, and change the supplier's price. Point at the cost column: it
**re-averages** across what they already hold rather than being overwritten. Overwriting is the
common shortcut and it quietly falsifies every margin report afterwards.

**7 · Returns** — find the bill, take a garment back. Tick one line to go back on the shelf and
untick another as unsaleable. The GST comes off at the rate that bill charged, not today's rate.

**8 · Stocktakes** — *new.* Start a count, change a couple of numbers to what is really on the
rack, post it. The variance is adjusted and written to the stock ledger as an `adjustment`
carrying the **difference**, so shrinkage becomes a number they can look at rather than a
mystery that shows up in the year-end.

**9 · Stock transfers** — *new.* Move pieces from the warehouse to the shop floor. Both sides
move in one transaction and both get a ledger row, so the **total never changes — only where it
sits**. This is the line to say out loud, because it is the thing a spreadsheet gets wrong.

**10 · Price Advisor** — show the GST dead zone. Garments are 5% up to ₹2,500 taxable and 18%
above, so a tag between ₹2,625 and ₹2,950 charges the customer more while the shop keeps less;
the gap is tax. Verifiable on their own calculator, which is why it lands.

**11 · Payables** — what they owe suppliers, mirroring receivables.

**12 · Print labels** — *new.* On Inventory, tick a few garments and press **Print labels**.
Pick the Avery stock they actually buy, and a printable sheet opens with real EAN-13 barcodes,
the garment name, size and price. If a stored barcode would not scan, it is corrected and
listed at the top of the sheet — worth pointing at, because that is the failure a shop only
finds at the till after printing four hundred tags.

**13 · Tax & Compliance** — *new.* Pick a month; see outward supply split by GST slab, input
credit from supplier bills, and what is actually payable after offsetting. Download GSTR-1 and
GSTR-3B. **Be straight about this one:** it is what they hand their accountant, not something
they upload themselves. The screen says so and so do both files.

**14 · Switch business, top-right** — everything changes. Same barcodes, same style codes,
completely separate books. *This is why it is safe to put your shop on it.*

---

## New in this build

| Screen | What it does | Why it matters to them |
|---|---|---|
| **Stocktakes** | Count a location; variance is adjusted and ledgered | Shrinkage stops being invisible. The count sheet starts pre-filled with what the books say, so they only touch what is wrong. |
| **Stock Transfers** | Move stock between locations, both legs at once | Warehouse and shop floor stop drifting apart. Total stock is conserved by construction. |
| **Label printing** | EAN-13 tags on Avery stock, printed from the browser | No separate label software. Unscannable barcodes are caught before the tags are made, not after. |
| **Tax & Compliance** | GSTR-1 and GSTR-3B working papers per month | Their accountant's two-day job becomes twenty minutes. The two returns are built from one query so they cannot disagree. |

Stocktakes and transfers refuse the things that would corrupt the books: you cannot transfer to the same location,
cannot send more than is on the shelf, cannot count a negative quantity, and cannot touch
another business's stock or locations. `make verify-inventory` proves each of those, and reads
the database back directly rather than trusting the HTTP status.

---

## Say these out loud

Better from you than discovered by them:

- No e-invoice IRN or e-way bill; no GSTN API integration.
- Cost basis is weighted-average only — no FIFO, no batch costing.
- **The GST files are working papers, not filings.** No live portal upload has been tested.
- **A count where everything matches cannot currently be saved** — the system only records a
  stocktake when something differs. Fine in practice, occasionally annoying.
- This local build has no login. The PostgreSQL build has JWT auth with roles, and **has never
  been run** — see `AGENTS.md`.
