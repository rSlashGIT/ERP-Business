"""Goods receiving — the moment ordered stock becomes real stock.

Until this exists an ERP can only ever count stock DOWN. The shop sells, the
numbers fall, and the only way back up is to edit the inventory by hand, which
is exactly the habit the product is supposed to replace.

WHAT A RECEIPT HAS TO GET RIGHT
-------------------------------
1. **Partial deliveries are normal.** A supplier sends 40 of 60 and the rest
   next week. So a PO is not "received" or "not received" — each line tracks
   how much has landed, and the PO's status follows from the lines.

2. **Rejected stock is not received stock.** Torn seams, wrong colour, short
   shipment. Accepted goes on the shelf; rejected is recorded against the
   supplier and touches neither stock nor cost.

3. **THE COST BASIS MOVES.** This is the part shops get wrong and it silently
   corrupts every margin report downstream. If 10 kurtas were bought at Rs 600
   and 30 more arrive at Rs 700, the cost is not Rs 700 — it is the weighted
   average, Rs 675. Overwriting `unit_cost` with the latest invoice makes the
   old stock look more expensive than it was and understates profit.

4. **Receiving twice must not double the stock.** Every receipt writes a stock
   movement with an idempotency key, and the ledger's UNIQUE constraint is what
   actually enforces it — not a flag we remember to check.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from .billing import next_number


class ReceivingError(Exception):
    """Refused before anything was written."""


def _uid() -> str:
    return str(uuid.uuid4())


def open_purchase_orders(conn: sqlite3.Connection, tenant_id: str) -> Dict[str, Any]:
    """POs with anything still outstanding, newest first."""
    rows = conn.execute(
        "SELECT po.id, po.po_number, po.status, po.created_at, po.total_value,"
        " s.name supplier_name, s.id supplier_id, l.code location_code, l.id location_id,"
        " COUNT(pl.id) lines,"
        " COALESCE(SUM(pl.ordered_qty),0) ordered,"
        " COALESCE(SUM(pl.received_qty),0) received"
        " FROM purchase_orders po"
        " LEFT JOIN suppliers s ON s.id=po.supplier_id AND s.tenant_id=po.tenant_id"
        " LEFT JOIN locations l ON l.id=po.location_id AND l.tenant_id=po.tenant_id"
        " LEFT JOIN purchase_order_lines pl ON pl.purchase_order_id=po.id"
        "   AND pl.tenant_id=po.tenant_id"
        " WHERE po.tenant_id=? AND po.status!='cancelled'"
        " GROUP BY po.id ORDER BY po.created_at DESC, po.po_number DESC",
        (tenant_id,)).fetchall()
    cols = ["id", "po_number", "status", "created_at", "total_value", "supplier_name",
            "supplier_id", "location_code", "location_id", "lines", "ordered", "received"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["outstanding"] = round((d["ordered"] or 0) - (d["received"] or 0), 2)
        d["fully_received"] = d["outstanding"] <= 0.001
        out.append(d)
    return {"items": out,
            "awaiting": sum(1 for d in out if not d["fully_received"])}


def purchase_order_detail(conn: sqlite3.Connection, tenant_id: str,
                          po_id: str) -> Optional[Dict[str, Any]]:
    po = conn.execute(
        "SELECT po.id, po.po_number, po.status, po.created_at, po.location_id,"
        " s.name supplier_name, s.id supplier_id, l.code location_code, l.name location_name"
        " FROM purchase_orders po"
        " LEFT JOIN suppliers s ON s.id=po.supplier_id AND s.tenant_id=po.tenant_id"
        " LEFT JOIN locations l ON l.id=po.location_id AND l.tenant_id=po.tenant_id"
        " WHERE po.tenant_id=? AND po.id=?", (tenant_id, po_id)).fetchone()
    if po is None:
        return None
    keys = ["id", "po_number", "status", "created_at", "location_id", "supplier_name",
            "supplier_id", "location_code", "location_name"]
    d = dict(zip(keys, po))
    lines = conn.execute(
        "SELECT pl.id, pl.product_id, pl.line_no, pl.ordered_qty,"
        " COALESCE(pl.received_qty,0) received_qty, pl.unit_cost,"
        " p.sku, p.name, p.size, p.colour, p.unit_cost current_cost,"
        " COALESCE(i.on_hand,0) on_hand"
        " FROM purchase_order_lines pl"
        " JOIN products p ON p.id=pl.product_id AND p.tenant_id=pl.tenant_id"
        " LEFT JOIN inventory_levels i ON i.product_id=p.id AND i.tenant_id=pl.tenant_id"
        "   AND i.location_id=?"
        " WHERE pl.tenant_id=? AND pl.purchase_order_id=? ORDER BY pl.line_no",
        (d["location_id"], tenant_id, po_id)).fetchall()
    lk = ["id", "product_id", "line_no", "ordered_qty", "received_qty", "unit_cost",
          "sku", "name", "size", "colour", "current_cost", "on_hand"]
    d["lines"] = []
    for r in lines:
        ln = dict(zip(lk, r))
        ln["outstanding"] = round((ln["ordered_qty"] or 0) - (ln["received_qty"] or 0), 2)
        d["lines"].append(ln)
    d["outstanding"] = round(sum(l["outstanding"] for l in d["lines"]), 2)
    return d


def weighted_average_cost(on_hand: float, old_cost: float,
                          qty_in: float, new_cost: float) -> float:
    """The new cost basis after receiving `qty_in` at `new_cost`.

        (10 @ 600) + (30 @ 700)  ->  675, not 700

    Overwriting with the latest invoice price is the common shortcut and it
    quietly falsifies every margin figure the shop looks at afterwards.
    Negative or zero stock on hand means there is nothing to average against,
    so the incoming price simply becomes the cost.
    """
    on_hand = max(0.0, float(on_hand or 0))
    qty_in = max(0.0, float(qty_in or 0))
    old_cost = float(old_cost or 0)
    new_cost = float(new_cost or 0)
    if qty_in <= 0:
        return round(old_cost, 2)
    if on_hand <= 0 or old_cost <= 0:
        return round(new_cost, 2)
    total = on_hand + qty_in
    return round((on_hand * old_cost + qty_in * new_cost) / total, 2)


def receive(conn: sqlite3.Connection, tenant_id: str, po_id: Optional[str],
            lines: List[Dict[str, Any]], *, location_id: Optional[str] = None,
            supplier_invoice: str = "", received_by: str = "store",
            notes: str = "", received_date: Optional[str] = None) -> Dict[str, Any]:
    """Book a delivery. Accepted stock goes up, cost basis re-averages.

    `lines` are {po_line_id?, product_id, accepted_qty, rejected_qty?,
    unit_cost?, reject_reason?}. Everything is refused unless it belongs to
    this tenant.
    """
    if not lines:
        raise ReceivingError("a receipt needs at least one line")

    po = None
    if po_id:
        po = conn.execute(
            "SELECT id, location_id, supplier_id FROM purchase_orders"
            " WHERE id=? AND tenant_id=?", (po_id, tenant_id)).fetchone()
        if po is None:
            raise ReceivingError("unknown purchase order for this business")
        location_id = location_id or po[1]

    if not location_id:
        row = conn.execute(
            "SELECT id FROM locations WHERE tenant_id=? ORDER BY code LIMIT 1",
            (tenant_id,)).fetchone()
        if row is None:
            raise ReceivingError("this business has no stock location")
        location_id = row[0]
    else:
        if conn.execute("SELECT 1 FROM locations WHERE id=? AND tenant_id=?",
                        (location_id, tenant_id)).fetchone() is None:
            raise ReceivingError("unknown location for this business")

    resolved = []
    for raw in lines:
        pid = raw.get("product_id")
        prod = conn.execute(
            "SELECT id, name, sku, unit_cost FROM products"
            " WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()
        if prod is None:
            raise ReceivingError(f"product {pid} not found for this business")
        acc = float(raw.get("accepted_qty") or 0)
        rej = float(raw.get("rejected_qty") or 0)
        if acc < 0 or rej < 0:
            raise ReceivingError(f"{prod[2]}: quantities cannot be negative")
        if acc == 0 and rej == 0:
            continue
        po_line = None
        if raw.get("po_line_id"):
            po_line = conn.execute(
                "SELECT id, ordered_qty, COALESCE(received_qty,0), unit_cost"
                " FROM purchase_order_lines WHERE id=? AND tenant_id=?",
                (raw["po_line_id"], tenant_id)).fetchone()
            if po_line is None:
                raise ReceivingError("that order line is not part of this business")
        cost = raw.get("unit_cost")
        if cost is None:
            cost = po_line[3] if po_line else prod[3]
        cost = float(cost or 0)
        if cost < 0:
            raise ReceivingError(f"{prod[2]}: cost cannot be negative")
        resolved.append({"prod": prod, "accepted": acc, "rejected": rej,
                         "cost": cost, "po_line": po_line,
                         "reason": raw.get("reject_reason") or None})

    if not resolved:
        raise ReceivingError("nothing to receive — every line was zero")

    grn_id = _uid()
    number = next_number(conn, tenant_id, "goods_receipts", "grn_number", "GRN")
    rdate = received_date or date.today().isoformat()
    total = 0.0

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO goods_receipts(id,tenant_id,grn_number,purchase_order_id,"
            "supplier_id,location_id,received_date,supplier_invoice,notes,total_value,"
            "received_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (grn_id, tenant_id, number, po_id, po[2] if po else None, location_id,
             rdate, supplier_invoice or None, notes or None, 0.0, received_by))

        for i, r in enumerate(resolved, start=1):
            pid = r["prod"][0]
            value = round(r["accepted"] * r["cost"], 2)
            total += value
            conn.execute(
                "INSERT INTO goods_receipt_lines(id,tenant_id,goods_receipt_id,"
                "purchase_order_line_id,product_id,line_no,accepted_qty,rejected_qty,"
                "unit_cost,line_value,reject_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), tenant_id, grn_id,
                 r["po_line"][0] if r["po_line"] else None, pid, i,
                 r["accepted"], r["rejected"], r["cost"], value, r["reason"]))

            if r["accepted"] <= 0:
                continue

            inv = conn.execute(
                "SELECT id, on_hand, on_order FROM inventory_levels"
                " WHERE tenant_id=? AND product_id=? AND location_id=?",
                (tenant_id, pid, location_id)).fetchone()
            on_hand = inv[1] if inv else 0.0

            # Cost basis BEFORE the stock moves — averaging against the new
            # quantity would weight the incoming price twice.
            new_cost = weighted_average_cost(on_hand, r["prod"][3],
                                             r["accepted"], r["cost"])
            conn.execute("UPDATE products SET unit_cost=? WHERE id=? AND tenant_id=?",
                         (new_cost, pid, tenant_id))

            if inv:
                conn.execute(
                    "UPDATE inventory_levels SET on_hand=on_hand+?,"
                    " on_order=MAX(0, on_order-?) WHERE id=? AND tenant_id=?",
                    (r["accepted"], r["accepted"], inv[0], tenant_id))
            else:
                conn.execute(
                    "INSERT INTO inventory_levels(id,tenant_id,product_id,location_id,"
                    "on_hand,on_order,reserved,backorder) VALUES(?,?,?,?,?,0,0,0)",
                    (_uid(), tenant_id, pid, location_id, r["accepted"]))

            # The ledger's UNIQUE(tenant_id, idempotency_key) is what actually
            # stops a double-click booking the same delivery twice.
            conn.execute(
                "INSERT INTO stock_movements(tenant_id,product_id,location_id,"
                "movement_type,quantity,occurred_at,reference_type,reference_id,"
                "idempotency_key) VALUES(?,?,?,'receipt',?,?, 'goods_receipt',?,?)",
                (tenant_id, pid, location_id, r["accepted"], rdate, grn_id,
                 f"grn:{grn_id}:{i}"))

            if r["po_line"]:
                conn.execute(
                    "UPDATE purchase_order_lines SET received_qty=COALESCE(received_qty,0)+?"
                    " WHERE id=? AND tenant_id=?",
                    (r["accepted"], r["po_line"][0], tenant_id))

        conn.execute("UPDATE goods_receipts SET total_value=? WHERE id=? AND tenant_id=?",
                     (round(total, 2), grn_id, tenant_id))

        status = None
        if po_id:
            agg = conn.execute(
                "SELECT COALESCE(SUM(ordered_qty),0), COALESCE(SUM(received_qty),0)"
                " FROM purchase_order_lines WHERE tenant_id=? AND purchase_order_id=?",
                (tenant_id, po_id)).fetchone()
            status = "received" if agg[1] >= agg[0] - 0.001 else "part_received"
            conn.execute("UPDATE purchase_orders SET status=? WHERE id=? AND tenant_id=?",
                         (status, po_id, tenant_id))

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"id": grn_id, "grn_number": number, "received_date": rdate,
            "lines": len(resolved),
            "accepted": round(sum(r["accepted"] for r in resolved), 2),
            "rejected": round(sum(r["rejected"] for r in resolved), 2),
            "total_value": round(total, 2), "po_status": status,
            "location_id": location_id}


def receipts(conn: sqlite3.Connection, tenant_id: str, limit: int = 100) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT g.id, g.grn_number, g.received_date, g.supplier_invoice, g.total_value,"
        " po.po_number, s.name supplier_name, l.code location_code,"
        " COALESCE(SUM(gl.accepted_qty),0) accepted, COALESCE(SUM(gl.rejected_qty),0) rejected"
        " FROM goods_receipts g"
        " LEFT JOIN purchase_orders po ON po.id=g.purchase_order_id AND po.tenant_id=g.tenant_id"
        " LEFT JOIN suppliers s ON s.id=g.supplier_id AND s.tenant_id=g.tenant_id"
        " LEFT JOIN locations l ON l.id=g.location_id AND l.tenant_id=g.tenant_id"
        " LEFT JOIN goods_receipt_lines gl ON gl.goods_receipt_id=g.id AND gl.tenant_id=g.tenant_id"
        " WHERE g.tenant_id=? GROUP BY g.id"
        " ORDER BY g.received_date DESC, g.grn_number DESC LIMIT ?",
        (tenant_id, limit)).fetchall()
    cols = ["id", "grn_number", "received_date", "supplier_invoice", "total_value",
            "po_number", "supplier_name", "location_code", "accepted", "rejected"]
    return {"items": [dict(zip(cols, r)) for r in rows]}
