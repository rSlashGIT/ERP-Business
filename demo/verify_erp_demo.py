#!/usr/bin/env python3
"""
Boot the ERP demo on a scratch database, exercise every API a sales demo
touches, and exit non-zero if any of it is wrong.

    python3 demo/verify_erp_demo.py          (or: make demo-check)

WHY THIS EXISTS
---------------
"I clicked around and it looked fine" is not verification, and the demo is the
product as far as a prospect is concerned. So it gets the same treatment as the
production code: a closed loop that fails loudly.

Past the read-path smoke tests, this drives the three things that would be
embarrassing in front of a shopkeeper:

  * a MIXED-SLAB invoice — a garment under Rs 2,500 at 5% and one over it at
    18% on the same bill — recomputed independently against the shared GST
    engine, then checked that stock actually moved and the receivable actually
    appeared and a payment actually cleared it;
  * the NEGATIVE CONTROLS: overselling refused, selling another tenant's
    garment refused, billing another tenant's customer refused. A guard nobody
    has watched fail is not a guard;
  * SIZE ORDER, because `S, 38, 40, M, 42, L` on screen kills the demo.

Runs against a throwaway database on a throwaway port (ERP_DEMO_DB + --port),
so it never disturbs the seeded demo you are about to show someone.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "erp-api"))

CHECKS: list[tuple[bool, str, str]] = []


def check(ok, label: str, detail: str = "") -> bool:
    ok = bool(ok)
    CHECKS.append((ok, label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str, tenant: str):
        self.base, self.tenant = base, tenant

    def _open(self, path: str, payload=None):
        req = urllib.request.Request(
            self.base + path,
            headers={"X-Tenant": self.tenant, "Content-Type": "application/json"},
            data=None if payload is None else json.dumps(payload).encode())
        return urllib.request.urlopen(req, timeout=25)

    def get(self, path: str):
        return json.load(self._open(path))

    def items(self, path: str) -> list:
        r = self.get(path)
        return r.get("items", r.get("rows", [])) if isinstance(r, dict) else r

    def rows(self, path: str) -> list:
        r = self.get(path)
        return r.get("rows", r.get("items", [])) if isinstance(r, dict) else r

    def post(self, path: str, payload: dict):
        return json.load(self._open(path, payload))

    def post_status(self, path: str, payload: dict) -> tuple[int, str]:
        """Status instead of an exception — for negative controls."""
        try:
            r = self._open(path, payload)
            return r.status, r.read().decode()[:200]
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:200]

    def raw(self, path: str) -> bytes:
        return self._open(path).read()


def wait_for(base: str, proc: subprocess.Popen, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(base + "/api/v1/tenants", timeout=2).read()
            return True
        except Exception:
            time.sleep(0.4)
    return False


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp = tempfile.mkdtemp(prefix="erp-verify-")
    env = {**os.environ, "ERP_DEMO_DB": os.path.join(tmp, "verify.db")}

    print(f"booting the ERP demo on {base} against a scratch database\n")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "demo" / "erp_server.py"),
         "--port", str(port), "--reseed"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait_for(base, proc):
            print("server did not come up")
            if proc.poll() is not None and proc.stdout:
                print(proc.stdout.read()[-3000:])
            return 1

        tenants = [t["id"] for t in Client(base, "x").get("/api/v1/tenants")["items"]]
        A, B = Client(base, tenants[0]), Client(base, tenants[1])

        print("SERVING")
        html = A.raw("/")
        check(len(html) > 20_000 and b"<html" in html.lower(),
              "UI served", f"{len(html) // 1024} KB, single file, no build step")
        check(len(tenants) >= 2, "two businesses seeded", " · ".join(tenants))

        print("\nREAD PATHS")
        d = A.get("/api/v1/dashboard")
        check(all(k in d for k in ("month", "inventory", "receivables", "procurement")),
              "dashboard", f"stock Rs {d['inventory']['stock_cost']:,.0f} · "
                           f"owed Rs {d['receivables']['value']:,.0f}")
        styles = A.items("/api/v1/styles")
        check(styles, "styles", f"{len(styles)}")
        inv = A.items("/api/v1/inventory")
        check(inv, "inventory by size/colour", f"{len(inv)} variant rows")
        custs = A.items("/api/v1/customers")
        check(custs, "customers", f"{len(custs)}")
        check(A.items("/api/v1/sales/invoices"), "invoices",
              f"{len(A.items('/api/v1/sales/invoices'))}")
        recv = A.get("/api/v1/sales/receivables")
        check("buckets" in recv and "by_customer" in recv, "receivables ageing",
              f"Rs {recv['total']:,.0f} across {len(recv['items'])} bills")
        check(isinstance(A.items("/api/v1/procurement/recommendations"), list),
              "AI reorder suggestions",
              f"{len(A.items('/api/v1/procurement/recommendations'))} pending")
        locs = A.items("/api/v1/locations")
        check(locs, "locations", f"{len(locs)}")
        for kind, minrows in (("sales_by_style", 1), ("size_curve", 1),
                              ("gst_summary", 1), ("dead_stock", 0)):
            rows = A.rows(f"/api/v1/reports?kind={kind}")
            check(len(rows) >= minrows, f"report {kind}", f"{len(rows)} rows")

        print("\nSIZE ORDER (the reason size_seq exists)")
        ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL"]
        bad = []
        for s in styles:
            alpha = [x for x in s.get("sizes", []) if x in ORDER]
            want = [x for x in ORDER if x in alpha]
            if alpha != want:
                bad.append(f"{s['style_code']}: {s.get('sizes')}")
        check(not bad, "size header in wearing order across every style",
              "; ".join(bad) if bad else
              " / ".join(styles[0].get("sizes", [])) + "  (first style)")

        # The header list is sorted in Python; the grid is drawn from `variants`,
        # which is ordered by SQL. Deleting `ORDER BY size_seq` from that query
        # left the header correct and scrambled the grid — checking only the
        # header passed the defect through. Check the rows the UI actually draws.
        bad = []
        for s in styles:
            seen, seq = [], []
            for v in s.get("variants", []):
                if v["size"] not in seen:
                    seen.append(v["size"])
                    seq.append(v["size_seq"] if v["size_seq"] is not None else 99999)
            if seq != sorted(seq):
                bad.append(f"{s['style_code']}: {seen}")
        check(not bad, "variant rows come back in size order too (what the grid draws)",
              "; ".join(bad) if bad else
              " / ".join(dict.fromkeys(v["size"] for v in styles[0].get("variants", [])))
              + "  (first style)")

        print("\nSHARED IDENTIFIERS RESOLVE PER TENANT")
        bcs = {r["barcode"] for r in inv if r.get("barcode")}
        shared = next((b for b in sorted(bcs)
                       if any(r.get("barcode") == b
                              for r in B.items("/api/v1/inventory"))), None)
        if check(shared is not None, "a barcode is deliberately shared by both businesses",
                 shared or "none found"):
            a_hit, b_hit = A.get(f"/api/v1/barcode/{shared}"), B.get(f"/api/v1/barcode/{shared}")
            check(a_hit["id"] != b_hit["id"],
                  "same barcode, different garment per business",
                  f"{a_hit['name']} {a_hit['size']}/{a_hit['colour']}  vs  "
                  f"{b_hit['name']} {b_hit['size']}/{b_hit['colour']}")

        print("\nBILLING — closed loop, mixed GST slabs")
        from app.domain.gst import LineInput, compute_invoice   # the production engine

        loc_id = locs[0]["id"]
        at_loc = [r for r in A.items(f"/api/v1/inventory?location={locs[0]['code']}")
                  if r["on_hand"] >= 3]
        cheap = next((r for r in at_loc if 0 < r["unit_price"] <= 2500), None)
        dear = next((r for r in at_loc if r["unit_price"] > 2500), None)
        cust = next((c for c in custs if not c.get("is_walkin")), custs[0])

        if check(cheap and dear, "stock exists on both sides of the Rs 2,500 slab line",
                 f"Rs {cheap['unit_price']:,.0f} and Rs {dear['unit_price']:,.0f}"
                 if cheap and dear else "seed lacks one side"):
            before = {r["product_id"]: r["on_hand"] for r in at_loc}
            doc = A.post("/api/v1/sales/invoices", {
                "customer_id": cust["id"], "location_id": loc_id,
                "lines": [{"product_id": cheap["product_id"], "quantity": 2},
                          {"product_id": dear["product_id"], "quantity": 1}]})

            rates = sorted({l["gst_rate"] for l in doc["lines"]})
            check(rates == [5.0, 18.0], "one invoice, two GST slabs",
                  f"{doc['invoice_number']} — 5% and 18% on the same bill")

            expected = compute_invoice(
                [LineInput(product_id=l["product_id"], quantity=Decimal(str(l["quantity"])),
                           unit_price=Decimal(str(l["unit_price"])),
                           discount_pct=Decimal(str(l.get("discount_pct") or 0)),
                           hsn_code=l.get("hsn_code")) for l in doc["lines"]],
                seller_state=d["tenant"]["state_code"] if "tenant" in d else None,
                buyer_state=doc["place_of_supply"])
            served, engine = Decimal(str(doc["grand_total"])), expected.grand_total
            check(abs(served - engine) < Decimal("0.01"),
                  "total recomputed independently by the shared GST engine",
                  f"Rs {served:,.2f} served vs Rs {engine:,.2f} engine")

            lines_add_up = abs(sum(Decimal(str(l["line_total"])) for l in doc["lines"])
                               + Decimal(str(doc["round_off"]))
                               - Decimal(str(doc["grand_total"]))) < Decimal("0.01")
            check(lines_add_up, "printed lines add up to the printed total",
                  f"round-off Rs {doc['round_off']:+.2f}")

            intra = not doc["is_interstate"]
            check((doc["igst_total"] == 0 and doc["cgst_total"] > 0) if intra
                  else (doc["cgst_total"] == 0 and doc["igst_total"] > 0),
                  "place of supply chose the right taxes",
                  "CGST+SGST (intra-state)" if intra else "IGST (inter-state)")
            # CGST and SGST may legitimately differ by one paisa on an odd tax
            # total — the engine splits as cgst=q(tax/2), sgst=tax-cgst so the
            # two halves always sum back to the tax exactly. Assert the sum, and
            # that the halves are within a paisa of each other.
            halves = Decimal(str(doc["cgst_total"])) + Decimal(str(doc["sgst_total"]))
            check((halves == Decimal(str(doc["tax_total"]))
                   and abs(doc["cgst_total"] - doc["sgst_total"]) <= 0.01) if intra
                  else Decimal(str(doc["igst_total"])) == Decimal(str(doc["tax_total"])),
                  "the tax halves reconcile to the tax total exactly",
                  f"Rs {doc['cgst_total']:,.2f} + Rs {doc['sgst_total']:,.2f} "
                  f"= Rs {doc['tax_total']:,.2f}")
            check(bool(doc.get("amount_in_words")), "amount in words (Indian numbering)",
                  doc.get("amount_in_words", "")[:70])

            after = {r["product_id"]: r["on_hand"]
                     for r in A.items(f"/api/v1/inventory?location={locs[0]['code']}")}
            check(before[cheap["product_id"]] - after[cheap["product_id"]] == 2
                  and before[dear["product_id"]] - after[dear["product_id"]] == 1,
                  "selling decremented stock",
                  f"{before[cheap['product_id']]:g}->{after[cheap['product_id']]:g} and "
                  f"{before[dear['product_id']]:g}->{after[dear['product_id']]:g}")

            open_now = A.get("/api/v1/sales/receivables")
            check(any(r["invoice_number"] == doc["invoice_number"] for r in open_now["items"]),
                  "invoice became a receivable", doc["invoice_number"])

            # Money is allocated OLDEST-FIRST, so paying the new bill's amount does
            # not settle the new bill — it settles the oldest one outstanding. That
            # is the point of the convention, so assert it rather than around it.
            mine = [r for r in A.get("/api/v1/sales/receivables")["items"]
                    if r["customer_id"] == cust["id"]]
            mine.sort(key=lambda r: (r["invoice_date"], r["invoice_number"]))
            oldest = mine[0]
            owed_before = sum(r["balance"] for r in mine)

            pay = A.post("/api/v1/sales/payments", {
                "customer_id": cust["id"], "amount": oldest["balance"], "method": "upi"})
            check([a["invoice_number"] for a in pay["applied"]] == [oldest["invoice_number"]]
                  and pay["on_account"] == 0,
                  "payment allocated oldest-first",
                  f"{pay['payment_number']} Rs {pay['amount']:,.2f} -> "
                  f"{oldest['invoice_number']} ({oldest['days_overdue']}d overdue)")

            settled = A.get(f"/api/v1/sales/invoices/{oldest['id']}")
            check(settled["status"] == "paid" and abs(settled["balance_due"]) < 0.01,
                  "that bill is now closed", f"status {settled['status']}")

            owed_after = sum(r["balance"] for r in A.get("/api/v1/sales/receivables")["items"]
                             if r["customer_id"] == cust["id"])
            check(abs((owed_before - owed_after) - oldest["balance"]) < 0.01,
                  "ledger moved by exactly the amount received",
                  f"Rs {owed_before:,.2f} -> Rs {owed_after:,.2f}")

        print("\nNEGATIVE CONTROLS (refused — and refused for the RIGHT REASON)")

        # A refusal that happens by accident is not a guard. Deleting the tenant
        # predicate from the product lookup still produced HTTP 422 here, because
        # the stock check then failed on a product with no local inventory — and
        # the error leaked the other business's garment name while doing it. A
        # status-only assertion passed that defect straight through. So every
        # negative control below asserts the REASON.
        def refused(label, path, payload, must_say, must_not_say=(), client=None):
            st, body = (client or A).post_status(path, payload)
            reason = (json.loads(body).get("error", body)
                      if body.strip().startswith("{") else body).lower()
            ok = st >= 400 and any(m in reason for m in must_say) \
                and not any(m.lower() in reason for m in must_not_say)
            check(ok, label, f"HTTP {st} · {reason[:72]}")

        b_inv = B.items("/api/v1/inventory")
        b_custs = B.items("/api/v1/customers")
        b_locs = B.items("/api/v1/locations")

        if cheap:
            refused("oversell refused, naming stock",
                    "/api/v1/sales/invoices",
                    {"customer_id": cust["id"], "location_id": loc_id,
                     "lines": [{"product_id": cheap["product_id"], "quantity": 99999}]},
                    must_say=("in stock",))
        if b_inv:
            refused("selling another business's garment refused BY THE TENANT CHECK",
                    "/api/v1/sales/invoices",
                    {"customer_id": cust["id"], "location_id": loc_id,
                     "lines": [{"product_id": b_inv[0]["product_id"], "quantity": 1}]},
                    must_say=("not found for this business",),
                    # must not fall through to the stock check, which would both
                    # pass vacuously and leak the other business's product name
                    must_not_say=("in stock", b_inv[0]["name"]))
        if b_custs:
            refused("billing another business's customer refused",
                    "/api/v1/sales/invoices",
                    {"customer_id": b_custs[0]["id"], "location_id": loc_id,
                     "lines": [{"product_id": cheap["product_id"], "quantity": 1}]},
                    must_say=("unknown customer",), must_not_say=(b_custs[0]["name"],))
            refused("taking payment for another business's customer refused",
                    "/api/v1/sales/payments",
                    {"customer_id": b_custs[0]["id"], "amount": 100, "method": "cash"},
                    must_say=("unknown customer",), must_not_say=(b_custs[0]["name"],))
        if b_locs and cheap:
            refused("billing out of another business's shop refused",
                    "/api/v1/sales/invoices",
                    {"customer_id": cust["id"], "location_id": b_locs[0]["id"],
                     "lines": [{"product_id": cheap["product_id"], "quantity": 1}]},
                    must_say=("unknown location",))
        refused("empty invoice refused", "/api/v1/sales/invoices",
                {"customer_id": cust["id"], "location_id": loc_id, "lines": []},
                must_say=("at least one line",))
        refused("zero-quantity line refused", "/api/v1/sales/invoices",
                {"customer_id": cust["id"], "location_id": loc_id,
                 "lines": [{"product_id": cheap["product_id"], "quantity": 0}]},
                must_say=("quantity must be positive",))
        refused("negative payment refused", "/api/v1/sales/payments",
                {"customer_id": cust["id"], "amount": -500, "method": "cash"},
                must_say=("must be positive",))

        print("\nIMPORT — the screen you run with the shop's own file")
        SHEET = (
            "Particulars\tArticle No\tSize\tColour\tHSN Code\tTax %\tUnit\t"
            "Purchase Price\tMRP\tClosing Stock\n"
            "Anarkali Gown Small Maroon\tANK-S\tSmall\tMaroon\t6104\t5%\tPcs\t"
            "Rs. 1,450.00\t3,299.00\t6\n"
            "Anarkali Gown Medium Maroon\tANK-M\tMED\tMAROON\t6104\t5\tNos\t1450\t3,299\t4\n"
            "Anarkali Gown Large Maroon\tANK-L\tLarge\tmaroon\t6104\t0.05\tPC\t1450\t3299\t2\n"
            "Chikankari Kurti S White\tCHK-S\tS\tWhite\t6106\t5\tPcs\t690\t1,699\t10\n"
            "\tORPHAN-1\tL\tWhite\t6106\t5\tPcs\t690\t1699\t3\n"
            "Banarasi Saree Free Gold\tBNS-F\tFree Size\tGold\t\t28\tNos\t4200\t\t2\n"
            "Kids Tee Toddler Red\tKID-T3\tToddler-3\tRed\t6109\t5\tPcs\t180\t399\t(4)\n")

        an = A.post("/api/v1/import/analyse", {"text": SHEET})
        s = an["summary"]
        check(not an["fatal"] and s["total"] == 7, "messy sheet read",
              f"{s['total']} rows, {s['styles']} styles")
        check(an["mapping"]["hsn"] == "HSN Code" and an["mapping"]["sku"] == "Article No",
              "columns matched — HSN is the HSN, Article No is the code",
              f"hsn<-{an['mapping']['hsn']}, sku<-{an['mapping']['sku']}")
        check(s["styles"] == 4,
              "three sizes of one gown collapse into ONE style",
              f"{s['styles']} styles from 7 rows")
        by_line = {r["line"]: r for r in an["rows"]}
        check(not by_line[6]["ok"], "the row with no item name is refused", "line 6")
        check(by_line[8]["qty"] == 0, "negative stock '(4)' clamped to zero", "line 8")
        check(by_line[3]["size"] == "M", "'MED' normalised to M")
        check(by_line[2]["cost"] == 1450, "'Rs. 1,450.00' cleaned to 1450")
        check(all("gst_rate" not in r for r in an["rows"]),
              "no GST rate is written onto any product — the slab is per-bill")

        before_products = len(A.items("/api/v1/inventory"))
        before_b = len(B.items("/api/v1/inventory"))
        res = A.post("/api/v1/import/commit", {"text": SHEET})
        check(res["products_created"] == 6 and res["rows_refused"] == 1,
              "committed the good rows, skipped the bad one",
              f"{res['products_created']} created, {res['rows_refused']} refused")

        after = A.items("/api/v1/inventory")
        check(len(after) == before_products + 6, "new variants appear in inventory",
              f"{before_products} -> {len(after)}")
        gown = [r for r in after if r.get("style_code") == "ANARKALI-GOWN"]
        check([g["size"] for g in sorted(gown, key=lambda g: g["size_seq"] or 99999)]
              == ["S", "M", "L"],
              "imported sizes come back in wearing order",
              " / ".join(g["size"] for g in sorted(gown, key=lambda g: g["size_seq"] or 99999)))

        # Re-import: a shopkeeper WILL paste a corrected sheet. Doubling their
        # stock the second time is the kind of bug that ends a pilot.
        again = A.post("/api/v1/import/commit", {"text": SHEET})
        after2 = A.items("/api/v1/inventory")
        check(again["products_created"] == 0 and again["products_updated"] == 6,
              "re-import updates instead of duplicating",
              f"{again['products_created']} created, {again['products_updated']} updated")
        check(len(after2) == len(after), "no new inventory rows on re-import")
        check(sum(r["on_hand"] for r in after2 if r.get("style_code") == "ANARKALI-GOWN") == 12,
              "stock is SET to the sheet, not added to it", "12 pieces, not 24")

        print("\nIMPORT — tenant safety")
        check(len(B.items("/api/v1/inventory")) == before_b,
              "the other business sees none of it", f"still {before_b} rows")
        b_skus = {r["sku"] for r in B.items("/api/v1/inventory")}
        check(not (b_skus & {"ANK-S", "ANK-M", "ANK-L", "CHK-S", "BNS-F", "KID-T3"}),
              "and none of the imported codes leaked across")

        # A's codes must not show up as "already in the system" for B — that
        # warning would tell one shop which codes its competitor uses.
        b_view = B.post("/api/v1/import/analyse", {"text": SHEET})
        check(not any("already in the system" in w
                      for r in b_view["rows"] for w in r["warnings"]),
              "A's codes are invisible to B's import preview",
              "no cross-tenant 'already in the system' hint")
        a_view = A.post("/api/v1/import/analyse", {"text": SHEET})
        check(sum(1 for r in a_view["rows"] if r["ok"]
                  and any("already in the system" in w for w in r["warnings"])) == 6,
              "but A is correctly told about A's own codes", "6 of 6 recognised")

        # Both shops importing the same sheet must each get their OWN rows.
        # Captured here, not earlier: receiving and returns legitimately change
        # A's inventory in between, and a stale baseline would fail for the
        # wrong reason.
        a_rows_before_b_import = len(A.items("/api/v1/inventory"))
        b_res = B.post("/api/v1/import/commit", {"text": SHEET})
        check(b_res["products_created"] == 6,
              "B's import creates B's own products, it does not adopt A's",
              f"{b_res['products_created']} created, {b_res['products_updated']} updated")
        b_after = B.items("/api/v1/inventory")
        check(len(b_after) == before_b + 6, "and they appear in B's inventory",
              f"{before_b} -> {len(b_after)}")

        a_styles = {s["style_code"]: s["id"] for s in A.items("/api/v1/styles")}
        b_styles = {s["style_code"]: s["id"] for s in B.items("/api/v1/styles")}
        shared = set(a_styles) & set(b_styles)
        check(shared and all(a_styles[c] != b_styles[c] for c in shared),
              "the same style code in both shops is two separate styles",
              f"{len(shared)} shared code(s), all distinct rows")
        print("\nGST EXPORTS — GSTR-1 and GSTR-3B")
        months = A.items("/api/v1/tax/months")
        check(months, "trading months are available to report on", f"{len(months)}")
        # The newest month is usually part-way through; pick a full one.
        month = months[1] if len(months) > 1 else months[0]
        tax = A.get(f"/api/v1/tax/summary?month={month}")
        g1, g3 = tax["gstr1"], tax["gstr3b"]

        check(g1["documents"] > 0, f"GSTR-1 has outward documents for {month}",
              f"{g1['documents']} docs, Rs {g1['taxable_total']:,.0f} taxable")
        # The slab breakdown IS the return. If it does not reconcile to the
        # header the shop files two different numbers for the same month.
        slab_sum = round(sum(r["taxable"] for r in g1["by_slab"]), 2)
        check(abs(slab_sum - g1["taxable_total"]) < 0.05,
              "slab rows reconcile to the taxable total",
              f"Rs {slab_sum:,.2f} vs Rs {g1['taxable_total']:,.2f}")
        tax_sum = round(sum(r["tax"] for r in g1["by_slab"]), 2)
        check(abs(tax_sum - g1["tax_total"]) < 0.05,
              "slab tax reconciles to the tax total",
              f"Rs {tax_sum:,.2f} vs Rs {g1['tax_total']:,.2f}")
        check(all(r["rate"] in (0, 5, 12, 18, 28) for r in g1["by_slab"]),
              "every slab is a real GST rate",
              " / ".join(f"{r['rate']:g}%" for r in g1["by_slab"]))

        # 3B must be derived from the SAME rows as 1. A mismatch between the two
        # returns is what gets a shop a departmental notice.
        check(abs(g3["outward"]["tax"] - g1["tax_total"]) < 0.05,
              "GSTR-3B outward tax equals GSTR-1 tax — the two returns agree",
              f"Rs {g3['outward']['tax']:,.2f}")
        recomputed = {h: round(max(0.0, g3["outward"][h] - g3["inward"][h]), 2)
                      for h in ("cgst", "sgst", "igst")}
        check(recomputed == g3["net_payable"],
              "credit is offset head by head, not as one netted lump",
              f"payable Rs {g3['net_payable_total']:,.2f} after Rs {g3['inward']['tax']:,.2f} credit")
        check(g3["net_payable_total"] >= 0, "net payable is never negative",
              f"Rs {g3['net_payable_total']:,.2f}")
        # Input credit must be recomputed from an INDEPENDENT source, not
        # merely shown to differ from the other tenant's. A mutation that
        # SWAPS the two tenants' credit satisfies "these differ" perfectly —
        # which is exactly what slipped through the first time this was
        # written. Count A's own bills for the month and require a match.
        own_bills = [b for b in A.items("/api/v1/procurement/bills")
                     if (b.get("bill_date") or "")[:7] == month]
        own_gross = round(sum(b["grand_total"] for b in own_bills), 2)
        claimed = round(g3["inward"]["taxable"] + g3["inward"]["tax"], 2)
        # Compare the AMOUNT, not the count. Both shops are seeded with the
        # same number of bills in the same month, so a mutation that reads the
        # wrong tenant's bills still produced a matching COUNT and sailed
        # through. The rupees differ; the counts do not.
        check(abs(claimed - own_gross) < 1.0,
              "input credit equals THIS shop's own bills, to the rupee",
              f"3B claims Rs {claimed:,.2f}, payables ledger has Rs {own_gross:,.2f} "
              f"across {len(own_bills)} bill(s)")

        # Snapshot every month now, so the credit-note effect can be measured
        # later against a real before-value rather than assumed.
        tax_by_month = {m: A.get(f"/api/v1/tax/summary?month={m}")["gstr1"]
                        for m in months[:3]}

        csv1 = A.raw(f"/api/v1/tax/gstr1.csv?month={month}").decode("utf-8-sig")
        check(all(t in csv1 for t in ("4A-B2B", "7-B2CS", "12-HSN")),
              "GSTR-1 CSV carries all three portal tables",
              f"{len(csv1.splitlines())} lines")
        check("NOT a portal upload" in csv1,
              "the CSV says plainly that it is a working paper, not a filing")
        csv3 = A.raw(f"/api/v1/tax/gstr3b.csv?month={month}").decode("utf-8-sig")
        check("3.1(a)" in csv3 and "4(A)(5)" in csv3,
              "GSTR-3B CSV uses the portal's section numbers")

        b_months = B.items("/api/v1/tax/months")
        b_tax = B.get(f"/api/v1/tax/summary?month={b_months[0]}") if b_months else None
        check(b_tax is None or b_tax["gstr1"]["taxable_total"] != g1["taxable_total"],
              "the other business reports its own figures, not these",
              f"A Rs {g1['taxable_total']:,.0f} vs B Rs "
              f"{b_tax['gstr1']['taxable_total']:,.0f}" if b_tax else "n/a")

        print("\nLABELS — printable EAN-13 tags")
        LB, BC = 'class="lb"', 'class="bc"'      # kept out of the f-strings
        # Inventory has one row per (product, location), so the same product
        # appears twice. De-duplicate or the count assertion is meaningless.
        prod_ids = list(dict.fromkeys(r["product_id"] for r in inv))[:8]
        sheet = A.raw("/api/v1/labels/print?product_ids=" + ",".join(prod_ids)).decode()
        check(sheet.startswith("<!DOCTYPE html>") and "@page" in sheet,
              "a printable A4 page is served", f"{len(sheet) // 1024} KB")
        check(sheet.count(LB) == len(prod_ids),
              "one label per product asked for",
              f"{sheet.count(LB)} of {len(prod_ids)}")
        check(sheet.count(BC) > 0, "barcodes are drawn as SVG",
              f"{sheet.count(BC)} symbols")

        one = A.raw(f"/api/v1/labels/print?product_ids={prod_ids[0]}&copies=4").decode()
        check(one.count(LB) == 4, "copies multiply the tags", "4 from 1 product")

        # The whole reason this generator validates rather than trusts: a wrong
        # check digit is a tag that will not scan at the till.
        from erp.labels import check_digit, ean13   # noqa: PLC0415
        check(check_digit("890123456789") == "0" and check_digit("400638133393") == "1",
              "EAN-13 check digit matches known-good barcodes")
        fixed, note = ean13("8901234567891")
        check(fixed == "8901234567890" and "corrected" in note,
              "a bad check digit is corrected rather than printed", note)
        check(ean13(None) == (None, "no barcode on this item"),
              "an item with no barcode is reported, not faked")

        b_prod = b_inv[0]["product_id"]
        cross = A.raw(f"/api/v1/labels/print?product_ids={b_prod}").decode()
        check(cross.count(LB) == 0,
              "another business's product prints NO label", "0 tags")

        print("\nRECEIVING — stock can finally go up")
        open_pos = [p for p in A.items("/api/v1/receiving/open-pos")
                    if not p["fully_received"]]
        check(open_pos, "a purchase order is awaiting delivery", f"{len(open_pos)} open")
        po = A.get("/api/v1/receiving/po/" + open_pos[0]["id"])
        line = po["lines"][0]
        cost_before, hand_before = line["current_cost"], line["on_hand"]
        half = max(1, int(line["ordered_qty"] // 2))
        new_cost = round(cost_before * 1.25, 2)

        grn = A.post("/api/v1/receiving/receive", {
            "purchase_order_id": po["id"],
            "supplier_invoice": "SUP/2026/881",
            "lines": [{"po_line_id": line["id"], "product_id": line["product_id"],
                       "accepted_qty": half, "rejected_qty": 1,
                       "unit_cost": new_cost, "reject_reason": "torn seam"}]})
        check(grn["accepted"] == half and grn["rejected"] == 1,
              "delivery booked in", f"{grn['grn_number']} — {grn['accepted']:g} in, 1 rejected")

        po2 = A.get("/api/v1/receiving/po/" + po["id"])
        l2 = next(l for l in po2["lines"] if l["id"] == line["id"])
        check(l2["on_hand"] == hand_before + half, "accepted stock went ON the shelf",
              f"{hand_before:g} -> {l2['on_hand']:g}")
        # Rejected pieces must not become stock and must not be paid for.
        check(abs(grn["total_value"] - half * new_cost) < 0.01,
              "rejected pieces are not valued", f"Rs {grn['total_value']:,.2f}")

        # A second delivery at a DIFFERENT price, now that there is stock to
        # average against. The first receipt often lands on an empty shelf,
        # where the weighted average and the invoice price are the same number
        # — an assertion that cannot distinguish the two proves nothing.
        hand_mid, cost_mid = l2["on_hand"], l2["current_cost"]
        second_cost = round(cost_mid * 0.6, 2)
        rest = max(1, int(l2["outstanding"]))
        A.post("/api/v1/receiving/receive", {
            "purchase_order_id": po["id"],
            "lines": [{"po_line_id": line["id"], "product_id": line["product_id"],
                       "accepted_qty": rest, "unit_cost": second_cost}]})
        po3 = A.get("/api/v1/receiving/po/" + po["id"])
        l3 = next(l for l in po3["lines"] if l["id"] == line["id"])
        expected = round((hand_mid * cost_mid + rest * second_cost) / (hand_mid + rest), 2)
        check(hand_mid > 0 and abs(second_cost - expected) > 0.5,
              "the averaging test is not vacuous",
              f"invoice price Rs {second_cost:,.2f} differs from the average Rs {expected:,.2f}")
        check(abs(l3["current_cost"] - expected) < 0.05,
              "cost basis re-averaged, not overwritten",
              f"{hand_mid:g}@Rs {cost_mid:,.2f} + {rest}@Rs {second_cost:,.2f} -> "
              f"Rs {l3['current_cost']:,.2f}, not Rs {second_cost:,.2f}")
        check(l2["outstanding"] == line["ordered_qty"] - half,
              "the rest is still outstanding", f"{l2['outstanding']:g} due")
        check(grn["po_status"] in ("part_received", "received"),
              "purchase order status moved", grn["po_status"])

        print("\nRECEIVING — refusals")
        refused("negative quantity refused", "/api/v1/receiving/receive",
                {"purchase_order_id": po["id"],
                 "lines": [{"po_line_id": line["id"], "product_id": line["product_id"],
                            "accepted_qty": -3}]},
                must_say=("cannot be negative",))
        refused("empty receipt refused", "/api/v1/receiving/receive",
                {"purchase_order_id": po["id"], "lines": []},
                must_say=("at least one line",))
        refused("receiving another business's garment refused",
                "/api/v1/receiving/receive",
                {"lines": [{"product_id": b_inv[0]["product_id"], "accepted_qty": 2}]},
                must_say=("not found for this business",),
                must_not_say=(b_inv[0]["name"],))
        # Asserting only "status >= 400" let a mutation through once already:
        # the guard was removed, the call crashed later with a 500, and the
        # check went green. The REASON is the thing being tested.
        refused("receiving against another business's order refused",
                "/api/v1/receiving/receive",
                {"purchase_order_id": po["id"],
                 "lines": [{"product_id": b_inv[0]["product_id"], "accepted_qty": 1}]},
                must_say=("unknown purchase order",),
                must_not_say=("internal_error",), client=B)

        print("\nRETURNS — a garment comes back")
        target = None
        for inv in A.items("/api/v1/sales/invoices?status=posted")[:12]:
            d = A.get("/api/v1/returns/returnable/" + inv["id"])
            if d.get("any_returnable") and len(d["lines"]) >= 2:
                target = d
                break
        check(target is not None, "found a bill with something returnable")
        rl0, rl1 = target["lines"][0], target["lines"][-1]

        def stock_of(pid):
            rows = [r for r in A.items("/api/v1/inventory") if r["product_id"] == pid]
            return sum(r["on_hand"] for r in rows)

        s0, s1 = stock_of(rl0["product_id"]), stock_of(rl1["product_id"])
        cn = A.post("/api/v1/returns/create", {
            "invoice_id": target["id"], "reason": "size exchange",
            "refund_mode": "credit",
            "lines": [{"invoice_line_id": rl0["id"], "quantity": 1, "restock": True},
                      {"invoice_line_id": rl1["id"], "quantity": 1, "restock": False,
                       "condition": "stained"}]})
        check(cn["restocked"] == 1 and cn["written_off"] == 1,
              "credit note raised", f"{cn['cn_number']} — Rs {cn['grand_total']:,.2f}")
        check(stock_of(rl0["product_id"]) == s0 + 1,
              "a resaleable return goes BACK on the shelf", f"{s0:g} -> {s0 + 1:g}")
        check(stock_of(rl1["product_id"]) == s1,
              "a written-off return does NOT", f"stayed at {s1:g}")

        # The whole point: GST comes off at the rate the ORIGINAL bill charged.
        rates = {l["gst_rate"] for l in (rl0, rl1)}
        expected_tax = sum(
            round(l["taxable_value"] / l["quantity"] * l["gst_rate"] / 100, 2)
            for l in (rl0, rl1))
        got_tax = cn["cgst_total"] + cn["sgst_total"] + cn["igst_total"]
        check(abs(got_tax - expected_tax) < 0.05,
              "GST reversed at the ORIGINAL line rates",
              f"rates {sorted(rates)} -> Rs {got_tax:,.2f}")

        # A credit note must REDUCE outward supply in the month it falls in.
        # The earlier GST block ran on a historical month with no returns in
        # it, so flipping the sign on credit notes there changed nothing and a
        # mutation sailed through. Assert it where a credit note actually
        # exists: the month this one was just raised in.
        cn_month = cn["note_date"][:7]
        before_cn = tax_by_month.get(cn_month)
        now_cn = A.get(f"/api/v1/tax/summary?month={cn_month}")["gstr1"]
        if before_cn is not None:
            check(now_cn["taxable_total"] < before_cn["taxable_total"],
                  "a credit note REDUCES outward supply in its month",
                  f"Rs {before_cn['taxable_total']:,.2f} -> "
                  f"Rs {now_cn['taxable_total']:,.2f}")
            check(now_cn["credit_notes"] >= 1,
                  "and it is counted as a credit note", f"{now_cn['credit_notes']}")

        after = A.get("/api/v1/returns/returnable/" + target["id"])
        a0 = next(l for l in after["lines"] if l["id"] == rl0["id"])
        check(a0["returnable"] == rl0["returnable"] - 1,
              "that unit can no longer be returned again",
              f"{rl0['returnable']:g} -> {a0['returnable']:g}")

        print("\nRETURNS — refusals")
        refused("returning more than was sold refused", "/api/v1/returns/create",
                {"invoice_id": target["id"],
                 "lines": [{"invoice_line_id": rl0["id"], "quantity": 9999}]},
                must_say=("left to return",))
        refused("empty credit note refused", "/api/v1/returns/create",
                {"invoice_id": target["id"], "lines": []},
                must_say=("at least one line",))
        refused("an invented refund mode refused", "/api/v1/returns/create",
                {"invoice_id": target["id"], "refund_mode": "cheque",
                 "lines": [{"invoice_line_id": rl1["id"], "quantity": 1}]},
                must_say=("credit", "refund"))
        refused("returning against another business's bill refused",
                "/api/v1/returns/create",
                {"invoice_id": target["id"],
                 "lines": [{"invoice_line_id": rl0["id"], "quantity": 1}]},
                must_say=("unknown invoice for this business",),
                must_not_say=("internal_error",), client=B)

        a_ids = {r["product_id"] for r in A.items("/api/v1/inventory")}
        b_ids = {r["product_id"] for r in b_after}
        check(not (a_ids & b_ids), "and not one product row is shared between them")
        check(len(A.items("/api/v1/inventory")) == a_rows_before_b_import,
              "importing into B did not touch A's catalogue")


    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    failed = [c for c in CHECKS if not c[0]]
    print("\n" + "=" * 70)
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        for _, label, detail in failed:
            print(f"  FAILED: {label}  {detail}")
        print("DEMO IS NOT SHOWABLE")
        return 1
    print("DEMO VERIFIED — safe to put in front of a customer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
