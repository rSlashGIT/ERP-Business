"""GSTR-1 and GSTR-3B extracts.

READ THIS BEFORE PROMISING ANYTHING TO A SHOP
---------------------------------------------
These are **working papers**, not a filing. They are built from the same
invoices the shop raised, they add up, and they are laid out the way the GST
portal's offline utility expects — but:

* nothing here has been uploaded to a live GST portal, so the column order has
  not been proven against the real validator;
* the portal ingests a JSON payload or its own Excel template, not a bare CSV,
  so a CA or the offline tool still sits between this file and a return;
* B2B versus B2C is decided purely on whether the customer has a GSTIN on file,
  which is right in practice and wrong the moment a GSTIN is missing from the
  master.

Say "this is what you hand your accountant, and it will take him twenty minutes
instead of two days". Do not say "this files your return".

WHAT IS ACTUALLY CORRECT HERE
-----------------------------
The arithmetic. Outward liability comes from `sales_invoices` NET of
`credit_notes`, split by slab and by place of supply. Input credit comes from
`supplier_bill_lines`. Both are per-tenant and per-month, and 3B is derived
from the same rows as 1 rather than computed a second way — so the two cannot
disagree with each other, which is the failure mode that gets a shop a notice.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

#: Post-22-Sept-2025 slabs. 12% and 28% were abolished but still appear on
#: historical invoices, so the report must be able to show them.
KNOWN_SLABS = (0.0, 5.0, 12.0, 18.0, 28.0)


def _month_bounds(month: str) -> Tuple[str, str]:
    """'2026-07' -> ('2026-07-01', '2026-07-31'), lexicographic-safe."""
    y, m = month.split("-")
    y, m = int(y), int(m)
    nxt = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    return f"{y:04d}-{m:02d}-01", nxt


def available_months(conn: sqlite3.Connection, tenant_id: str) -> List[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(invoice_date,1,7) m FROM sales_invoices"
        " WHERE tenant_id=? AND status!='cancelled' ORDER BY m DESC",
        (tenant_id,)).fetchall()
    return [r[0] for r in rows]


# ─────────────────────────── the shared figures ───────────────────────────

def _outward(conn: sqlite3.Connection, tenant_id: str, month: str) -> List[Dict[str, Any]]:
    """Every outward LINE in the month, credit notes carried as negatives.

    One query shape for both reports. GSTR-1 groups these; 3B sums them. Two
    separate queries would eventually disagree, and a mismatch between the two
    returns is exactly what triggers a departmental notice.
    """
    lo, hi = _month_bounds(month)
    rows = conn.execute(
        "SELECT 'invoice' doc, i.invoice_number num, i.invoice_date dt,"
        " i.is_interstate, i.place_of_supply, c.gstin, c.name customer,"
        " l.gst_rate, l.taxable_value, l.cgst, l.sgst, l.igst, l.hsn_code, l.quantity"
        " FROM sales_invoice_lines l"
        " JOIN sales_invoices i ON i.id=l.invoice_id AND i.tenant_id=l.tenant_id"
        " JOIN customers c ON c.id=i.customer_id AND c.tenant_id=i.tenant_id"
        " WHERE l.tenant_id=? AND i.status!='cancelled'"
        "   AND i.invoice_date>=? AND i.invoice_date<?"
        " UNION ALL"
        " SELECT 'credit_note', n.cn_number, n.note_date,"
        " i.is_interstate, i.place_of_supply, c.gstin, c.name,"
        " nl.gst_rate, -nl.taxable_value, -nl.cgst, -nl.sgst, -nl.igst, NULL, -nl.quantity"
        " FROM credit_note_lines nl"
        " JOIN credit_notes n ON n.id=nl.credit_note_id AND n.tenant_id=nl.tenant_id"
        " JOIN sales_invoices i ON i.id=n.invoice_id AND i.tenant_id=n.tenant_id"
        " JOIN customers c ON c.id=n.customer_id AND c.tenant_id=n.tenant_id"
        " WHERE nl.tenant_id=? AND n.note_date>=? AND n.note_date<?",
        (tenant_id, lo, hi, tenant_id, lo, hi)).fetchall()
    cols = ["doc", "num", "dt", "is_interstate", "place_of_supply", "gstin",
            "customer", "gst_rate", "taxable", "cgst", "sgst", "igst", "hsn", "qty"]
    return [dict(zip(cols, r)) for r in rows]


def _inward(conn: sqlite3.Connection, tenant_id: str, month: str) -> List[Dict[str, Any]]:
    """Input tax credit — what the shop paid its suppliers in the month."""
    lo, hi = _month_bounds(month)
    rows = conn.execute(
        "SELECT b.bill_number, b.bill_date, s.name supplier, s.code,"
        " bl.gst_rate, bl.taxable_value, bl.cgst, bl.sgst, bl.igst, bl.hsn_code"
        " FROM supplier_bill_lines bl"
        " JOIN supplier_bills b ON b.id=bl.bill_id AND b.tenant_id=bl.tenant_id"
        " LEFT JOIN suppliers s ON s.id=b.supplier_id AND s.tenant_id=b.tenant_id"
        " WHERE bl.tenant_id=? AND b.status!='cancelled'"
        "   AND b.bill_date>=? AND b.bill_date<?",
        (tenant_id, lo, hi)).fetchall()
    cols = ["bill_number", "bill_date", "supplier", "supplier_code",
            "gst_rate", "taxable", "cgst", "sgst", "igst", "hsn"]
    return [dict(zip(cols, r)) for r in rows]


# ─────────────────────────── GSTR-1 ───────────────────────────

def gstr1_summary(conn: sqlite3.Connection, tenant_id: str, month: str) -> Dict[str, Any]:
    """What the screen shows before anyone downloads anything."""
    lines = _outward(conn, tenant_id, month)
    b2b: Dict[Tuple, Dict[str, float]] = {}
    b2cs: Dict[Tuple, Dict[str, float]] = {}
    hsn: Dict[Tuple, Dict[str, float]] = {}

    for r in lines:
        rate = float(r["gst_rate"] or 0)
        pos = r["place_of_supply"] or ""
        # A registered buyer goes in B2B, an unregistered one in the B2C
        # summary. That is the only thing separating the two tables.
        bucket = b2b if (r["gstin"] or "").strip() else b2cs
        key = ((r["gstin"] or "").strip(), r["customer"], pos, rate) if bucket is b2b \
            else (pos, rate)
        acc = bucket.setdefault(key, {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0,
                                      "igst": 0.0, "docs": set()})
        acc["taxable"] += float(r["taxable"] or 0)
        acc["cgst"] += float(r["cgst"] or 0)
        acc["sgst"] += float(r["sgst"] or 0)
        acc["igst"] += float(r["igst"] or 0)
        acc["docs"].add(r["num"])

        h = (r["hsn"] or "—", rate)
        ha = hsn.setdefault(h, {"taxable": 0.0, "tax": 0.0, "qty": 0.0})
        ha["taxable"] += float(r["taxable"] or 0)
        ha["tax"] += float(r["cgst"] or 0) + float(r["sgst"] or 0) + float(r["igst"] or 0)
        ha["qty"] += float(r["qty"] or 0)

    def totals(d):
        return {
            "rows": len(d),
            "taxable": round(sum(v["taxable"] for v in d.values()), 2),
            "tax": round(sum(v["cgst"] + v["sgst"] + v["igst"] for v in d.values()), 2),
        }

    by_slab = {}
    for r in lines:
        rate = float(r["gst_rate"] or 0)
        a = by_slab.setdefault(rate, {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0})
        a["taxable"] += float(r["taxable"] or 0)
        a["cgst"] += float(r["cgst"] or 0)
        a["sgst"] += float(r["sgst"] or 0)
        a["igst"] += float(r["igst"] or 0)

    return {
        "month": month,
        "b2b": totals(b2b), "b2cs": totals(b2cs),
        "documents": len({r["num"] for r in lines}),
        "credit_notes": len({r["num"] for r in lines if r["doc"] == "credit_note"}),
        "hsn_rows": len(hsn),
        "by_slab": [
            {"rate": k, **{kk: round(vv, 2) for kk, vv in v.items()},
             "tax": round(v["cgst"] + v["sgst"] + v["igst"], 2)}
            for k, v in sorted(by_slab.items())
        ],
        "taxable_total": round(sum(float(r["taxable"] or 0) for r in lines), 2),
        "tax_total": round(sum(float(r["cgst"] or 0) + float(r["sgst"] or 0)
                               + float(r["igst"] or 0) for r in lines), 2),
    }


def gstr1_csv(conn: sqlite3.Connection, tenant_id: str, month: str) -> str:
    """B2B, B2C-summary and HSN tables in one CSV, portal table names in column A."""
    lines = _outward(conn, tenant_id, month)
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")

    tenant = conn.execute("SELECT name, gstin FROM tenants WHERE id=?",
                          (tenant_id,)).fetchone()
    w.writerow(["GSTR-1 working paper — NOT a portal upload"])
    w.writerow(["Business", tenant[0] if tenant else "", "GSTIN",
                (tenant[1] if tenant else "") or "unregistered"])
    w.writerow(["Return period", month])
    w.writerow([])

    # ── 4A: B2B ──
    b2b: Dict[Tuple, Dict[str, Any]] = {}
    for r in lines:
        if not (r["gstin"] or "").strip():
            continue
        k = (r["gstin"].strip(), r["customer"], r["num"], r["dt"],
             r["place_of_supply"] or "", float(r["gst_rate"] or 0),
             int(r["is_interstate"] or 0))
        a = b2b.setdefault(k, {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0})
        for f in ("taxable", "cgst", "sgst", "igst"):
            a[f] += float(r[f if f != "taxable" else "taxable"] or 0)

    w.writerow(["Table", "GSTIN of recipient", "Receiver name", "Invoice number",
                "Invoice date", "Place of supply", "Reverse charge", "Invoice type",
                "Rate", "Taxable value", "CGST", "SGST", "IGST", "Cess"])
    for (gstin, name, num, dt, pos, rate, inter), a in sorted(b2b.items(), key=lambda x: x[0][2]):
        w.writerow(["4A-B2B", gstin, name, num, dt, pos, "N", "Regular",
                    f"{rate:g}", f"{a['taxable']:.2f}", f"{a['cgst']:.2f}",
                    f"{a['sgst']:.2f}", f"{a['igst']:.2f}", "0.00"])
    w.writerow([])

    # ── 7: B2C small, summarised by place of supply and rate ──
    b2cs: Dict[Tuple, Dict[str, float]] = {}
    for r in lines:
        if (r["gstin"] or "").strip():
            continue
        k = (r["place_of_supply"] or "", float(r["gst_rate"] or 0),
             int(r["is_interstate"] or 0))
        a = b2cs.setdefault(k, {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0})
        a["taxable"] += float(r["taxable"] or 0)
        a["cgst"] += float(r["cgst"] or 0)
        a["sgst"] += float(r["sgst"] or 0)
        a["igst"] += float(r["igst"] or 0)

    w.writerow(["Table", "Type", "Place of supply", "Rate", "Taxable value",
                "CGST", "SGST", "IGST", "Cess"])
    for (pos, rate, inter), a in sorted(b2cs.items()):
        w.writerow(["7-B2CS", "INTER" if inter else "INTRA", pos, f"{rate:g}",
                    f"{a['taxable']:.2f}", f"{a['cgst']:.2f}", f"{a['sgst']:.2f}",
                    f"{a['igst']:.2f}", "0.00"])
    w.writerow([])

    # ── 12: HSN summary ──
    hsn: Dict[Tuple, Dict[str, float]] = {}
    for r in lines:
        k = (r["hsn"] or "", float(r["gst_rate"] or 0))
        a = hsn.setdefault(k, {"qty": 0.0, "taxable": 0.0, "cgst": 0.0,
                               "sgst": 0.0, "igst": 0.0})
        a["qty"] += float(r["qty"] or 0)
        a["taxable"] += float(r["taxable"] or 0)
        a["cgst"] += float(r["cgst"] or 0)
        a["sgst"] += float(r["sgst"] or 0)
        a["igst"] += float(r["igst"] or 0)

    w.writerow(["Table", "HSN", "Description", "UQC", "Total quantity",
                "Rate", "Taxable value", "CGST", "SGST", "IGST", "Cess"])
    for (code, rate), a in sorted(hsn.items()):
        w.writerow(["12-HSN", code or "NOT SET", "", "PCS", f"{a['qty']:g}",
                    f"{rate:g}", f"{a['taxable']:.2f}", f"{a['cgst']:.2f}",
                    f"{a['sgst']:.2f}", f"{a['igst']:.2f}", "0.00"])

    w.writerow([])
    w.writerow(["Credit notes are included above as negative values, which is how"])
    w.writerow(["they net against outward supply. This file is a working paper for"])
    w.writerow(["your accountant, not a portal upload."])
    return out.getvalue()


# ─────────────────────────── GSTR-3B ───────────────────────────

def gstr3b_summary(conn: sqlite3.Connection, tenant_id: str, month: str) -> Dict[str, Any]:
    """Outward liability, input credit, and what is actually payable."""
    out_lines = _outward(conn, tenant_id, month)
    in_lines = _inward(conn, tenant_id, month)

    def agg(rows):
        return {
            "taxable": round(sum(float(r["taxable"] or 0) for r in rows), 2),
            "cgst": round(sum(float(r["cgst"] or 0) for r in rows), 2),
            "sgst": round(sum(float(r["sgst"] or 0) for r in rows), 2),
            "igst": round(sum(float(r["igst"] or 0) for r in rows), 2),
        }

    outward = agg(out_lines)
    inward = agg(in_lines)
    outward["tax"] = round(outward["cgst"] + outward["sgst"] + outward["igst"], 2)
    inward["tax"] = round(inward["cgst"] + inward["sgst"] + inward["igst"], 2)

    # Credit is claimed head by head — IGST credit cannot wipe out an SGST
    # liability without an ordering rule the portal applies itself. Netting the
    # single grand total would understate what is actually payable.
    payable = {h: round(max(0.0, outward[h] - inward[h]), 2)
               for h in ("cgst", "sgst", "igst")}
    carried = {h: round(max(0.0, inward[h] - outward[h]), 2)
               for h in ("cgst", "sgst", "igst")}

    return {
        "month": month,
        "outward": outward,
        "inward": inward,
        "net_payable": payable,
        "net_payable_total": round(sum(payable.values()), 2),
        "credit_carried_forward": carried,
        "credit_carried_total": round(sum(carried.values()), 2),
        "invoices": len({r["num"] for r in out_lines if r["doc"] == "invoice"}),
        "credit_notes": len({r["num"] for r in out_lines if r["doc"] == "credit_note"}),
        "supplier_bills": len({r["bill_number"] for r in in_lines}),
    }


def gstr3b_csv(conn: sqlite3.Connection, tenant_id: str, month: str) -> str:
    d = gstr3b_summary(conn, tenant_id, month)
    tenant = conn.execute("SELECT name, gstin FROM tenants WHERE id=?",
                          (tenant_id,)).fetchone()
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["GSTR-3B working paper — NOT a portal upload"])
    w.writerow(["Business", tenant[0] if tenant else "", "GSTIN",
                (tenant[1] if tenant else "") or "unregistered"])
    w.writerow(["Return period", d["month"]])
    w.writerow([])
    w.writerow(["Section", "Description", "Taxable value", "CGST", "SGST", "IGST"])
    o, i = d["outward"], d["inward"]
    w.writerow(["3.1(a)", "Outward taxable supplies (other than zero rated)",
                f"{o['taxable']:.2f}", f"{o['cgst']:.2f}", f"{o['sgst']:.2f}",
                f"{o['igst']:.2f}"])
    w.writerow(["4(A)(5)", "Input tax credit — all other ITC",
                f"{i['taxable']:.2f}", f"{i['cgst']:.2f}", f"{i['sgst']:.2f}",
                f"{i['igst']:.2f}"])
    p = d["net_payable"]
    w.writerow(["5.1", "Tax payable after credit", "",
                f"{p['cgst']:.2f}", f"{p['sgst']:.2f}", f"{p['igst']:.2f}"])
    c = d["credit_carried_forward"]
    w.writerow(["", "Credit carried forward", "",
                f"{c['cgst']:.2f}", f"{c['sgst']:.2f}", f"{c['igst']:.2f}"])
    w.writerow([])
    w.writerow(["Net payable this month", f"{d['net_payable_total']:.2f}"])
    w.writerow([])
    w.writerow(["Built from", f"{d['invoices']} invoices",
                f"{d['credit_notes']} credit notes",
                f"{d['supplier_bills']} supplier bills"])
    w.writerow(["Credit is offset head by head (CGST against CGST, and so on),"])
    w.writerow(["because a single netted total would understate what is payable."])
    return out.getvalue()
