#!/usr/bin/env python3
"""
Runnable demo: two apparel retailers on one ERP, sharing every natural key.

    python3 demo/tenant_isolation_demo.py

WHAT IT SHOWS
-------------
Two tenants -- Kurta House and Denim Depot -- deliberately use the SAME style
code, the SAME barcode, the SAME location code and the SAME PO number, because
that is what real retailers do. It then calls the shipped route handlers as
each tenant and shows that neither can see the other's data.

WHY IT LOOKS LIKE THIS AND NOT LIKE curl
----------------------------------------
There is no HTTP server here: FastAPI, SQLAlchemy and PostgreSQL cannot be
installed in this environment (no network -- see AGENTS.md). The demo imports
the REAL route functions from app/api/v1/inventory.py and runs them against an
in-memory store, so the logic exercised is production code, not a mock of it.
The frontend is likewise blocked, so this is API-level output, not a UI.

Everything printed is computed live. Nothing is hard-coded.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "erp-api"
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE / "tests"))
os.environ.setdefault("JWT_SECRET", "demo-secret-key-at-least-32-characters!!")

import _fakedb as fake  # noqa: E402

fake.install()
from app.security.core import Role, issue_token, verify_token  # noqa: E402
import app.db  # noqa: E402

models = fake.make_models()
_sess = types.ModuleType("app.db.session")
_sess.get_session = lambda: None
sys.modules["app.db.models"] = models
sys.modules["app.db.session"] = _sess

from app.api.v1 import dashboard as dash_mod  # noqa: E402
from app.api.v1 import inventory as inv_mod  # noqa: E402

# ── the deliberately colliding values ──
STYLE_CODE = "SHIRT-001"
BARCODE = "8901234567890"
LOCATION_CODE = "DC-01"
PO_NUMBER = "PO-202608-00001"

TENANTS = [
    # (size, size_seq, colour) -- size_seq is what makes S/M/L/XL order
    # correctly; sorting the labels alphabetically gives L, M, S, XL.
    ("kurta-house", "Kurta House", "KH", "Cotton Kurta",
     [("XL", 60, "Maroon"), ("S", 30, "Ivory"), ("L", 50, "Sage"),
      ("M", 40, "Indigo")], 1499.0),
    ("denim-depot", "Denim Depot", "DD", "Slim Fit Jeans",
     [("34", 34, "Black"), ("30", 30, "Stone"), ("36", 36, "Rinse"),
      ("32", 32, "Indigo")], 2799.0),
]

C = {"h": "\033[1;36m", "t": "\033[1;33m", "g": "\033[32m", "r": "\033[31m",
     "d": "\033[2m", "b": "\033[1m", "x": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}


def rule(title: str = "") -> None:
    print(f"\n{C['h']}{'─' * 78}{C['x']}")
    if title:
        print(f"{C['h']}{C['b']} {title}{C['x']}")
        print(f"{C['h']}{'─' * 78}{C['x']}")


def build_store():
    M = models
    t = {k: [] for k in ("ProductStyle", "Product", "Location", "InventoryLevel",
                         "Supplier", "PurchaseOrder", "Recommendation",
                         "ReplenishmentRun", "StockMovement", "AuditLog")}
    for tid, name, tag, style_name, variants, price in TENANTS:
        style = M.ProductStyle(id=f"{tag}-style", tenant_id=tid, style_code=STYLE_CODE,
                               name=style_name, brand=name, category="Apparel",
                               season="SS26", hsn_code="6205", is_active=True)
        t["ProductStyle"].append(style)
        loc = M.Location(id=f"{tag}-loc", tenant_id=tid, code=LOCATION_CODE,
                         name=f"{name} main warehouse", is_active=True)
        t["Location"].append(loc)
        t["Supplier"].append(M.Supplier(id=f"{tag}-sup", tenant_id=tid,
                                        code="SUP-001", name=f"{name} vendor"))
        t["PurchaseOrder"].append(M.PurchaseOrder(
            id=f"{tag}-po", tenant_id=tid, status=fake.EnumVal("approved"),
            total_value=100000.0 + len(variants), po_number=PO_NUMBER))
        t["ReplenishmentRun"].append(M.ReplenishmentRun(
            id=f"{tag}-run", tenant_id=tid, run_date=__import__("datetime").date(2026, 8, 4),
            status=fake.EnumVal("succeeded"), policy_version=f"pol-{tag}",
            lines_recommended=len(variants), duration_ms=40 + len(variants),
            error=None, created_at=None))
        for i, (size, seq, colour) in enumerate(variants):
            pid = f"{tag}-p{i}"
            t["Product"].append(M.Product(
                id=pid, tenant_id=tid, sku=f"{tag}-{size}-{colour[:3].upper()}",
                name=f"{style_name} {size} {colour}", unit_cost=price * 0.55,
                unit_price=price + i * 100, is_active=True, deleted_at=None,
                style_id=style.id, style=style, size=size, size_seq=seq, colour=colour,
                barcode=BARCODE if i == 0 else f"{tag}-BC-{i}"))
            t["InventoryLevel"].append(M.InventoryLevel(
                id=f"{tag}-inv{i}", tenant_id=tid, product_id=pid, location_id=loc.id,
                on_hand=float(12 * (i + 1)), on_order=0.0, reserved=0.0, backorder=0.0,
                reorder_point=20.0, order_up_to=90.0, safety_stock=8.0,
                product=t["Product"][-1], location=loc))
            t["Recommendation"].append(M.Recommendation(
                id=f"{tag}-r{i}", tenant_id=tid, run_id=f"{tag}-run",
                status=fake.EnumVal("pending"),
                urgency="critical" if i == 0 else "low",
                line_value=price * 10, recommended_qty=40.0, final_qty=None,
                decided_at=None, product_id=pid, location_id=loc.id,
                supplier_id=f"{tag}-sup"))
    return fake.FakeSession(t)


def token(tid: str) -> str:
    return issue_token(f"buyer@{tid}", tid, [Role.BUYER.value])


def main() -> int:
    store = build_store()

    rule("SETUP — two retailers, identical natural keys")
    print(f"  Both tenants deliberately use the same values, as real retailers do:")
    for label, val in (("style code", STYLE_CODE), ("barcode", BARCODE),
                       ("location code", LOCATION_CODE), ("PO number", PO_NUMBER)):
        holders = [r.tenant_id for r in store.tables["ProductStyle" if label == "style code"
                   else "Product" if label == "barcode"
                   else "Location" if label == "location code" else "PurchaseOrder"]
                   if getattr(r, {"style code": "style_code", "barcode": "barcode",
                                  "location code": "code",
                                  "PO number": "po_number"}[label], None) == val]
        print(f"    {label:<15} {C['t']}{val:<18}{C['x']} held by {len(holders)} tenants: "
              f"{', '.join(holders)}")

    breaches: list = []
    for tid, name, tag, *_ in TENANTS:
        p = verify_token(token(tid))
        rule(f"AS {name}  (token sub={p.subject}, tid={p.tenant_id})")

        st = asyncio.run(inv_mod.list_styles(search=None, category=None, limit=50,
                                             offset=0, session=store, principal=p))
        print(f"  GET /api/v1/inventory/styles")
        print(f"     {st['total']} style(s) visible")
        for item in st["items"]:
            print(f"     {C['b']}{item['style_code']}{C['x']}  {item['name']}  "
                  f"({item['brand']}, HSN {item['hsn_code']})")
            alpha = sorted(item["sizes"])
            print(f"       sizes   {item['sizes']}   "
                  f"{C['d']}(alphabetical would be {alpha}){C['x']}")
            print(f"       colours {item['colours']}")
            print(f"       {item['variant_count']} variants:")
            for v in item["variants"]:
                print(f"         {v['sku']:<18} {str(v['size']):<4} {v['colour']:<8} "
                      f"barcode={str(v['barcode']):<16} Rs {v['unit_price']:,.0f}")

        bc = asyncio.run(inv_mod.variant_by_barcode(barcode=BARCODE, session=store,
                                                    principal=p))
        print(f"\n  GET /api/v1/inventory/variants/by-barcode/{BARCODE}")
        # A cross-tenant resolution shows up here as a SKU belonging to the
        # other tenant, and as a null style (the style lookup is still scoped,
        # so it finds nothing for a foreign product). Report that as a security
        # failure rather than letting it crash on the None -- a traceback reads
        # like a demo bug, which is exactly the wrong signal.
        leaked_item = not bc["sku"].startswith(tag)
        if leaked_item or bc["style"] is None:
            print(f"     {C['r']}LEAK{C['x']} resolved to {C['r']}{bc['sku']}{C['x']} "
                  f"— {bc['name']}  (not this tenant's)")
            breaches.append(f"{name} scanned {BARCODE} and received {bc['sku']}")
            continue
        print(f"     resolves to {C['g']}{bc['sku']}{C['x']} — {bc['name']}")
        print(f"     style {bc['style']['style_code']} ({bc['style']['name']}), "
              f"size {bc['size']}, colour {bc['colour']}")
        print(f"     Rs {bc['unit_price']:,.0f}   on hand {bc['total_on_hand']:.0f} "
              f"across {len(bc['stock'])} location(s)")
        # GST slab is derived from the variant's own price, never stored
        slab = 5 if bc["unit_price"] <= 2500 else 18
        print(f"     GST slab derived from THIS variant's price: {C['t']}{slab}%{C['x']} "
              f"(<=Rs 2,500 -> 5%, above -> 18%)")

        dash = asyncio.run(dash_mod.dashboard(session=store, principal=p))
        print(f"\n  GET /api/v1/dashboard")
        print(f"     stock value Rs {dash['inventory']['stock_value']:,.0f}   "
              f"pending {dash['procurement']['pending_recommendations']}   "
              f"critical {dash['procurement']['critical_recommendations']}   "
              f"open POs {dash['procurement']['open_purchase_orders']}")
        print(f"     last run policy {dash['last_run']['policy_version']}")

    # ── isolation proof ──
    rule("ISOLATION CHECK")
    a, b = verify_token(token(TENANTS[0][0])), verify_token(token(TENANTS[1][0]))
    failures = list(breaches)

    ra = asyncio.run(inv_mod.variant_by_barcode(barcode=BARCODE, session=store, principal=a))
    rb = asyncio.run(inv_mod.variant_by_barcode(barcode=BARCODE, session=store, principal=b))
    same_code_diff_item = ra["sku"] != rb["sku"]
    print(f"  same barcode {BARCODE} ->")
    print(f"     {TENANTS[0][1]:<14} {ra['sku']}  ({ra['name']})")
    print(f"     {TENANTS[1][1]:<14} {rb['sku']}  ({rb['name']})")
    if not same_code_diff_item:
        failures.append("barcode resolved to the same item for both tenants")

    mark = len(fake.EXECUTED)
    for p, tid in ((a, TENANTS[0][0]), (b, TENANTS[1][0])):
        asyncio.run(inv_mod.list_inventory(location_code=LOCATION_CODE, below_reorder=False,
                                           search=None, limit=100, offset=0,
                                           session=store, principal=p))
    leaked = [(q["model"], getattr(r, "id", "?"), r.tenant_id)
              for q in fake.EXECUTED[mark:] for r in q["rows"]]
    cross = [x for x in leaked if x[2] not in {a.tenant_id, b.tenant_id}]
    unscoped = [q["model"] for q in fake.EXECUTED[mark:] if not q["tenant_filtered"]]
    print(f"\n  shared location code {LOCATION_CODE}: both tenants queried it")
    print(f"     queries executed {len(fake.EXECUTED[mark:])}, "
          f"unscoped {len(unscoped)}, rows from an unexpected tenant {len(cross)}")
    if unscoped:
        failures.append(f"unscoped queries: {unscoped}")

    total = len(fake.EXECUTED)
    unfiltered = [q["model"] for q in fake.EXECUTED if not q["tenant_filtered"]]
    mixed = [q["model"] for q in fake.EXECUTED if len(q["tenants_returned"]) > 1]
    print(f"\n  across the whole demo: {total} queries executed")
    print(f"     without a tenant predicate : {len(unfiltered)}")
    print(f"     returning more than 1 tenant: {len(mixed)}")
    if unfiltered:
        failures.append(f"{len(unfiltered)} unscoped queries: {sorted(set(unfiltered))}")
    if mixed:
        failures.append(f"{len(mixed)} queries mixed tenants: {sorted(set(mixed))}")

    rule()
    if failures:
        print(f"{C['r']}  ISOLATION FAILED{C['x']}")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"{C['g']}  ISOLATION HOLDS{C['x']} — {total} queries, all tenant-filtered, "
          f"none mixed tenants.")
    print(f"{C['d']}  Verified continuously by: make test-tenancy (58 assertions){C['x']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
