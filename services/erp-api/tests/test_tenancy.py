"""
Cross-tenant isolation for api/v1/inventory.py and api/v1/dashboard.py.

Executes the REAL route functions (not a reimplementation) against a two-tenant
in-memory dataset, and asserts no foreign-tenant row survives any query.

Instrumentation style follows tests/../test_no_mocks.py: rather than trusting
that the code looks right, every query the routes build is recorded and
inspected after the fact.

Before this change `GET /api/v1/inventory` returned every tenant's stock to any
caller, and all seven dashboard aggregates counted every tenant's rows.
"""
from __future__ import annotations

import asyncio
import ast
import datetime
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parent          # .../services/erp-api
APP = SERVICE / "app"
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(HERE))
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

import _fakedb as fake  # noqa: E402

fake.install()

# Import the REAL app.security first: it depends only on PyJWT, so it loads
# cleanly and we get genuine token verification rather than a stub.
from app.security.core import Principal, issue_token, verify_token  # noqa: E402
import app.db  # noqa: E402  (real, empty package -- establishes the parent)

# Only NOW swap in db stand-ins. app.db.models needs real SQLAlchemy, which is
# not installable here; app.db.session would open a real engine.
models = fake.make_models()
session_mod = types.ModuleType("app.db.session")
session_mod.get_session = lambda: None
sys.modules["app.db.models"] = models
sys.modules["app.db.session"] = session_mod

from app.api.v1 import dashboard as dash_mod  # noqa: E402
from app.api.v1 import inventory as inv_mod  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


TA, TB = "tenant-alpha", "tenant-beta"


# Both tenants deliberately reuse the SAME style code and the SAME barcode.
# That is normal retail: style codes are a retailer's own numbering and EAN
# ranges get reused across unrelated businesses. Global uniqueness on either
# would be wrong, and an unscoped lookup on either is a cross-tenant leak.
SHARED_STYLE_CODE = "SHIRT-001"
SHARED_BARCODE = "8901234567890"
# The same collision pattern for the other natural keys. Every retailer calls
# its main warehouse DC-01 and numbers POs from 00001; supplier codes and
# idempotency keys collide just as readily.
SHARED_LOCATION_CODE = "DC-01"
SHARED_SUPPLIER_CODE = "SUP-001"
SHARED_PO_NUMBER = "PO-202608-00001"
SHARED_IDEMPOTENCY_KEY = "adj-2026-08-04-0001"


def seed():
    M = models
    inv, prod, loc, reco, po, run, mv, styles, sup = [], [], [], [], [], [], [], [], []
    for tid, tag, n in ((TA, "A", 4), (TB, "B", 6)):
        # One style per tenant, sharing a style code across tenants.
        st = M.ProductStyle(
            id=f"{tag}-style", tenant_id=tid, style_code=SHARED_STYLE_CODE,
            name=f"{tag} Oxford Shirt", brand=f"{tag}-brand", category="Shirts",
            season="SS26", hsn_code="6205", is_active=True)
        styles.append(st)
        for i in range(n):
            pid, lid = f"{tag}-p{i}", f"{tag}-l{i}"
            # Variant i==0 of BOTH tenants carries the same barcode.
            prod.append(M.Product(
                id=pid, tenant_id=tid, sku=f"{tag}-SKU-{i}",
                name=f"{tag} product {i}", unit_cost=10.0, unit_price=1800.0 + i,
                is_active=True, deleted_at=None,
                style_id=f"{tag}-style", style=st,
                size=["S", "M", "L", "XL", "XXL", "3XL"][i],
                size_seq=[30, 40, 50, 60, 70, 80][i],
                colour=["Blue", "White", "Black", "Olive", "Grey", "Navy"][i],
                barcode=SHARED_BARCODE if i == 0 else f"{tag}-BC-{i}"))
            # Location 0 of BOTH tenants uses the same code.
            loc.append(M.Location(
                id=lid, tenant_id=tid,
                code=SHARED_LOCATION_CODE if i == 0 else f"{tag}-DC-{i}",
                name=f"{tag} DC {i}", is_active=True))
            p_obj, l_obj = prod[-1], loc[-1]
            inv.append(M.InventoryLevel(
                id=f"{tag}-inv{i}", tenant_id=tid, product_id=pid, location_id=lid,
                on_hand=0.0 if i == 0 else 100.0 + i, on_order=5.0, reserved=0.0,
                backorder=0.0, reorder_point=50.0, order_up_to=200.0, safety_stock=20.0,
                product=p_obj, location=l_obj))
            reco.append(M.Recommendation(
                id=f"{tag}-r{i}", tenant_id=tid, run_id=f"{tag}-run", status=fake.EnumVal("pending"),
                urgency="critical" if i == 0 else "low", line_value=1000.0 * (i + 1),
                recommended_qty=10.0, final_qty=None, decided_at=None,
                product_id=pid, location_id=lid, supplier_id=f"{tag}-s"))
            mv.append(M.StockMovement(
                id=f"{tag}-mv{i}", tenant_id=tid, product_id=pid, location_id=lid,
                quantity=1.0,
                idempotency_key=SHARED_IDEMPOTENCY_KEY if i == 0 else f"{tag}-idem-{i}"))
        po.append(M.PurchaseOrder(
            id=f"{tag}-po", tenant_id=tid, status=fake.EnumVal("approved"),
            total_value=5000.0, po_number=SHARED_PO_NUMBER))
        sup.append(M.Supplier(id=f"{tag}-sup", tenant_id=tid,
                              code=SHARED_SUPPLIER_CODE, name=f"{tag} Supplier"))
        run.append(M.ReplenishmentRun(
            id=f"{tag}-run", tenant_id=tid, run_date=datetime.date(2026, 8, 4),
            status=fake.EnumVal("succeeded"), policy_version=f"pol-{tag}", lines_recommended=n,
            duration_ms=42, error=None, created_at=datetime.date(2026, 8, 4)))
    return fake.FakeSession({
        "InventoryLevel": inv, "Product": prod, "Location": loc,
        "Recommendation": reco, "PurchaseOrder": po, "ReplenishmentRun": run,
        "StockMovement": mv, "AuditLog": [], "ProductStyle": styles,
        "Supplier": sup,
    })


def principal(tid: str, sub: str = "alice@erp") -> Principal:
    return verify_token(issue_token(sub, tid, ["buyer"]))


def foreign_rows(since: int) -> list:
    """Every row returned by queries executed since `since`, not owned by tenant A."""
    bad = []
    for q in fake.EXECUTED[since:]:
        for r in q["rows"]:
            if getattr(r, "tenant_id", None) != TA:
                bad.append((q["model"], getattr(r, "id", "?"), getattr(r, "tenant_id", "?")))
    return bad


# ───────────────────────── tests ─────────────────────────

def test_inventory_list_is_scoped():
    s = seed()
    mark = len(fake.EXECUTED)
    res = asyncio.run(inv_mod.list_inventory(
        location_code=None, below_reorder=False, search=None,
        limit=200, offset=0, session=s, principal=principal(TA)))
    qs = fake.EXECUTED[mark:]
    check("list_inventory executed at least one query", len(qs) >= 1, f"{len(qs)} queries")
    check("every list_inventory query carries a tenant predicate",
          all(q["tenant_filtered"] for q in qs),
          str([q["predicates"] for q in qs if not q["tenant_filtered"]]))
    check("no foreign-tenant row survived any list_inventory query",
          not foreign_rows(mark), str(foreign_rows(mark)[:3]))
    skus = [i["sku"] for i in res["items"]]
    check("response contains only tenant-A SKUs", all(x.startswith("A-") for x in skus), str(skus))
    check("response is non-empty (test would be vacuous otherwise)", len(skus) == 4, str(len(skus)))
    check("total count reflects only tenant A", res["total"] == 4, str(res["total"]))


def test_inventory_list_filters_still_scoped():
    """A filter must NARROW within the tenant, never widen across tenants."""
    for kw, label in (({"location_code": "B-DC"}, "another tenant's location code"),
                      ({"search": "B-SKU"}, "another tenant's SKU search"),
                      ({"below_reorder": True}, "below-reorder filter")):
        s = seed()
        mark = len(fake.EXECUTED)
        res = asyncio.run(inv_mod.list_inventory(
            location_code=kw.get("location_code"), below_reorder=kw.get("below_reorder", False),
            search=kw.get("search"), limit=200, offset=0, session=s, principal=principal(TA)))
        skus = [i["sku"] for i in res["items"]]
        check(f"{label} returns zero tenant-B rows",
              all(x.startswith("A-") for x in skus) and not foreign_rows(mark), str(skus))


def test_adjustment_cannot_touch_another_tenant():
    s = seed()
    mark = len(fake.EXECUTED)
    body = types.SimpleNamespace(product_id="B-p1", location_id="B-l1",
                                 quantity=5, reason="stock count", idempotency_key=None)
    raised = False
    try:
        asyncio.run(inv_mod.adjust(body=body, session=s, principal=principal(TA)))
    except Exception as exc:
        raised = "404" in str(exc)
    check("adjusting another tenant's inventory raises 404", raised)
    check("no foreign row was read during the attempt", not foreign_rows(mark),
          str(foreign_rows(mark)[:3]))
    check("nothing was written", len(s.added) == 0, str(len(s.added)))


def test_adjustment_own_tenant_stamps_rows():
    s = seed()
    body = types.SimpleNamespace(product_id="A-p1", location_id="A-l1",
                                 quantity=5, reason="stock count", idempotency_key="k1")
    out = asyncio.run(inv_mod.adjust(body=body, session=s, principal=principal(TA, "bob@erp")))
    check("own-tenant adjustment succeeds", out.get("status") == "ok", str(out))
    check("two rows written (movement + audit)", len(s.added) == 2, str(len(s.added)))
    check("every written row is stamped with the caller's tenant",
          all(getattr(r, "tenant_id", None) == TA for r in s.added),
          str([getattr(r, "tenant_id", None) for r in s.added]))
    audit = [r for r in s.added if getattr(r, "entity_type", None) == "inventory_level"]
    check("audit actor comes from the token, not the request body",
          audit and audit[0].actor == "bob@erp", str(audit[0].actor if audit else None))


def test_idempotency_key_is_tenant_scoped():
    """Tenant B reusing tenant A's key must NOT be silently deduplicated."""
    s = seed()
    body = types.SimpleNamespace(product_id="B-p1", location_id="B-l1", quantity=5,
                                 reason="count", idempotency_key="A-idem-1")
    mark = len(fake.EXECUTED)
    out = None
    try:
        out = asyncio.run(inv_mod.adjust(body=body, session=s, principal=principal(TB)))
    except Exception:
        out = {"status": "404"}
    dup = out.get("status") == "duplicate_ignored"
    check("tenant B is not deduplicated against tenant A's idempotency key", not dup, str(out))
    check("the idempotency lookup itself was tenant-filtered",
          all(q["tenant_filtered"] for q in fake.EXECUTED[mark:] if q["model"] == "StockMovement"))


def test_dashboard_all_seven_aggregates_scoped():
    s = seed()
    mark = len(fake.EXECUTED)
    res = asyncio.run(dash_mod.dashboard(session=s, principal=principal(TA)))
    qs = fake.EXECUTED[mark:]
    check("dashboard executed seven queries", len(qs) == 7, f"{len(qs)}")
    unscoped = [q for q in qs if not q["tenant_filtered"]]
    check("all seven dashboard queries carry a tenant predicate",
          not unscoped, str([q["model"] for q in unscoped]))
    check("no foreign-tenant row contributed to any aggregate",
          not foreign_rows(mark), str(foreign_rows(mark)[:3]))
    check("every query returned rows from at most one tenant",
          all(len(q["tenants_returned"]) <= 1 for q in qs),
          str([q["tenants_returned"] for q in qs]))
    check("out-of-stock count is tenant A's 1, not the combined 2",
          res["inventory"]["skus_out_of_stock"] == 1,
          str(res["inventory"]["skus_out_of_stock"]))
    check("pending recommendations is tenant A's 4, not the combined 10",
          res["procurement"]["pending_recommendations"] == 4,
          str(res["procurement"]["pending_recommendations"]))
    check("critical count is tenant A's 1, not the combined 2",
          res["procurement"]["critical_recommendations"] == 1,
          str(res["procurement"]["critical_recommendations"]))
    check("open PO count is tenant A's 1, not the combined 2",
          res["procurement"]["open_purchase_orders"] == 1,
          str(res["procurement"]["open_purchase_orders"]))
    lr = res["last_run"]
    check("last_run belongs to tenant A", lr and lr["policy_version"] == "pol-A",
          str(lr and lr["policy_version"]))


def test_dashboard_from_the_other_side():
    """Run as tenant B and confirm the numbers differ — proves the filter binds
    to the token rather than returning a constant."""
    s = seed()
    a = asyncio.run(dash_mod.dashboard(session=s, principal=principal(TA)))
    b = asyncio.run(dash_mod.dashboard(session=s, principal=principal(TB)))
    check("tenant B sees its own 6 pending, not tenant A's 4",
          b["procurement"]["pending_recommendations"] == 6,
          str(b["procurement"]["pending_recommendations"]))
    check("the two tenants see different dashboards",
          a["procurement"]["pending_recommendations"] != b["procurement"]["pending_recommendations"])
    check("tenant B's last_run is its own",
          b["last_run"]["policy_version"] == "pol-B",
          str(b["last_run"]["policy_version"]))


def test_styles_are_tenant_scoped_despite_shared_style_code():
    s = seed()
    mark = len(fake.EXECUTED)
    res = asyncio.run(inv_mod.list_styles(
        search=None, category=None, limit=100, offset=0,
        session=s, principal=principal(TA)))
    qs = fake.EXECUTED[mark:]
    check("list_styles executed queries", len(qs) >= 2, f"{len(qs)}")
    check("every list_styles query carries a tenant predicate",
          all(q["tenant_filtered"] for q in qs),
          str([q["predicates"] for q in qs if not q["tenant_filtered"]]))
    check("no foreign-tenant row survived list_styles", not foreign_rows(mark),
          str(foreign_rows(mark)[:3]))
    check("both tenants share the style code (test would be vacuous otherwise)",
          len([r for r in s.tables["ProductStyle"] if r.style_code == SHARED_STYLE_CODE]) == 2)
    check("tenant A sees exactly one style, not both tenants'",
          res["total"] == 1 and len(res["items"]) == 1, str(res["total"]))
    item = res["items"][0]
    check("the style returned is tenant A's", item["name"].startswith("A "), item["name"])
    check("variant count is tenant A's 4, not the combined 10",
          item["variant_count"] == 4, str(item["variant_count"]))
    check("no tenant-B SKU leaked into the variant grid",
          all(v["sku"].startswith("A-") for v in item["variants"]),
          str([v["sku"] for v in item["variants"]]))
    check("size axis is in WEARING order, not alphabetical",
          item["sizes"] == ["S", "M", "L", "XL"],
          f"{item['sizes']} (alphabetical would be {sorted(item['sizes'])})")
    check("colours present", len(item["colours"]) == 4, str(item["colours"]))
    check("variants are returned in size order",
          [v["size"] for v in item["variants"]] == ["S", "M", "L", "XL"],
          str([v["size"] for v in item["variants"]]))


def test_style_search_cannot_reach_the_other_tenant():
    s = seed()
    mark = len(fake.EXECUTED)
    res = asyncio.run(inv_mod.list_styles(
        search="B Oxford", category=None, limit=100, offset=0,
        session=s, principal=principal(TA)))
    check("searching for tenant B's style name returns nothing",
          res["total"] == 0 and not foreign_rows(mark),
          f"total={res['total']} leaked={foreign_rows(mark)[:2]}")


def test_barcode_collision_resolves_within_the_caller_tenant():
    """The realistic collision: both tenants scan the same barcode."""
    s = seed()
    mark = len(fake.EXECUTED)
    a = asyncio.run(inv_mod.variant_by_barcode(
        barcode=SHARED_BARCODE, session=s, principal=principal(TA)))
    check("tenant A resolves the shared barcode to its OWN variant",
          a["sku"] == "A-SKU-0", a["sku"])
    check("no foreign row was read resolving it", not foreign_rows(mark),
          str(foreign_rows(mark)[:3]))
    check("every barcode-lookup query is tenant-filtered",
          all(q["tenant_filtered"] for q in fake.EXECUTED[mark:]),
          str([q["model"] for q in fake.EXECUTED[mark:] if not q["tenant_filtered"]]))
    check("the joined style belongs to tenant A", a["style"]["name"].startswith("A "),
          a["style"]["name"])
    # A-SKU-0 is the deliberately out-of-stock row (seeded on_hand=0 so the
    # dashboard's out-of-stock count is a meaningful 1), so assert on which
    # rows came back rather than on the total.
    check("exactly one stock row returned, tenant A's own location",
          len(a["stock"]) == 1, str(a["stock"]))
    check("the stock row is the caller's, not tenant B's",
          all(not r["location_id"].startswith("B-") for r in a["stock"]),
          str([r["location_id"] for r in a["stock"]]))

    mark2 = len(fake.EXECUTED)
    b = asyncio.run(inv_mod.variant_by_barcode(
        barcode=SHARED_BARCODE, session=s, principal=principal(TB)))
    check("tenant B resolves the SAME barcode to a DIFFERENT variant",
          b["sku"] == "B-SKU-0" and b["sku"] != a["sku"], f"{a['sku']} vs {b['sku']}")
    check("tenant B's lookup leaked nothing either",
          all(getattr(r, "tenant_id", None) == TB
              for q in fake.EXECUTED[mark2:] for r in q["rows"]))


def test_barcode_of_another_tenant_only_is_a_404():
    s = seed()
    mark = len(fake.EXECUTED)
    raised = False
    try:
        asyncio.run(inv_mod.variant_by_barcode(
            barcode="B-BC-3", session=s, principal=principal(TA)))
    except Exception as exc:
        raised = "404" in str(exc)
    check("scanning a barcode that exists only in tenant B returns 404", raised)
    check("and reads no tenant-B row while doing so", not foreign_rows(mark),
          str(foreign_rows(mark)[:3]))


def test_variant_axis_is_distinguishable():
    """Stock-by-variant depends on size and colour reaching the response."""
    s = seed()
    res = asyncio.run(inv_mod.list_inventory(
        location_code=None, below_reorder=False, search=None,
        limit=200, offset=0, session=s, principal=principal(TA)))
    check("inventory rows still resolve after the schema change",
          len(res["items"]) == 4, str(len(res["items"])))
    a = asyncio.run(inv_mod.variant_by_barcode(
        barcode="A-BC-2", session=s, principal=principal(TA)))
    check("a variant carries its size", a["size"] == "L", str(a["size"]))
    check("a variant carries its colour", a["colour"] == "Black", str(a["colour"]))
    check("a variant carries its own price (GST slab is derived per variant)",
          a["unit_price"] == 1802.0, str(a["unit_price"]))


def test_shared_natural_keys_do_not_collide_across_tenants():
    """The uniqueness defect class, from the data side.

    Every one of these values is deliberately identical across both tenants.
    If any constraint were still globally unique the second tenant could not
    exist at all; if any query were unscoped the wrong tenant's row comes back.
    """
    s = seed()
    for label, table, attr, shared in (
        ("location code", "Location", "code", SHARED_LOCATION_CODE),
        ("supplier code", "Supplier", "code", SHARED_SUPPLIER_CODE),
        ("PO number", "PurchaseOrder", "po_number", SHARED_PO_NUMBER),
        ("idempotency key", "StockMovement", "idempotency_key", SHARED_IDEMPOTENCY_KEY),
        ("style code", "ProductStyle", "style_code", SHARED_STYLE_CODE),
        ("barcode", "Product", "barcode", SHARED_BARCODE),
    ):
        rows = [r for r in s.tables[table] if getattr(r, attr, None) == shared]
        tenants = {getattr(r, "tenant_id", None) for r in rows}
        check(f"both tenants hold the same {label} ({shared})",
              len(rows) == 2 and tenants == {TA, TB},
              f"{len(rows)} rows, tenants={sorted(tenants)}")


def test_scoped_lookup_on_a_shared_location_code():
    """DC-01 exists in both tenants; a filter on it must stay inside one."""
    for tenant, prefix in ((TA, "A-"), (TB, "B-")):
        s = seed()
        mark = len(fake.EXECUTED)
        res = asyncio.run(inv_mod.list_inventory(
            location_code=SHARED_LOCATION_CODE, below_reorder=False, search=None,
            limit=200, offset=0, session=s, principal=principal(tenant)))
        skus = [i["sku"] for i in res["items"]]
        leaked = [r for q in fake.EXECUTED[mark:] for r in q["rows"]
                  if getattr(r, "tenant_id", None) != tenant]
        check(f"{tenant} filtering on the shared location code sees only its own",
              skus and all(x.startswith(prefix) for x in skus) and not leaked,
              f"{skus} leaked={len(leaked)}")


def test_route_scoping_audit_reports_no_defects():
    """Runs scripts/audit_route_scoping.py so the unscoped-query class stays closed."""
    import subprocess
    root = SERVICE.parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "audit_route_scoping.py")],
                       capture_output=True, text=True)
    check("scripts/audit_route_scoping.py reports zero route-scoping defects",
          r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[:80])


def test_uniqueness_audit_reports_no_defects():
    """Runs scripts/audit_uniqueness.py so the defect class stays closed."""
    import subprocess
    root = SERVICE.parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "audit_uniqueness.py")],
                       capture_output=True, text=True)
    check("scripts/audit_uniqueness.py reports zero global-uniqueness defects",
          r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[:80])


def test_static_no_query_bypasses_scope_query():
    """AST sweep: every session.execute in both files must wrap its select.

    Runtime coverage proves the paths exercised by these tests; this proves
    there is no unexercised query hiding in a branch.
    """
    for fname in ("inventory.py", "dashboard.py"):
        path = APP / "api" / "v1" / fname
        tree = ast.parse(path.read_text())
        # Names assigned from an expression containing scope_query(...) are
        # themselves scoped, and so is anything derived from them (e.g.
        # `select(count()).select_from(stmt.subquery())`). Track them rather
        # than demanding the literal call at every execute site.
        scoped_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and "scope_query(" in ast.unparse(node.value):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        scoped_names.add(tgt.id)
        # second pass: x = <expr referencing a scoped name>
        for _ in range(3):
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    src_v = ast.unparse(node.value)
                    if any(n in src_v for n in scoped_names):
                        for tgt in node.targets:
                            if isinstance(tgt, ast.Name):
                                scoped_names.add(tgt.id)
        bad = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute"):
                continue
            if not node.args:
                continue
            src = ast.unparse(node.args[0])
            if "scope_query(" in src or any(n in src for n in scoped_names):
                continue
            bad.append(src[:70])
        check(f"{fname}: every session.execute() is wrapped in scope_query",
              not bad, "; ".join(bad))

        routes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decs = [ast.unparse(d) for d in node.decorator_list]
                if any("router." in d for d in decs):
                    args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
                    if "principal" not in args:
                        routes.append(node.name)
        check(f"{fname}: every route takes a principal", not routes, str(routes))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCross-tenant isolation: inventory + dashboard ({len(tests)} groups)")
    print("=" * 66)
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "=" * 66)
    print(f"queries executed and inspected: {len(fake.EXECUTED)}")
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
