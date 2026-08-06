"""Inventory operations — stocktakes and transfers."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime
from typing import Any, Dict, List

from .billing import next_number


class InventoryError(Exception):
    """Refused before anything was written."""


def _uid() -> str:
    return str(uuid.uuid4())


def get_stocktakes(conn: sqlite3.Connection, tenant_id: str, limit: int = 100) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT st.id, st.stocktake_number, st.status, st.created_at, st.completed_at,"
        " l.name location_name,"
        " COUNT(sl.id) lines_counted,"
        " COALESCE(SUM(sl.variance_qty), 0) total_variance"
        " FROM stocktakes st"
        " LEFT JOIN locations l ON l.id=st.location_id AND l.tenant_id=st.tenant_id"
        " LEFT JOIN stocktake_lines sl ON sl.stocktake_id=st.id AND sl.tenant_id=st.tenant_id"
        " WHERE st.tenant_id=?"
        " GROUP BY st.id ORDER BY st.created_at DESC LIMIT ?",
        (tenant_id, limit)).fetchall()
    
    items = []
    for r in rows:
        items.append({
            "id": r[0], "stocktake_number": r[1], "status": r[2],
            "created_at": r[3], "completed_at": r[4], "location_name": r[5],
            "lines_counted": r[6], "total_variance": float(r[7])
        })
    return {"items": items}


def post_stocktake(conn: sqlite3.Connection, tenant_id: str, location_id: str, lines: List[Dict[str, Any]], notes: str = "") -> Dict[str, Any]:
    if not lines:
        raise InventoryError("a stocktake requires at least one line")
    
    loc = conn.execute("SELECT id FROM locations WHERE id=? AND tenant_id=?", (location_id, tenant_id)).fetchone()
    if not loc:
        raise InventoryError("unknown location")

    resolved = []
    for raw in lines:
        pid = raw.get("product_id")
        prod = conn.execute("SELECT id, name, sku, unit_cost FROM products WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()
        if not prod:
            raise InventoryError(f"product {pid} not found")
        
        counted = float(raw.get("counted_qty", 0))
        if counted < 0:
            raise InventoryError(f"{prod[2]}: counted quantity cannot be negative")

        inv = conn.execute("SELECT id, on_hand FROM inventory_levels WHERE tenant_id=? AND product_id=? AND location_id=?", (tenant_id, pid, location_id)).fetchone()
        expected = inv[1] if inv else 0.0
        variance = counted - expected
        cost = float(prod[3] or 0)

        resolved.append({
            "product_id": pid, "expected": expected, "counted": counted,
            "variance": variance, "cost": cost, "inv_id": inv[0] if inv else None
        })

    if all(r["variance"] == 0 for r in resolved):
        raise InventoryError("no discrepancies found, nothing to adjust")

    st_id = _uid()
    number = next_number(conn, tenant_id, "stocktakes", "stocktake_number", "ST")
    now = datetime.now().isoformat()

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO stocktakes(id, tenant_id, stocktake_number, location_id, status, created_at, completed_at, notes) "
            "VALUES(?, ?, ?, ?, 'completed', ?, ?, ?)",
            (st_id, tenant_id, number, location_id, now, now, notes or None)
        )

        for i, r in enumerate(resolved, start=1):
            if r["variance"] == 0:
                continue

            conn.execute(
                "INSERT INTO stocktake_lines(id, tenant_id, stocktake_id, product_id, expected_qty, counted_qty, variance_qty, unit_cost) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (_uid(), tenant_id, st_id, r["product_id"], r["expected"], r["counted"], r["variance"], r["cost"])
            )

            if r["inv_id"]:
                conn.execute("UPDATE inventory_levels SET on_hand=? WHERE id=? AND tenant_id=?", (r["counted"], r["inv_id"], tenant_id))
            else:
                conn.execute(
                    "INSERT INTO inventory_levels(id, tenant_id, product_id, location_id, on_hand, on_order, reserved, backorder) "
                    "VALUES(?, ?, ?, ?, ?, 0, 0, 0)",
                    (_uid(), tenant_id, r["product_id"], location_id, r["counted"])
                )

            conn.execute(
                "INSERT INTO stock_movements(tenant_id, product_id, location_id, movement_type, quantity, occurred_at, reference_type, reference_id, idempotency_key) "
                "VALUES(?, ?, ?, 'adjustment', ?, ?, 'stocktake', ?, ?)",
                (tenant_id, r["product_id"], location_id, r["variance"], now, st_id, f"st:{st_id}:{i}")
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    variances = [r for r in resolved if r["variance"] != 0]
    return {
        "id": st_id, "stocktake_number": number,
        "lines_adjusted": len(variances),
        "total_variance": sum(r["variance"] for r in variances)
    }


def get_transfers(conn: sqlite3.Connection, tenant_id: str, limit: int = 100) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT t.id, t.transfer_number, t.status, t.created_at, t.completed_at,"
        " l1.name from_location, l2.name to_location,"
        " COUNT(tl.id) lines,"
        " COALESCE(SUM(tl.quantity), 0) total_qty"
        " FROM stock_transfers t"
        " LEFT JOIN locations l1 ON l1.id=t.from_location_id AND l1.tenant_id=t.tenant_id"
        " LEFT JOIN locations l2 ON l2.id=t.to_location_id AND l2.tenant_id=t.tenant_id"
        " LEFT JOIN stock_transfer_lines tl ON tl.transfer_id=t.id AND tl.tenant_id=t.tenant_id"
        " WHERE t.tenant_id=?"
        " GROUP BY t.id ORDER BY t.created_at DESC LIMIT ?",
        (tenant_id, limit)).fetchall()
    
    items = []
    for r in rows:
        items.append({
            "id": r[0], "transfer_number": r[1], "status": r[2],
            "created_at": r[3], "completed_at": r[4],
            "from_location": r[5], "to_location": r[6],
            "lines": r[7], "total_qty": float(r[8])
        })
    return {"items": items}


def post_transfer(conn: sqlite3.Connection, tenant_id: str, from_loc: str, to_loc: str, lines: List[Dict[str, Any]], notes: str = "") -> Dict[str, Any]:
    if not lines:
        raise InventoryError("a transfer requires at least one line")
    
    if from_loc == to_loc:
        raise InventoryError("cannot transfer to the same location")

    l1 = conn.execute("SELECT id FROM locations WHERE id=? AND tenant_id=?", (from_loc, tenant_id)).fetchone()
    l2 = conn.execute("SELECT id FROM locations WHERE id=? AND tenant_id=?", (to_loc, tenant_id)).fetchone()
    if not l1 or not l2:
        raise InventoryError("unknown location(s)")

    resolved = []
    for raw in lines:
        pid = raw.get("product_id")
        prod = conn.execute("SELECT id, name, sku FROM products WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()
        if not prod:
            raise InventoryError(f"product {pid} not found")
        
        qty = float(raw.get("quantity", 0))
        if qty <= 0:
            raise InventoryError(f"{prod[2]}: transfer quantity must be positive")

        inv_from = conn.execute("SELECT id, on_hand FROM inventory_levels WHERE tenant_id=? AND product_id=? AND location_id=?", (tenant_id, pid, from_loc)).fetchone()
        on_hand_from = inv_from[1] if inv_from else 0.0
        if on_hand_from < qty:
            raise InventoryError(f"{prod[2]}: only {on_hand_from} in stock at source location")

        inv_to = conn.execute("SELECT id FROM inventory_levels WHERE tenant_id=? AND product_id=? AND location_id=?", (tenant_id, pid, to_loc)).fetchone()

        resolved.append({
            "product_id": pid, "quantity": qty,
            "inv_from_id": inv_from[0], "inv_to_id": inv_to[0] if inv_to else None
        })

    tr_id = _uid()
    number = next_number(conn, tenant_id, "stock_transfers", "transfer_number", "TR")
    now = datetime.now().isoformat()

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO stock_transfers(id, tenant_id, transfer_number, from_location_id, to_location_id, status, created_at, completed_at, notes) "
            "VALUES(?, ?, ?, ?, ?, 'completed', ?, ?, ?)",
            (tr_id, tenant_id, number, from_loc, to_loc, now, now, notes or None)
        )

        for i, r in enumerate(resolved, start=1):
            conn.execute(
                "INSERT INTO stock_transfer_lines(id, tenant_id, transfer_id, product_id, quantity) VALUES(?, ?, ?, ?, ?)",
                (_uid(), tenant_id, tr_id, r["product_id"], r["quantity"])
            )

            # Deduct from source
            conn.execute("UPDATE inventory_levels SET on_hand=on_hand-? WHERE id=? AND tenant_id=?", (r["quantity"], r["inv_from_id"], tenant_id))
            
            # Add to destination
            if r["inv_to_id"]:
                conn.execute("UPDATE inventory_levels SET on_hand=on_hand+? WHERE id=? AND tenant_id=?", (r["quantity"], r["inv_to_id"], tenant_id))
            else:
                conn.execute(
                    "INSERT INTO inventory_levels(id, tenant_id, product_id, location_id, on_hand, on_order, reserved, backorder) "
                    "VALUES(?, ?, ?, ?, ?, 0, 0, 0)",
                    (_uid(), tenant_id, r["product_id"], to_loc, r["quantity"])
                )

            # Insert movements
            conn.execute(
                "INSERT INTO stock_movements(tenant_id, product_id, location_id, movement_type, quantity, occurred_at, reference_type, reference_id, idempotency_key) "
                "VALUES(?, ?, ?, 'transfer_out', ?, ?, 'transfer', ?, ?)",
                (tenant_id, r["product_id"], from_loc, -r["quantity"], now, tr_id, f"tr:{tr_id}:{i}:out")
            )
            conn.execute(
                "INSERT INTO stock_movements(tenant_id, product_id, location_id, movement_type, quantity, occurred_at, reference_type, reference_id, idempotency_key) "
                "VALUES(?, ?, ?, 'transfer_in', ?, ?, 'transfer', ?, ?)",
                (tenant_id, r["product_id"], to_loc, r["quantity"], now, tr_id, f"tr:{tr_id}:{i}:in")
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "id": tr_id, "transfer_number": number,
        "lines": len(resolved),
        "total_qty": sum(r["quantity"] for r in resolved)
    }
