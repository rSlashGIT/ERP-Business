"""Credit notes — taking a garment back.

Apparel has the highest return rate in retail. Without this, a shop's only
option is to delete or edit the invoice, which destroys the audit trail, the
GST position and the stock history in one move.

THE FOUR THINGS THAT MUST BE RIGHT
----------------------------------
1. **GST reverses at the ORIGINAL rate.** A kurta sold at Rs 2,600 taxable
   carried 18%. If it comes back after the shop has marked it down to Rs 2,400,
   today's slab is 5% — reversing at 5% would leave the shop having remitted
   18% and refunded 5%, quietly out of pocket, and would misstate GSTR-1. So
   every credit line points at the invoice line it reverses and copies its
   rate.

2. **You cannot return more than was sold.** Across ALL credit notes against
   that invoice line, not just this one — otherwise two half-returns become a
   double refund.

3. **Restocking is a per-line decision.** A garment returned unworn goes back
   on the shelf. One returned stained is a write-off: the customer is still
   credited, but stock does not rise. Treating every return as restockable is
   how phantom inventory appears.

4. **The money has to go somewhere.** Either it reduces what the customer owes
   (`credit`) or it leaves the till (`refund`). Both are recorded; neither is
   assumed.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "services" / "erp-api"))

from app.domain.gst import q as gq  # noqa: E402

from .billing import next_number  # noqa: E402


class ReturnError(Exception):
    """Refused before anything was written."""


def _uid() -> str:
    return str(uuid.uuid4())


def returnable(conn: sqlite3.Connection, tenant_id: str,
               invoice_id: str) -> Optional[Dict[str, Any]]:
    """The invoice, with how much of each line is still returnable."""
    inv = conn.execute(
        "SELECT i.id, i.invoice_number, i.invoice_date, i.status, i.location_id,"
        " i.is_interstate, i.grand_total, i.amount_paid, c.name, c.id"
        " FROM sales_invoices i JOIN customers c ON c.id=i.customer_id"
        "   AND c.tenant_id=i.tenant_id"
        " WHERE i.tenant_id=? AND i.id=?", (tenant_id, invoice_id)).fetchone()
    if inv is None:
        return None
    keys = ["id", "invoice_number", "invoice_date", "status", "location_id",
            "is_interstate", "grand_total", "amount_paid", "customer_name", "customer_id"]
    d = dict(zip(keys, inv))
    if d["status"] == "cancelled":
        return None

    rows = conn.execute(
        "SELECT l.id, l.product_id, l.line_no, l.quantity, l.unit_price, l.discount_pct,"
        " l.gst_rate, l.taxable_value, l.line_total, p.sku, p.name, p.size, p.colour,"
        " COALESCE((SELECT SUM(cl.quantity) FROM credit_note_lines cl"
        "           WHERE cl.invoice_line_id=l.id AND cl.tenant_id=l.tenant_id),0) returned"
        " FROM sales_invoice_lines l"
        " JOIN products p ON p.id=l.product_id AND p.tenant_id=l.tenant_id"
        " WHERE l.tenant_id=? AND l.invoice_id=? ORDER BY l.line_no",
        (tenant_id, invoice_id)).fetchall()
    lk = ["id", "product_id", "line_no", "quantity", "unit_price", "discount_pct",
          "gst_rate", "taxable_value", "line_total", "sku", "name", "size", "colour",
          "returned"]
    d["lines"] = []
    for r in rows:
        ln = dict(zip(lk, r))
        ln["returnable"] = round(ln["quantity"] - (ln["returned"] or 0), 2)
        d["lines"].append(ln)
    d["any_returnable"] = any(l["returnable"] > 0.001 for l in d["lines"])
    return d


def create_credit_note(conn: sqlite3.Connection, tenant_id: str, invoice_id: str,
                       lines: List[Dict[str, Any]], *, reason: str = "",
                       refund_mode: str = "credit", created_by: str = "counter",
                       note_date: Optional[str] = None) -> Dict[str, Any]:
    """Reverse part or all of a sale.

    `lines` are {invoice_line_id, quantity, restock?, condition?}.
    """
    if not lines:
        raise ReturnError("a credit note needs at least one line")
    if refund_mode not in ("credit", "refund"):
        raise ReturnError("refund mode must be 'credit' or 'refund'")

    inv = returnable(conn, tenant_id, invoice_id)
    if inv is None:
        raise ReturnError("unknown invoice for this business")

    by_id = {l["id"]: l for l in inv["lines"]}
    resolved = []
    for raw in lines:
        lid = raw.get("invoice_line_id")
        src = by_id.get(lid)
        if src is None:
            raise ReturnError("that line is not on this invoice")
        qty = float(raw.get("quantity") or 0)
        if qty <= 0:
            continue
        if qty > src["returnable"] + 0.001:
            raise ReturnError(
                f"{src['sku']}: only {src['returnable']:g} of {src['quantity']:g} "
                f"left to return")
        resolved.append({"src": src, "qty": qty,
                         "restock": bool(raw.get("restock", True)),
                         "condition": raw.get("condition") or "resaleable"})
    if not resolved:
        raise ReturnError("nothing to return — every line was zero")

    interstate = bool(inv["is_interstate"])
    cn_id = _uid()
    number = next_number(conn, tenant_id, "credit_notes", "cn_number", "CN")
    ndate = note_date or date.today().isoformat()

    taxable = cgst = sgst = igst = Decimal("0")
    computed = []
    for r in resolved:
        s = r["src"]
        # Per-unit taxable straight off the original line, so a mid-life price
        # change cannot alter what gets refunded.
        per_unit = Decimal(str(s["taxable_value"])) / Decimal(str(s["quantity"]))
        line_taxable = gq(per_unit * Decimal(str(r["qty"])))
        rate = Decimal(str(s["gst_rate"]))          # the ORIGINAL rate, never today's
        tax = gq(line_taxable * rate / 100)
        if interstate:
            l_cgst = l_sgst = Decimal("0")
            l_igst = tax
        else:
            l_cgst = gq(tax / 2)
            l_sgst = gq(tax - l_cgst)
            l_igst = Decimal("0")
        taxable += line_taxable
        cgst += l_cgst
        sgst += l_sgst
        igst += l_igst
        computed.append((r, line_taxable, rate, l_cgst, l_sgst, l_igst,
                         gq(line_taxable + l_cgst + l_sgst + l_igst)))

    gross = taxable + cgst + sgst + igst
    rounded = gq(gross.to_integral_value(rounding="ROUND_HALF_UP"))
    round_off = gq(rounded - gross)

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO credit_notes(id,tenant_id,cn_number,invoice_id,customer_id,"
            "location_id,note_date,reason,taxable_total,cgst_total,sgst_total,igst_total,"
            "round_off,grand_total,refund_mode,created_by,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (cn_id, tenant_id, number, invoice_id, inv["customer_id"], inv["location_id"],
             ndate, reason or None, float(taxable), float(cgst), float(sgst), float(igst),
             float(round_off), float(rounded), refund_mode, created_by))

        for i, (r, l_taxable, rate, l_cgst, l_sgst, l_igst, l_total) in enumerate(
                computed, start=1):
            s = r["src"]
            conn.execute(
                "INSERT INTO credit_note_lines(id,tenant_id,credit_note_id,invoice_line_id,"
                "product_id,line_no,quantity,unit_price,discount_pct,taxable_value,gst_rate,"
                "cgst,sgst,igst,line_total,restock,condition)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), tenant_id, cn_id, s["id"], s["product_id"], i, r["qty"],
                 s["unit_price"], s["discount_pct"], float(l_taxable), float(rate),
                 float(l_cgst), float(l_sgst), float(l_igst), float(l_total),
                 1 if r["restock"] else 0, r["condition"]))

            if r["restock"]:
                lvl = conn.execute(
                    "SELECT id FROM inventory_levels WHERE tenant_id=? AND product_id=?"
                    " AND location_id=?",
                    (tenant_id, s["product_id"], inv["location_id"])).fetchone()
                if lvl:
                    conn.execute(
                        "UPDATE inventory_levels SET on_hand=on_hand+? WHERE id=? AND tenant_id=?",
                        (r["qty"], lvl[0], tenant_id))
                else:
                    conn.execute(
                        "INSERT INTO inventory_levels(id,tenant_id,product_id,location_id,"
                        "on_hand,on_order,reserved,backorder) VALUES(?,?,?,?,?,0,0,0)",
                        (_uid(), tenant_id, s["product_id"], inv["location_id"], r["qty"]))
                conn.execute(
                    "INSERT INTO stock_movements(tenant_id,product_id,location_id,"
                    "movement_type,quantity,occurred_at,reference_type,reference_id,"
                    "idempotency_key) VALUES(?,?,?,'return_in',?,?, 'credit_note',?,?)",
                    (tenant_id, s["product_id"], inv["location_id"], r["qty"], ndate,
                     cn_id, f"cn:{cn_id}:{i}"))
            else:
                # Written off: the customer is still credited, stock is not.
                conn.execute(
                    "INSERT INTO stock_movements(tenant_id,product_id,location_id,"
                    "movement_type,quantity,occurred_at,reference_type,reference_id,"
                    "idempotency_key) VALUES(?,?,?,'write_off',0,?, 'credit_note',?,?)",
                    (tenant_id, s["product_id"], inv["location_id"], ndate,
                     cn_id, f"cn-wo:{cn_id}:{i}"))

        # Money. Reducing the bill is only possible up to what is still unpaid;
        # anything beyond that has already been collected and must be refunded.
        row = conn.execute(
            "SELECT grand_total, amount_paid, status FROM sales_invoices"
            " WHERE id=? AND tenant_id=?", (invoice_id, tenant_id)).fetchone()
        outstanding = round(row[0] - row[1], 2)
        applied = 0.0
        if refund_mode == "credit":
            applied = min(float(rounded), max(0.0, outstanding))
            if applied > 0:
                new_paid = round(row[1] + applied, 2)
                new_status = "paid" if new_paid >= row[0] - 0.01 else "part_paid"
                conn.execute(
                    "UPDATE sales_invoices SET amount_paid=?, status=? WHERE id=? AND tenant_id=?",
                    (new_paid, new_status, invoice_id, tenant_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"id": cn_id, "cn_number": number, "note_date": ndate,
            "invoice_number": inv["invoice_number"], "customer_name": inv["customer_name"],
            "taxable_total": float(taxable), "cgst_total": float(cgst),
            "sgst_total": float(sgst), "igst_total": float(igst),
            "round_off": float(round_off), "grand_total": float(rounded),
            "refund_mode": refund_mode,
            "applied_to_invoice": round(applied, 2),
            "to_refund": round(float(rounded) - applied, 2),
            "restocked": round(sum(r["qty"] for r in resolved if r["restock"]), 2),
            "written_off": round(sum(r["qty"] for r in resolved if not r["restock"]), 2),
            "lines": len(resolved)}


def credit_notes(conn: sqlite3.Connection, tenant_id: str, limit: int = 100):
    rows = conn.execute(
        "SELECT n.id, n.cn_number, n.note_date, n.grand_total, n.refund_mode, n.reason,"
        " i.invoice_number, c.name customer_name,"
        " COALESCE(SUM(nl.quantity),0) units,"
        " COALESCE(SUM(CASE WHEN nl.restock=1 THEN nl.quantity ELSE 0 END),0) restocked"
        " FROM credit_notes n"
        " JOIN sales_invoices i ON i.id=n.invoice_id AND i.tenant_id=n.tenant_id"
        " JOIN customers c ON c.id=n.customer_id AND c.tenant_id=n.tenant_id"
        " LEFT JOIN credit_note_lines nl ON nl.credit_note_id=n.id AND nl.tenant_id=n.tenant_id"
        " WHERE n.tenant_id=? GROUP BY n.id"
        " ORDER BY n.note_date DESC, n.cn_number DESC LIMIT ?",
        (tenant_id, limit)).fetchall()
    cols = ["id", "cn_number", "note_date", "grand_total", "refund_mode", "reason",
            "invoice_number", "customer_name", "units", "restocked"]
    items = [dict(zip(cols, r)) for r in rows]
    return {"items": items,
            "value": round(sum(i["grand_total"] for i in items), 2),
            "units": round(sum(i["units"] for i in items), 2)}
