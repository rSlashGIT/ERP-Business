"""Writing an analysed stock list into a tenant's catalogue.

`app/domain/importing.py` decides WHAT the sheet says. This decides what to do
about it, against sqlite, for exactly one tenant.

THREE PROPERTIES THIS MUST HAVE
-------------------------------
1. **Tenant-scoped.** Every read and every write carries `tenant_id`. Two shops
   legitimately use the same style codes and the same barcodes; an import that
   matched on code alone would silently overwrite a competitor's catalogue.

2. **Idempotent.** Running the same file twice must not double the stock. A
   shopkeeper WILL re-import — they'll fix one price and send the sheet again.
   So stock is SET to the sheet's figure, never added to, and products are
   matched on (tenant, sku) and updated in place.

3. **All-or-nothing.** One bad row must not leave a half-loaded catalogue.
   Everything runs inside a single transaction and rolls back on any error.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "services" / "erp-api"))

from app.domain.importing import Analysis, analyse   # noqa: E402

from .sizes import size_seq                          # noqa: E402


class ImportError_(Exception):
    """Refused before anything was written."""


def _uid() -> str:
    return str(uuid.uuid4())


def existing_skus(conn: sqlite3.Connection, tenant_id: str) -> List[str]:
    """SKUs already on THIS tenant — never all tenants.

    Passing an unscoped list would tell one shop which codes another shop uses,
    via the "already in the system" warning on the preview screen.
    """
    return [r[0] for r in conn.execute(
        "SELECT sku FROM products WHERE tenant_id=?", (tenant_id,))]


def analyse_for_tenant(conn: sqlite3.Connection, tenant_id: str, text,
                       mapping: Optional[Dict[str, Optional[str]]] = None) -> Analysis:
    return analyse(text, mapping, existing_skus=existing_skus(conn, tenant_id))


def commit(conn: sqlite3.Connection, tenant_id: str, text,
           mapping: Optional[Dict[str, Optional[str]]] = None,
           location_code: Optional[str] = None) -> Dict[str, Any]:
    """Write the importable rows. Refused rows are skipped, never guessed at.

    Returns counts the UI shows back to the shopkeeper. Raises before writing
    anything if the sheet cannot be read at all.
    """
    a = analyse_for_tenant(conn, tenant_id, text, mapping)
    if a.fatal:
        raise ImportError_(a.fatal)
    if not a.importable:
        raise ImportError_("Nothing in this sheet could be imported - "
                           "every row was refused.")

    loc = conn.execute(
        "SELECT id FROM locations WHERE tenant_id=?"
        + (" AND code=?" if location_code else "") + " ORDER BY code LIMIT 1",
        (tenant_id, location_code) if location_code else (tenant_id,)).fetchone()
    if loc is None:
        raise ImportError_("This business has no stock location to import into.")
    location_id = loc[0]

    styles_new = styles_updated = 0
    products_new = products_updated = 0
    units = 0.0

    try:
        conn.execute("BEGIN")

        style_ids: Dict[str, str] = {}
        for row in a.importable:
            v = row.values
            code = v["style_code"]
            if code in style_ids:
                continue
            # Scoped on tenant AND code: the same style code on another tenant
            # is a different garment and must not be touched.
            found = conn.execute(
                "SELECT id FROM product_styles WHERE tenant_id=? AND style_code=?",
                (tenant_id, code)).fetchone()
            if found:
                style_ids[code] = found[0]
                conn.execute(
                    "UPDATE product_styles SET name=?, brand=COALESCE(NULLIF(?,''),brand),"
                    " category=COALESCE(NULLIF(?,''),category),"
                    " hsn_code=COALESCE(NULLIF(?,''),hsn_code), is_active=1"
                    " WHERE id=? AND tenant_id=?",
                    (v["style_name"], v["brand"], v["category"], v["hsn"],
                     found[0], tenant_id))
                styles_updated += 1
            else:
                sid = _uid()
                conn.execute(
                    "INSERT INTO product_styles(id,tenant_id,style_code,name,brand,"
                    "category,hsn_code,is_active) VALUES(?,?,?,?,?,?,?,1)",
                    (sid, tenant_id, code, v["style_name"], v["brand"] or None,
                     v["category"] or None, v["hsn"] or None))
                style_ids[code] = sid
                styles_new += 1

        for row in a.importable:
            v = row.values
            sid = style_ids[v["style_code"]]
            seq = size_seq(v["size"])

            found = conn.execute(
                "SELECT id FROM products WHERE tenant_id=? AND sku=?",
                (tenant_id, v["sku"])).fetchone()
            if found:
                pid = found[0]
                conn.execute(
                    "UPDATE products SET name=?, style_id=?, size=?, size_seq=?,"
                    " colour=?, hsn_code=?, unit_cost=?, unit_price=?, is_active=1"
                    " WHERE id=? AND tenant_id=?",
                    (v["name"], sid, v["size"] or None, seq, v["colour"] or None,
                     v["hsn"] or None, v["cost"], v["price"], pid, tenant_id))
                products_updated += 1
            else:
                pid = _uid()
                conn.execute(
                    "INSERT INTO products(id,tenant_id,sku,name,style_id,size,size_seq,"
                    "colour,barcode,hsn_code,unit_cost,unit_price,is_active)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (pid, tenant_id, v["sku"], v["name"], sid, v["size"] or None, seq,
                     v["colour"] or None, None, v["hsn"] or None,
                     v["cost"], v["price"]))
                products_new += 1

            # SET, never increment — re-importing a corrected sheet must not
            # double the shop's stock.
            existing = conn.execute(
                "SELECT id FROM inventory_levels WHERE tenant_id=? AND product_id=?"
                " AND location_id=?", (tenant_id, pid, location_id)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE inventory_levels SET on_hand=?, reorder_point=?"
                    " WHERE id=? AND tenant_id=?",
                    (v["qty"], v["reorder"] or None, existing[0], tenant_id))
            else:
                conn.execute(
                    "INSERT INTO inventory_levels(id,tenant_id,product_id,location_id,"
                    "on_hand,on_order,reserved,backorder,reorder_point)"
                    " VALUES(?,?,?,?,?,0,0,0,?)",
                    (_uid(), tenant_id, pid, location_id, v["qty"],
                     v["reorder"] or None))
            units += v["qty"]

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "styles_created": styles_new, "styles_updated": styles_updated,
        "products_created": products_new, "products_updated": products_updated,
        "rows_imported": len(a.importable),
        "rows_refused": len(a.rows) - len(a.importable),
        "rows_flagged": sum(1 for r in a.importable if r.warnings),
        "units": round(units, 2),
        "notes": a.notes,
    }
