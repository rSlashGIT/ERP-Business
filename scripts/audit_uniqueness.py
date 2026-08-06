#!/usr/bin/env python3
"""
Audit: every UniqueConstraint on a tenant-scoped table.

THE DEFECT CLASS
----------------
`UniqueConstraint("sku")` on a tenant-scoped table means two tenants cannot
both stock SHIRT-M-BLU. Same shape as an unscoped query: tenant identity
omitted from something that needs it. Found on products.sku, then on four more
tables, including replenishment_runs(run_date, triggered_by) -- which capped
the entire platform at ONE tenant having a nightly run per day.

THE RULE (see db/models.py:TenantMixin)
---------------------------------------
Include tenant_id IF AND ONLY IF the columns are a NATURAL key: a value the
tenant chooses, which another tenant may legitimately choose too.

Do NOT include it for SURROGATE keys -- UUID foreign keys to rows that are
themselves tenant-scoped. Two tenants cannot share a product_id, so
(product_id, location_id) is already per-tenant; adding tenant_id there is
redundant noise that obscures the real constraint.

Run: python3 scripts/audit_uniqueness.py     (exit 1 on any defect)
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

MODELS = Path(__file__).resolve().parents[1] / "services/erp-api/app/db/models.py"
STR = re.compile(r"""["']([^"']+)["']""")

# A constraint is implicitly tenant-scoped if it contains AT LEAST ONE foreign
# key to a tenant-scoped table. One such column is sufficient: tenant A's
# product_id can never equal tenant B's, so (product_id, location_id,
# bucket_date) is already unique per tenant even though two of its three
# columns are plain values.
#
# Requiring EVERY column to be an FK was the first attempt and it was wrong --
# it flagged (style_id, size, colour) and (purchase_order_id, line_no) as
# defects when both are already scoped by their FK.


def analyse() -> Tuple[List[str], List[str], List[str]]:
    tree = ast.parse(MODELS.read_text())
    defects: List[str] = []
    exempt: List[str] = []
    ok: List[str] = []

    # {table: {column: referenced_table}} for every foreign key
    fk_targets: Dict[str, Dict[str, str]] = {}
    tenant_tables: Set[str] = set()
    names: Dict[str, str] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [ast.unparse(b) for b in node.bases]
        if "Base" not in bases:
            continue
        tname = None
        fks: Dict[str, str] = {}
        for st in node.body:
            if isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__":
                tname = st.value.value  # type: ignore[attr-defined]
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                src = ast.unparse(st.value)
                m = re.search(r"""ForeignKey\(\s*["']([^"'.]+)\.""", src)
                if m:
                    fks[getattr(st.target, "id", "")] = m.group(1)
        if tname:
            names[node.name] = tname
            fk_targets[tname] = fks
            if "TenantMixin" in bases:
                tenant_tables.add(tname)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [ast.unparse(b) for b in node.bases]
        if "Base" not in bases:
            continue
        tname = names.get(node.name)
        if not tname or tname not in tenant_tables:
            continue
        for st in node.body:
            if not (isinstance(st, ast.Assign)
                    and getattr(st.targets[0], "id", "") == "__table_args__"):
                continue
            for el in getattr(st.value, "elts", []):
                src = ast.unparse(el)
                if not src.startswith("UniqueConstraint"):
                    continue
                toks = STR.findall(src)
                cname = next((t for t in toks if t.startswith("uq_")), "?")
                cols = [t for t in toks if t != cname]
                label = f"{tname}.{cname} ({', '.join(cols)})"
                if "tenant_id" in cols:
                    ok.append(label)
                    continue
                # Surrogate exemption: at least one column is an FK pointing at
                # a tenant-scoped table.
                scoped_fk = next(
                    (c for c in cols
                     if fk_targets.get(tname, {}).get(c) in tenant_tables),
                    None,
                )
                if scoped_fk:
                    exempt.append(f"{label}  [via {scoped_fk}]")
                    continue
                defects.append(label)
    return defects, exempt, ok


def main() -> int:
    defects, exempt, ok = analyse()
    print("=" * 72)
    print(" Uniqueness audit — tenant-scoped tables")
    print("=" * 72)
    print(f"\n  PER-TENANT ({len(ok)}) — natural keys correctly scoped")
    for x in ok:
        print(f"    OK      {x}")
    print(f"\n  EXEMPT ({len(exempt)}) — surrogate keys, already per-tenant via UUID FKs")
    for x in exempt:
        print(f"    exempt  {x}")
    print(f"\n  DEFECTS ({len(defects)}) — natural keys missing tenant_id")
    for x in defects:
        print(f"    DEFECT  {x}")
    print("\n" + "=" * 72)
    if defects:
        print(f" {len(defects)} GLOBAL-UNIQUENESS DEFECT(S)")
        return 1
    print(" NO GLOBAL-UNIQUENESS DEFECTS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
