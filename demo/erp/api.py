"""Read APIs for the demo ERP. Every query is tenant-scoped, no exceptions."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(conn, sql, args=()) -> Optional[Dict[str, Any]]:
    r = _rows(conn, sql, args)
    return r[0] if r else None


# ─────────────────────────── dashboard ───────────────────────────

def dashboard(conn: sqlite3.Connection, t: str) -> Dict[str, Any]:
    today = date.today().isoformat()
    m0 = date.today().replace(day=1).isoformat()
    d30 = (date.today() - timedelta(days=30)).isoformat()

    today_row = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(grand_total),0) v FROM"
                     " sales_invoices WHERE tenant_id=? AND invoice_date=?"
                     " AND status!='cancelled'", (t, today))
    month = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(grand_total),0) v,"
                 " COALESCE(SUM(taxable_total),0) tv FROM sales_invoices"
                 " WHERE tenant_id=? AND invoice_date>=? AND status!='cancelled'", (t, m0))
    stock = _one(conn, "SELECT COALESCE(SUM(i.on_hand*p.unit_cost),0) cost,"
                 " COALESCE(SUM(i.on_hand*p.unit_price),0) retail, COALESCE(SUM(i.on_hand),0) units"
                 " FROM inventory_levels i JOIN products p ON p.id=i.product_id"
                 " WHERE i.tenant_id=?", (t,))
    low = _one(conn, "SELECT COUNT(*) n FROM inventory_levels WHERE tenant_id=?"
               " AND reorder_point IS NOT NULL AND on_hand<=reorder_point", (t,))
    out = _one(conn, "SELECT COUNT(*) n FROM inventory_levels WHERE tenant_id=? AND on_hand<=0", (t,))
    recv = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(grand_total-amount_paid),0) v FROM"
                " sales_invoices WHERE tenant_id=? AND status IN ('posted','part_paid')"
                " AND grand_total-amount_paid>0.01", (t,))
    overdue = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(grand_total-amount_paid),0) v FROM"
                   " sales_invoices WHERE tenant_id=? AND status IN ('posted','part_paid')"
                   " AND grand_total-amount_paid>0.01 AND due_date<?", (t, today))
    reco = _one(conn, "SELECT COUNT(*) n, COALESCE(SUM(line_value),0) v FROM recommendations"
                " WHERE tenant_id=? AND status='pending'", (t,))
    crit = _one(conn, "SELECT COUNT(*) n FROM recommendations WHERE tenant_id=?"
                " AND status='pending' AND urgency='critical'", (t,))
    gst = _one(conn, "SELECT COALESCE(SUM(cgst_total+sgst_total+igst_total),0) v FROM"
               " sales_invoices WHERE tenant_id=? AND invoice_date>=? AND status!='cancelled'",
               (t, m0))

    trend = _rows(conn, "SELECT invoice_date d, SUM(grand_total) v, COUNT(*) n FROM"
                  " sales_invoices WHERE tenant_id=? AND invoice_date>=? AND status!='cancelled'"
                  " GROUP BY invoice_date ORDER BY invoice_date", (t, d30))
    top = _rows(conn, "SELECT ps.name style, SUM(l.quantity) qty, SUM(l.line_total) value"
                " FROM sales_invoice_lines l JOIN products p ON p.id=l.product_id"
                " JOIN product_styles ps ON ps.id=p.style_id"
                " JOIN sales_invoices i ON i.id=l.invoice_id"
                " WHERE l.tenant_id=? AND i.invoice_date>=? GROUP BY ps.name"
                " ORDER BY value DESC LIMIT 6", (t, d30))
    return {
        "today": {"invoices": today_row["n"], "sales": round(today_row["v"], 2)},
        "month": {"invoices": month["n"], "sales": round(month["v"], 2),
                  "taxable": round(month["tv"], 2), "gst": round(gst["v"], 2)},
        "inventory": {"stock_cost": round(stock["cost"], 2),
                      "stock_retail": round(stock["retail"], 2),
                      "units": int(stock["units"]),
                      "low_stock": low["n"], "out_of_stock": out["n"]},
        "receivables": {"count": recv["n"], "value": round(recv["v"], 2),
                        "overdue_count": overdue["n"], "overdue_value": round(overdue["v"], 2)},
        "procurement": {"pending": reco["n"], "value": round(reco["v"], 2),
                        "critical": crit["n"]},
        "trend": trend, "top_styles": top,
    }


# ─────────────────────────── catalogue ───────────────────────────

def styles(conn, t, search: str = "", category: str = "") -> Dict[str, Any]:
    sql = ("SELECT s.*, COUNT(p.id) variant_count, COALESCE(SUM(inv.on_hand),0) on_hand"
           " FROM product_styles s LEFT JOIN products p ON p.style_id=s.id AND p.tenant_id=s.tenant_id"
           " LEFT JOIN inventory_levels inv ON inv.product_id=p.id AND inv.tenant_id=s.tenant_id"
           " WHERE s.tenant_id=? AND s.is_active=1")
    args: List[Any] = [t]
    if search:
        sql += " AND (s.style_code LIKE ? OR s.name LIKE ? OR s.brand LIKE ?)"
        args += [f"%{search}%"] * 3
    if category:
        sql += " AND s.category=?"; args.append(category)
    sql += " GROUP BY s.id ORDER BY s.style_code"
    out = _rows(conn, sql, args)
    for s in out:
        # Ordered by size_seq: apparel sizes do not sort lexically.
        s["variants"] = _rows(
            conn, "SELECT p.id,p.sku,p.size,p.size_seq,p.colour,p.barcode,p.unit_price,"
            " COALESCE(SUM(i.on_hand),0) on_hand FROM products p"
            " LEFT JOIN inventory_levels i ON i.product_id=p.id AND i.tenant_id=p.tenant_id"
            " WHERE p.tenant_id=? AND p.style_id=? GROUP BY p.id"
            " ORDER BY COALESCE(p.size_seq, 99999), p.size, p.colour", (t, s["id"]))
        seen: Dict[str, int] = {}
        for v in s["variants"]:
            if v["size"] not in seen:
                seen[v["size"]] = v["size_seq"] if v["size_seq"] is not None else 99999
        s["sizes"] = [k for k, _ in sorted(seen.items(), key=lambda kv: kv[1])]
        s["colours"] = sorted({v["colour"] for v in s["variants"] if v["colour"]})
    return {"total": len(out), "items": out}


def inventory(conn, t, search: str = "", low_only: str = "",
              location: str = "") -> Dict[str, Any]:
    sql = ("SELECT p.id product_id,p.sku,p.name,p.size,p.size_seq,p.colour,p.barcode,"
           " p.unit_cost,p.unit_price,l.code location_code,i.on_hand,i.on_order,"
           " i.reorder_point,i.on_hand*p.unit_cost stock_value,"
           " ps.style_code, ps.name style_name"
           " FROM inventory_levels i JOIN products p ON p.id=i.product_id"
           " JOIN locations l ON l.id=i.location_id"
           " LEFT JOIN product_styles ps ON ps.id=p.style_id"
           " WHERE i.tenant_id=? AND p.is_active=1")
    args: List[Any] = [t]
    if search:
        sql += " AND (p.sku LIKE ? OR p.name LIKE ? OR p.barcode=?)"
        args += [f"%{search}%", f"%{search}%", search]
    if location:
        sql += " AND l.code=?"; args.append(location)
    if low_only == "true":
        sql += " AND i.reorder_point IS NOT NULL AND i.on_hand<=i.reorder_point"
    sql += " ORDER BY ps.style_code, COALESCE(p.size_seq,99999), p.colour LIMIT 500"
    items = _rows(conn, sql, args)
    for i in items:
        i["status"] = ("out" if i["on_hand"] <= 0
                       else "low" if i["reorder_point"] and i["on_hand"] <= i["reorder_point"]
                       else "ok")
    return {"total": len(items), "items": items}


def lookup_barcode(conn, t, barcode: str) -> Optional[Dict[str, Any]]:
    p = _one(conn, "SELECT p.*, ps.name style_name, ps.style_code FROM products p"
             " LEFT JOIN product_styles ps ON ps.id=p.style_id"
             " WHERE p.tenant_id=? AND p.barcode=? AND p.is_active=1", (t, barcode))
    if not p:
        return None
    p["stock"] = _rows(conn, "SELECT l.code location_code, i.on_hand FROM inventory_levels i"
                       " JOIN locations l ON l.id=i.location_id"
                       " WHERE i.tenant_id=? AND i.product_id=?", (t, p["id"]))
    p["total_on_hand"] = sum(s["on_hand"] for s in p["stock"])
    return p


def search_products(conn, t, term: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Billing-screen search: barcode exact-match wins, then SKU/name."""
    if not term:
        return []
    exact = _rows(conn, "SELECT p.id,p.sku,p.name,p.size,p.colour,p.barcode,p.unit_price,"
                  " p.hsn_code, COALESCE(SUM(i.on_hand),0) on_hand FROM products p"
                  " LEFT JOIN inventory_levels i ON i.product_id=p.id AND i.tenant_id=p.tenant_id"
                  " WHERE p.tenant_id=? AND p.barcode=? AND p.is_active=1 GROUP BY p.id",
                  (t, term))
    if exact:
        return exact
    return _rows(conn, "SELECT p.id,p.sku,p.name,p.size,p.colour,p.barcode,p.unit_price,"
                 " p.hsn_code, COALESCE(SUM(i.on_hand),0) on_hand FROM products p"
                 " LEFT JOIN inventory_levels i ON i.product_id=p.id AND i.tenant_id=p.tenant_id"
                 " WHERE p.tenant_id=? AND p.is_active=1 AND (p.sku LIKE ? OR p.name LIKE ?)"
                 " GROUP BY p.id ORDER BY p.name LIMIT ?",
                 (t, f"%{term}%", f"%{term}%", limit))


# ─────────────────────────── sales ───────────────────────────

def customers(conn, t, search: str = "") -> Dict[str, Any]:
    sql = ("SELECT c.*, COALESCE(SUM(CASE WHEN i.status IN ('posted','part_paid')"
           " THEN i.grand_total-i.amount_paid ELSE 0 END),0) outstanding,"
           " COUNT(DISTINCT i.id) invoice_count"
           " FROM customers c LEFT JOIN sales_invoices i ON i.customer_id=c.id"
           " AND i.tenant_id=c.tenant_id WHERE c.tenant_id=? AND c.is_active=1")
    args: List[Any] = [t]
    if search:
        sql += " AND (c.name LIKE ? OR c.code LIKE ? OR c.phone LIKE ?)"
        args += [f"%{search}%"] * 3
    sql += " GROUP BY c.id ORDER BY c.is_walkin DESC, c.name"
    return {"items": _rows(conn, sql, args)}


def invoices(conn, t, status: str = "", search: str = "", limit: int = 200) -> Dict[str, Any]:
    sql = ("SELECT i.*, c.name customer_name, c.gstin customer_gstin,"
           " i.grand_total-i.amount_paid balance_due FROM sales_invoices i"
           " JOIN customers c ON c.id=i.customer_id WHERE i.tenant_id=?")
    args: List[Any] = [t]
    if status and status != "all":
        sql += " AND i.status=?"; args.append(status)
    if search:
        sql += " AND (i.invoice_number LIKE ? OR c.name LIKE ?)"
        args += [f"%{search}%"] * 2
    sql += " ORDER BY i.invoice_date DESC, i.invoice_number DESC LIMIT ?"
    args.append(limit)
    return {"items": _rows(conn, sql, args)}


def invoice_detail(conn, t, invoice_id: str) -> Optional[Dict[str, Any]]:
    inv = _one(conn, "SELECT i.*, c.name customer_name, c.gstin customer_gstin,"
               " c.address customer_address, c.phone customer_phone,"
               " c.state_code customer_state, l.name location_name"
               " FROM sales_invoices i JOIN customers c ON c.id=i.customer_id"
               " JOIN locations l ON l.id=i.location_id"
               " WHERE i.tenant_id=? AND i.id=?", (t, invoice_id))
    if not inv:
        return None
    inv["lines"] = _rows(conn, "SELECT l.*, p.sku, p.name product_name, p.size, p.colour"
                         " FROM sales_invoice_lines l JOIN products p ON p.id=l.product_id"
                         " WHERE l.tenant_id=? AND l.invoice_id=? ORDER BY l.line_no",
                         (t, invoice_id))
    inv["balance_due"] = round(inv["grand_total"] - inv["amount_paid"], 2)
    summary: Dict[float, Dict[str, float]] = {}
    for l in inv["lines"]:
        b = summary.setdefault(l["gst_rate"], {"taxable": 0.0, "cgst": 0.0,
                                               "sgst": 0.0, "igst": 0.0})
        for k in ("taxable", "cgst", "sgst", "igst"):
            b[k] += l["taxable_value"] if k == "taxable" else l[k]
    inv["rate_summary"] = [{"rate": r, **{k: round(v, 2) for k, v in vals.items()}}
                           for r, vals in sorted(summary.items())]
    from app.domain.gst import amount_in_words
    inv["amount_in_words"] = amount_in_words(inv["grand_total"])
    inv["tenant"] = _one(conn, "SELECT * FROM tenants WHERE id=?", (t,))
    return inv


def receivables(conn, t) -> Dict[str, Any]:
    today = date.today()
    rows = _rows(conn, "SELECT i.id,i.invoice_number,i.invoice_date,i.due_date,"
                 " i.grand_total,i.amount_paid,i.grand_total-i.amount_paid balance,"
                 " c.name customer_name,c.id customer_id,c.phone"
                 " FROM sales_invoices i JOIN customers c ON c.id=i.customer_id"
                 " WHERE i.tenant_id=? AND i.status IN ('posted','part_paid')"
                 " AND i.grand_total-i.amount_paid>0.01 ORDER BY i.due_date", (t,))
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    by_cust: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        days = (today - date.fromisoformat(r["due_date"])).days if r["due_date"] else 0
        r["days_overdue"] = max(0, days)
        b = ("current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60
             else "61-90" if days <= 90 else "90+")
        r["bucket"] = b
        buckets[b] += r["balance"]
        c = by_cust.setdefault(r["customer_id"], {
            "customer_name": r["customer_name"], "phone": r["phone"],
            "total": 0.0, "oldest_days": 0, "invoices": 0})
        c["total"] += r["balance"]
        c["invoices"] += 1
        c["oldest_days"] = max(c["oldest_days"], r["days_overdue"])
    return {"items": rows,
            "buckets": {k: round(v, 2) for k, v in buckets.items()},
            "total": round(sum(buckets.values()), 2),
            "by_customer": sorted(by_cust.values(), key=lambda x: -x["total"])}


# ─────────────────────────── procurement ───────────────────────────

def recommendations(conn, t, status: str = "pending") -> Dict[str, Any]:
    sql = ("SELECT r.*, p.sku, p.name product_name, p.size, p.colour,"
           " ps.style_code, l.code location_code, s.name supplier_name,"
           " inv.on_hand FROM recommendations r"
           " JOIN products p ON p.id=r.product_id"
           " LEFT JOIN product_styles ps ON ps.id=p.style_id"
           " JOIN locations l ON l.id=r.location_id"
           " LEFT JOIN suppliers s ON s.id=r.supplier_id"
           " LEFT JOIN inventory_levels inv ON inv.product_id=r.product_id"
           " AND inv.location_id=r.location_id AND inv.tenant_id=r.tenant_id"
           " WHERE r.tenant_id=?")
    args: List[Any] = [t]
    if status and status != "all":
        sql += " AND r.status=?"; args.append(status)
    rows = _rows(conn, sql, args)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda r: (order.get(r["urgency"], 9), -(r["line_value"] or 0)))
    for r in rows:
        try:
            r["rationale"] = json.loads(r["rationale"] or "{}")
        except Exception:
            r["rationale"] = {}
    summary: Dict[str, Dict[str, float]] = {}
    for r in rows:
        b = summary.setdefault(r["urgency"], {"count": 0, "value": 0.0})
        b["count"] += 1
        b["value"] = round(b["value"] + (r["line_value"] or 0), 2)
    return {"total": len(rows), "items": rows, "summary": summary}


def purchase_orders(conn, t) -> Dict[str, Any]:
    return {"items": _rows(conn,
            "SELECT po.*, s.name supplier_name, COUNT(l.id) line_count"
            " FROM purchase_orders po LEFT JOIN suppliers s ON s.id=po.supplier_id"
            " LEFT JOIN purchase_order_lines l ON l.purchase_order_id=po.id"
            " AND l.tenant_id=po.tenant_id WHERE po.tenant_id=? GROUP BY po.id"
            " ORDER BY po.created_at DESC LIMIT 100", (t,))}


# ─────────────────────────── reports ───────────────────────────

def reports(conn, t, kind: str, days: int = 90) -> Dict[str, Any]:
    since = (date.today() - timedelta(days=days)).isoformat()
    if kind == "sales_by_style":
        return {"rows": _rows(conn,
            "SELECT ps.style_code, ps.name style, ps.category,"
            " SUM(l.quantity) qty, SUM(l.taxable_value) revenue,"
            " SUM(l.quantity*p.unit_cost) cost,"
            " SUM(l.taxable_value)-SUM(l.quantity*p.unit_cost) margin"
            " FROM sales_invoice_lines l JOIN products p ON p.id=l.product_id"
            " JOIN product_styles ps ON ps.id=p.style_id"
            " JOIN sales_invoices i ON i.id=l.invoice_id"
            " WHERE l.tenant_id=? AND i.invoice_date>=? AND i.status!='cancelled'"
            " GROUP BY ps.id ORDER BY revenue DESC", (t, since))}
    if kind == "size_curve":
        # Ordered by size_seq — the reason that column exists.
        return {"rows": _rows(conn,
            "SELECT p.size, MIN(COALESCE(p.size_seq,99999)) seq, SUM(l.quantity) qty,"
            " SUM(l.taxable_value) revenue FROM sales_invoice_lines l"
            " JOIN products p ON p.id=l.product_id"
            " JOIN sales_invoices i ON i.id=l.invoice_id"
            " WHERE l.tenant_id=? AND i.invoice_date>=? AND p.size IS NOT NULL"
            " GROUP BY p.size ORDER BY seq", (t, since))}
    if kind == "gst_summary":
        return {"rows": _rows(conn,
            "SELECT substr(invoice_date,1,7) month, gst_rate rate,"
            " SUM(taxable_value) taxable, SUM(cgst) cgst, SUM(sgst) sgst, SUM(igst) igst"
            " FROM sales_invoice_lines l JOIN sales_invoices i ON i.id=l.invoice_id"
            " WHERE l.tenant_id=? AND i.status!='cancelled'"
            " GROUP BY month, rate ORDER BY month DESC, rate", (t,))}
    if kind == "dead_stock":
        return {"rows": _rows(conn,
            "SELECT p.sku, p.name, p.size, p.colour, SUM(i.on_hand) on_hand,"
            " SUM(i.on_hand*p.unit_cost) tied_up,"
            " COALESCE((SELECT SUM(l.quantity) FROM sales_invoice_lines l"
            "  JOIN sales_invoices si ON si.id=l.invoice_id"
            "  WHERE l.product_id=p.id AND l.tenant_id=p.tenant_id"
            "  AND si.invoice_date>=?),0) sold"
            " FROM products p JOIN inventory_levels i ON i.product_id=p.id"
            " AND i.tenant_id=p.tenant_id WHERE p.tenant_id=? GROUP BY p.id"
            " HAVING on_hand>0 AND sold=0 ORDER BY tied_up DESC LIMIT 50", (since, t))}
    return {"rows": []}
