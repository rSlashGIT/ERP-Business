"""Price advice for a tenant's catalogue, straight off the sales ledger.

`app/domain/pricing.py` is the maths. This pulls the evidence out of sqlite —
what each style actually sold for, and how much of it moved — and hands the
answer to the Price screen.

Every query carries `tenant_id`. One shop's elasticity is one shop's business.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "services" / "erp-api"))

from app.domain.pricing import CLIFF_SHELF, CLIFF_SHELF_ABOVE, advise  # noqa: E402

#: How long a season is assumed to run when nobody has said otherwise.
DEFAULT_SEASON_DAYS = 90


def _style_rows(conn: sqlite3.Connection, tenant_id: str,
                style_id: Optional[str] = None) -> List[sqlite3.Row]:
    sql = ("SELECT s.id, s.style_code, s.name,"
           " AVG(p.unit_cost) cost, AVG(p.unit_price) price,"
           " COALESCE(SUM(i.on_hand),0) on_hand"
           " FROM product_styles s"
           " JOIN products p ON p.style_id=s.id AND p.tenant_id=s.tenant_id"
           " LEFT JOIN inventory_levels i ON i.product_id=p.id AND i.tenant_id=s.tenant_id"
           " WHERE s.tenant_id=? AND s.is_active=1 AND p.is_active=1")
    args: List[Any] = [tenant_id]
    if style_id:
        sql += " AND s.id=?"
        args.append(style_id)
    sql += " GROUP BY s.id ORDER BY s.style_code"
    return conn.execute(sql, args).fetchall()


def _observations(conn: sqlite3.Connection, tenant_id: str, style_id: str):
    """(price actually charged per piece, units sold at it) for one style.

    The price is the taxable value per piece AFTER discount — which is exactly
    what drives the GST slab and what the elasticity fit needs. Reading
    `unit_price` off the product master instead would give one price point and
    no elasticity at all.
    """
    rows = conn.execute(
        "SELECT ROUND(l.taxable_value / NULLIF(l.quantity,0), 0) per_piece,"
        " SUM(l.quantity) units, substr(i.invoice_date,1,7) month,"
        " COUNT(DISTINCT i.invoice_date) days"
        " FROM sales_invoice_lines l"
        " JOIN products p ON p.id = l.product_id AND p.tenant_id = l.tenant_id"
        " JOIN sales_invoices i ON i.id = l.invoice_id AND i.tenant_id = l.tenant_id"
        " WHERE l.tenant_id=? AND p.style_id=? AND i.status!='cancelled'"
        " AND l.quantity > 0"
        " GROUP BY month, per_piece HAVING per_piece > 0"
        " ORDER BY month, per_piece", (tenant_id, style_id)).fetchall()
    # Two corrections, both of which flip the sign of the fitted elasticity if
    # you skip them:
    #
    #   month  — cancels the festive demand shock. Apparel sells at full price
    #            during Navratri and discounts in July, so a raw fit concludes
    #            that raising prices sells more.
    #   /days  — units must be a RATE, not a total. Full price is offered on
    #            far more days than a sale price, so it accumulates more units
    #            purely from exposure. Dividing by the number of days that price
    #            was actually on the tag is what makes the two comparable.
    return [(float(r[0]), float(r[1]) / max(1, int(r[3])), r[2]) for r in rows]


def _daily_units(conn: sqlite3.Connection, tenant_id: str, style_id: str) -> float:
    r = conn.execute(
        "SELECT COALESCE(SUM(l.quantity),0), MIN(i.invoice_date), MAX(i.invoice_date)"
        " FROM sales_invoice_lines l"
        " JOIN products p ON p.id=l.product_id AND p.tenant_id=l.tenant_id"
        " JOIN sales_invoices i ON i.id=l.invoice_id AND i.tenant_id=l.tenant_id"
        " WHERE l.tenant_id=? AND p.style_id=? AND i.status!='cancelled'",
        (tenant_id, style_id)).fetchone()
    units = float(r[0] or 0)
    if not r[1] or not r[2]:
        return 0.0
    span = max(1, (date.fromisoformat(r[2]) - date.fromisoformat(r[1])).days + 1)
    return units / span


def advise_style(conn, tenant_id: str, row, days_left: int) -> Dict[str, Any]:
    a = advise(
        style_code=row[1], style_name=row[2],
        cost=float(row[3] or 0), current_taxable=float(row[4] or 0),
        on_hand=float(row[5] or 0),
        observations=_observations(conn, tenant_id, row[0]),
        daily_units=_daily_units(conn, tenant_id, row[0]),
        days_left=days_left)
    d = a.as_dict()
    d["style_id"] = row[0]
    d["cliff_note"] = a.cliff_note
    return d


def price_advice(conn: sqlite3.Connection, tenant_id: str,
                 days_left: int = DEFAULT_SEASON_DAYS) -> Dict[str, Any]:
    """Every style, ranked by how much money the advice is worth."""
    items = [advise_style(conn, tenant_id, r, days_left)
             for r in _style_rows(conn, tenant_id)]

    def worth(d: Dict[str, Any]) -> float:
        return max(abs(d["annual_gain"]), abs(d["markdown"]["gain"]))

    items.sort(key=worth, reverse=True)
    return {
        "items": items,
        "dead_zone": {"low": CLIFF_SHELF, "high": CLIFF_SHELF_ABOVE},
        "summary": {
            "styles": len(items),
            "in_dead_zone": sum(1 for d in items if d["cliff"]),
            "need_markdown": sum(1 for d in items
                                 if d["markdown"]["urgency"] in ("act", "urgent")),
            "opportunity": round(sum(max(0.0, d["annual_gain"]) for d in items), 2),
            "markdown_opportunity": round(
                sum(max(0.0, d["markdown"]["gain"]) for d in items), 2),
        },
    }


def price_detail(conn: sqlite3.Connection, tenant_id: str, style_id: str,
                 days_left: int = DEFAULT_SEASON_DAYS) -> Optional[Dict[str, Any]]:
    rows = _style_rows(conn, tenant_id, style_id)
    if not rows:
        return None
    return advise_style(conn, tenant_id, rows[0], days_left)


def apply_price(conn: sqlite3.Connection, tenant_id: str, style_id: str,
                taxable: float) -> Dict[str, Any]:
    """Accept the advice: write the new price onto every variant of the style."""
    if taxable <= 0:
        raise ValueError("price must be positive")
    style = conn.execute(
        "SELECT id, name FROM product_styles WHERE tenant_id=? AND id=?",
        (tenant_id, style_id)).fetchone()
    if style is None:
        raise ValueError("unknown style for this business")
    cur = conn.execute(
        "UPDATE products SET unit_price=? WHERE tenant_id=? AND style_id=?",
        (round(float(taxable), 2), tenant_id, style_id))
    conn.commit()
    return {"style_id": style_id, "style_name": style[1],
            "new_taxable": round(float(taxable), 2), "variants_updated": cur.rowcount}
