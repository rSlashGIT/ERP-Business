"""Invoice posting: the closed loop that convinces a shopkeeper.

    bill a garment -> stock drops -> receivable appears -> reorder suggestion fires

GST is computed by services/erp-api/app/domain/gst.py — the SAME module the
FastAPI routes use. The tax rules are never reimplemented here; only the
persistence differs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "erp-api"))
from app.domain.gst import LineInput, amount_in_words, compute_invoice, q  # noqa: E402


class BillingError(Exception):
    """A refusal the user should see, not a crash."""


def _uid() -> str:
    return str(uuid.uuid4())


def next_number(conn: sqlite3.Connection, tenant_id: str, table: str,
                column: str, prefix: str) -> str:
    """Per-tenant sequence. Tenant A's numbering is unaffected by tenant B's
    volume — the constraint is UNIQUE(tenant_id, number), so sequences run
    independently."""
    fy = date.today().year if date.today().month >= 4 else date.today().year - 1
    stem = f"{prefix}/{fy}-{str(fy + 1)[2:]}/"
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE tenant_id=? AND {column} LIKE ?"
        f" ORDER BY {column} DESC LIMIT 1", (tenant_id, stem + "%")).fetchone()
    n = int(str(row[0]).split("/")[-1]) + 1 if row else 1
    return f"{stem}{n:04d}"


def post_invoice(
    conn: sqlite3.Connection,
    tenant_id: str,
    customer_id: str,
    location_id: str,
    lines: List[Dict[str, Any]],
    invoice_date: Optional[str] = None,
    created_by: str = "counter",
    notes: str = "",
    auto_pay: bool = False,
    allow_negative_stock: bool = False,
    commit: bool = True,
) -> Dict[str, Any]:
    """Compute GST, write the invoice, move stock, create the receivable.

    Everything is tenant-scoped: the customer, the location and every product
    must belong to `tenant_id` or the call is refused. A billing screen that
    could sell another tenant's garment is the worst possible leak.
    """
    if not lines:
        raise BillingError("an invoice needs at least one line")

    cust = conn.execute(
        "SELECT id,name,state_code,gstin,credit_days,is_walkin FROM customers"
        " WHERE id=? AND tenant_id=?", (customer_id, tenant_id)).fetchone()
    if cust is None:
        raise BillingError("unknown customer")
    loc = conn.execute("SELECT id,code FROM locations WHERE id=? AND tenant_id=?",
                       (location_id, tenant_id)).fetchone()
    if loc is None:
        raise BillingError("unknown location")
    seller_state = conn.execute("SELECT state_code FROM tenants WHERE id=?",
                                (tenant_id,)).fetchone()[0]

    gst_lines: List[LineInput] = []
    resolved = []
    for raw in lines:
        pid = raw.get("product_id")
        prod = conn.execute(
            "SELECT id,name,sku,unit_price,hsn_code,size,colour FROM products"
            " WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()
        if prod is None:
            raise BillingError(f"product {pid} not found for this business")
        qty = Decimal(str(raw.get("quantity") or 0))
        if qty <= 0:
            raise BillingError(f"{prod[2]}: quantity must be positive")
        price = Decimal(str(raw.get("unit_price") if raw.get("unit_price") is not None
                            else prod[3]))
        gst_lines.append(LineInput(
            product_id=prod[0], quantity=qty, unit_price=price,
            discount_pct=Decimal(str(raw.get("discount_pct") or 0)),
            hsn_code=prod[4], description=prod[1]))
        resolved.append(prod)

    if not allow_negative_stock:
        for gl in gst_lines:
            row = conn.execute(
                "SELECT on_hand FROM inventory_levels WHERE tenant_id=? AND product_id=?"
                " AND location_id=?", (tenant_id, gl.product_id, location_id)).fetchone()
            have = row[0] if row else 0
            if have < float(gl.quantity):
                name = next(p[1] for p in resolved if p[0] == gl.product_id)
                raise BillingError(f"only {have:g} in stock at this location for {name}")

    totals = compute_invoice(gst_lines, seller_state, cust[2])

    inv_id = _uid()
    number = next_number(conn, tenant_id, "sales_invoices", "invoice_number", "INV")
    idate = invoice_date or date.today().isoformat()
    credit_days = cust[4] or 0
    due = (date.fromisoformat(idate) + timedelta(days=credit_days)).isoformat()
    paid = float(totals.grand_total) if auto_pay else 0.0
    status = "paid" if auto_pay else "posted"

    conn.execute(
        "INSERT INTO sales_invoices(id,tenant_id,invoice_number,customer_id,location_id,"
        "invoice_date,due_date,status,place_of_supply,is_interstate,subtotal,discount_total,"
        "taxable_total,cgst_total,sgst_total,igst_total,round_off,grand_total,amount_paid,"
        "notes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (inv_id, tenant_id, number, customer_id, location_id, idate, due, status,
         cust[2], int(totals.is_interstate), float(totals.subtotal),
         float(totals.discount_total), float(totals.taxable_total),
         float(totals.cgst_total), float(totals.sgst_total), float(totals.igst_total),
         float(totals.round_off), float(totals.grand_total), paid, notes, created_by,
         datetime.now().isoformat(timespec="seconds")))

    for i, lr in enumerate(totals.lines, start=1):
        conn.execute(
            "INSERT INTO sales_invoice_lines(id,tenant_id,invoice_id,product_id,line_no,"
            "quantity,unit_price,discount_pct,taxable_value,gst_rate,cgst,sgst,igst,"
            "line_total,hsn_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_uid(), tenant_id, inv_id, lr.product_id, i, float(lr.quantity),
             float(lr.unit_price), float(lr.discount_pct), float(lr.taxable_value),
             float(lr.gst_rate), float(lr.cgst), float(lr.sgst), float(lr.igst),
             float(lr.line_total), lr.hsn_code))

        # Ledger first, then the cached balance — the movement row is the truth.
        conn.execute(
            "INSERT INTO stock_movements(tenant_id,product_id,location_id,movement_type,"
            "quantity,occurred_at,reference_type,reference_id,idempotency_key)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (tenant_id, lr.product_id, location_id, "sale", -float(lr.quantity),
             idate, "sales_invoice", number, f"{tenant_id}:{number}:{i}"))
        conn.execute(
            "UPDATE inventory_levels SET on_hand = on_hand - ? WHERE tenant_id=?"
            " AND product_id=? AND location_id=?",
            (float(lr.quantity), tenant_id, lr.product_id, location_id))

    if auto_pay:
        pay_id = _uid()
        pnum = next_number(conn, tenant_id, "payments", "payment_number", "RCP")
        conn.execute(
            "INSERT INTO payments(id,tenant_id,payment_number,customer_id,payment_date,"
            "amount,method,received_by) VALUES(?,?,?,?,?,?,?,?)",
            (pay_id, tenant_id, pnum, customer_id, idate, paid, "cash", created_by))
        conn.execute(
            "INSERT INTO payment_allocations(id,tenant_id,payment_id,invoice_id,amount)"
            " VALUES(?,?,?,?,?)", (_uid(), tenant_id, pay_id, inv_id, paid))

    # Seeding posts thousands of invoices; committing each one fsyncs, which
    # turns a ten-second seed into a forty-second one on a network share.
    if commit:
        conn.commit()
    return {
        "id": inv_id, "invoice_number": number, "invoice_date": idate, "due_date": due,
        "status": status, "customer_name": cust[1], "customer_gstin": cust[3],
        "is_interstate": totals.is_interstate, "place_of_supply": cust[2],
        "amount_paid": paid, "balance_due": round(float(totals.grand_total) - paid, 2),
        "amount_in_words": amount_in_words(totals.grand_total),
        **totals.as_dict(),
    }


def record_payment(conn: sqlite3.Connection, tenant_id: str, customer_id: str,
                   amount: float, method: str = "cash", reference: str = "",
                   received_by: str = "counter") -> Dict[str, Any]:
    """Receive money and settle the oldest invoices first.

    Oldest-first is the convention Indian retailers expect and it is what makes
    the ageing report meaningful; allocating newest-first leaves permanently
    stale balances at the top of the ledger.
    """
    if amount <= 0:
        raise BillingError("amount must be positive")
    cust = conn.execute("SELECT id,name FROM customers WHERE id=? AND tenant_id=?",
                        (customer_id, tenant_id)).fetchone()
    if cust is None:
        raise BillingError("unknown customer")

    pay_id = _uid()
    number = next_number(conn, tenant_id, "payments", "payment_number", "RCP")
    conn.execute(
        "INSERT INTO payments(id,tenant_id,payment_number,customer_id,payment_date,"
        "amount,method,reference,received_by) VALUES(?,?,?,?,?,?,?,?,?)",
        (pay_id, tenant_id, number, customer_id, date.today().isoformat(),
         amount, method, reference, received_by))

    left = amount
    applied = []
    open_invs = conn.execute(
        "SELECT id,invoice_number,grand_total,amount_paid FROM sales_invoices"
        " WHERE tenant_id=? AND customer_id=? AND status IN ('posted','part_paid')"
        " AND grand_total - amount_paid > 0.01 ORDER BY invoice_date, invoice_number",
        (tenant_id, customer_id)).fetchall()
    for inv in open_invs:
        if left <= 0.009:
            break
        due = round(inv[2] - inv[3], 2)
        take = min(left, due)
        conn.execute("INSERT INTO payment_allocations(id,tenant_id,payment_id,invoice_id,"
                     "amount) VALUES(?,?,?,?,?)", (_uid(), tenant_id, pay_id, inv[0], take))
        new_paid = round(inv[3] + take, 2)
        conn.execute("UPDATE sales_invoices SET amount_paid=?, status=? WHERE id=?",
                     (new_paid, "paid" if new_paid >= inv[2] - 0.01 else "part_paid", inv[0]))
        applied.append({"invoice_number": inv[1], "amount": round(take, 2)})
        left = round(left - take, 2)

    conn.commit()
    return {"payment_number": number, "amount": amount, "applied": applied,
            "on_account": round(left, 2), "customer_name": cust[1]}
