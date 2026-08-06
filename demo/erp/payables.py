from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "erp-api"))
from app.domain.gst import LineInput, compute_invoice  # noqa: E402
from .billing import next_number

class PayablesError(Exception):
    """A refusal the user should see, not a crash."""

def _uid() -> str:
    return str(uuid.uuid4())

def post_supplier_bill(
    conn: sqlite3.Connection,
    tenant_id: str,
    supplier_id: str,
    location_id: str,
    lines: List[Dict[str, Any]],
    supplier_invoice_number: str = "",
    goods_receipt_id: Optional[str] = None,
    bill_date: Optional[str] = None,
    created_by: str = "backoffice",
    notes: str = "",
    auto_pay: bool = False,
    commit: bool = True,
) -> Dict[str, Any]:
    if not lines:
        raise PayablesError("a bill needs at least one line")
    
    sup = conn.execute(
        "SELECT id,name FROM suppliers WHERE id=? AND tenant_id=?",
        (supplier_id, tenant_id)).fetchone()
    if sup is None:
        raise PayablesError("unknown supplier")
    
    # Use the tenant's state code for both buyer and seller to assume intra-state GST
    # since suppliers table currently lacks state_code in the schema.
    seller_state = conn.execute("SELECT state_code FROM tenants WHERE id=?", (tenant_id,)).fetchone()[0]

    gst_lines = []
    for raw in lines:
        pid = raw.get("product_id")
        prod = conn.execute(
            "SELECT id,name,sku,unit_cost,hsn_code FROM products"
            " WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()
        if prod is None:
            raise PayablesError(f"product {pid} not found for this business")
        qty = Decimal(str(raw.get("quantity") or 0))
        if qty <= 0:
            raise PayablesError(f"{prod[2]}: quantity must be positive")
        price = Decimal(str(raw.get("unit_price") if raw.get("unit_price") is not None else prod[3]))
        
        gst_lines.append(LineInput(
            product_id=prod[0], quantity=qty, unit_price=price,
            discount_pct=Decimal("0"), hsn_code=prod[4], description=prod[1]))
            
    totals = compute_invoice(gst_lines, seller_state, seller_state)
    
    bill_id = _uid()
    number = next_number(conn, tenant_id, "supplier_bills", "bill_number", "BIL")
    idate = bill_date or date.today().isoformat()
    due = (date.fromisoformat(idate) + timedelta(days=30)).isoformat()
    paid = float(totals.grand_total) if auto_pay else 0.0
    status = "paid" if auto_pay else "posted"
    tax_tot = float(totals.cgst_total + totals.sgst_total + totals.igst_total)

    conn.execute(
        "INSERT INTO supplier_bills(id,tenant_id,bill_number,supplier_id,location_id,"
        "bill_date,due_date,status,goods_receipt_id,supplier_invoice_number,subtotal,"
        "tax_total,round_off,grand_total,amount_paid,notes,created_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bill_id, tenant_id, number, supplier_id, location_id, idate, due, status,
         goods_receipt_id, supplier_invoice_number, float(totals.subtotal),
         tax_tot, float(totals.round_off), float(totals.grand_total), paid, notes, created_by,
         datetime.now().isoformat(timespec="seconds")))

    for i, lr in enumerate(totals.lines, start=1):
        conn.execute(
            "INSERT INTO supplier_bill_lines(id,tenant_id,bill_id,product_id,line_no,"
            "quantity,unit_price,taxable_value,gst_rate,cgst,sgst,igst,line_total,hsn_code) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_uid(), tenant_id, bill_id, lr.product_id, i, float(lr.quantity),
             float(lr.unit_price), float(lr.taxable_value), float(lr.gst_rate),
             float(lr.cgst), float(lr.sgst), float(lr.igst), float(lr.line_total), lr.hsn_code))

    if auto_pay:
        pay_id = _uid()
        pnum = next_number(conn, tenant_id, "supplier_payments", "payment_number", "PAY")
        conn.execute(
            "INSERT INTO supplier_payments(id,tenant_id,payment_number,supplier_id,payment_date,"
            "amount,method,paid_by) VALUES(?,?,?,?,?,?,?,?)",
            (pay_id, tenant_id, pnum, supplier_id, idate, paid, "bank", created_by))
        conn.execute(
            "INSERT INTO supplier_payment_allocations(id,tenant_id,payment_id,bill_id,amount)"
            " VALUES(?,?,?,?,?)", (_uid(), tenant_id, pay_id, bill_id, paid))

    if commit:
        conn.commit()
    return {
        "id": bill_id, "bill_number": number, "bill_date": idate,
        "supplier_name": sup[1], "grand_total": float(totals.grand_total),
        "amount_paid": paid, "balance_due": round(float(totals.grand_total) - paid, 2)
    }

def record_supplier_payment(conn: sqlite3.Connection, tenant_id: str, supplier_id: str,
                            amount: float, method: str = "bank", reference: str = "",
                            paid_by: str = "backoffice") -> Dict[str, Any]:
    if amount <= 0:
        raise PayablesError("amount must be positive")
    sup = conn.execute("SELECT id,name FROM suppliers WHERE id=? AND tenant_id=?",
                       (supplier_id, tenant_id)).fetchone()
    if sup is None:
        raise PayablesError("unknown supplier")

    pay_id = _uid()
    number = next_number(conn, tenant_id, "supplier_payments", "payment_number", "PAY")
    conn.execute(
        "INSERT INTO supplier_payments(id,tenant_id,payment_number,supplier_id,payment_date,"
        "amount,method,reference,paid_by) VALUES(?,?,?,?,?,?,?,?,?)",
        (pay_id, tenant_id, number, supplier_id, date.today().isoformat(),
         amount, method, reference, paid_by))

    left = amount
    applied = []
    open_bills = conn.execute(
        "SELECT id,bill_number,grand_total,amount_paid FROM supplier_bills"
        " WHERE tenant_id=? AND supplier_id=? AND status IN ('posted','part_paid')"
        " AND grand_total - amount_paid > 0.01 ORDER BY bill_date, bill_number",
        (tenant_id, supplier_id)).fetchall()
        
    for inv in open_bills:
        if left <= 0.009:
            break
        due = round(inv[2] - inv[3], 2)
        take = min(left, due)
        conn.execute("INSERT INTO supplier_payment_allocations(id,tenant_id,payment_id,bill_id,"
                     "amount) VALUES(?,?,?,?,?)", (_uid(), tenant_id, pay_id, inv[0], take))
        new_paid = round(inv[3] + take, 2)
        conn.execute("UPDATE supplier_bills SET amount_paid=?, status=? WHERE id=?",
                     (new_paid, "paid" if new_paid >= inv[2] - 0.01 else "part_paid", inv[0]))
        applied.append({"bill_number": inv[1], "amount": round(take, 2)})
        left = round(left - take, 2)

    conn.commit()
    return {"payment_number": number, "amount": amount, "applied": applied,
            "on_account": round(left, 2), "supplier_name": sup[1]}

def payables(conn: sqlite3.Connection, tenant_id: str) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT s.id, s.name, COALESCE(SUM(b.grand_total - b.amount_paid), 0) as balance"
        " FROM suppliers s"
        " LEFT JOIN supplier_bills b ON b.supplier_id=s.id AND b.tenant_id=s.tenant_id AND b.status IN ('posted', 'part_paid')"
        " WHERE s.tenant_id=? GROUP BY s.id HAVING balance > 0.01 ORDER BY balance DESC",
        (tenant_id,)).fetchall()
    return {"items": [{"supplier_id": r[0], "supplier_name": r[1], "balance": round(r[2], 2)} for r in rows]}

def supplier_bills(conn: sqlite3.Connection, tenant_id: str, status: str = "") -> Dict[str, Any]:
    query = "SELECT b.id, b.bill_number, b.bill_date, b.grand_total, b.amount_paid, b.status, s.name supplier_name" \
            " FROM supplier_bills b JOIN suppliers s ON s.id=b.supplier_id WHERE b.tenant_id=?"
    params = [tenant_id]
    if status:
        query += " AND b.status=?"
        params.append(status)
    query += " ORDER BY b.bill_date DESC, b.bill_number DESC LIMIT 100"
    
    rows = conn.execute(query, params).fetchall()
    cols = ["id", "bill_number", "bill_date", "grand_total", "amount_paid", "status", "supplier_name"]
    return {"items": [dict(zip(cols, r)) for r in rows]}
