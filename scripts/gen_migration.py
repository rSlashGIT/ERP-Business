#!/usr/bin/env python3
"""
Generate the initial Alembic migration by AST-parsing db/models.py.

WHY NOT `alembic revision --autogenerate`?
------------------------------------------
Autogenerate needs a live database to diff against and an importable
SQLAlchemy metadata object. This build environment has neither installed
(see docs/HANDOFF.md section 13). Parsing the declarative models with `ast`
is deterministic, needs no dependencies, and produces exactly the same
op sequence -- and it doubles as the verifier: scripts/verify_migration.py
re-parses both files and asserts nothing was dropped.

When you DO have alembic and a database, `alembic revision --autogenerate`
against these same models should produce an empty diff. That is the check
to run first on a real machine.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MODELS = Path(__file__).resolve().parents[1] / "services/erp-api/app/db/models.py"

# SQLAlchemy type -> alembic sa.* rendering
TYPE_MAP = {
    "String": "sa.String(length={0})",
    "Text": "sa.Text()",
    "Integer": "sa.Integer()",
    "BigInteger": "sa.BigInteger()",
    "Boolean": "sa.Boolean()",
    "Date": "sa.Date()",
    "DateTime": "sa.DateTime(timezone=True)",
    "Numeric": "sa.Numeric(precision={0}, scale={1})",
    "JSONB": "postgresql.JSONB(astext_type=sa.Text())",
    "UUID": "postgresql.UUID(as_uuid=True)",
}


# ast.unparse emits single-quoted strings; accept both quote styles everywhere.
STR = re.compile(r"""["']([^"']+)["']""")
NAMED = re.compile(r"""name=(?:"([^"]+)"|'([^']+)')""")
CHECK = re.compile(r"""CheckConstraint\(\s*(?:"([^"]+)"|'([^']+)')""")
FK = re.compile(r"""ForeignKey\(\s*["']([^"']+)["']""")
ONDEL = re.compile(r"""ondelete=(?:"([^"]+)"|'([^']+)')""")
SDEF = re.compile(r"""server_default=text\(\s*["']([^"']+)["']\s*\)""")
ENUMPAT = re.compile(r"""Enum\((\w+),\s*name=["'](\w+)["']""")


def _txt(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<expr>"


class Table:
    def __init__(self, cls_name: str) -> None:
        self.cls_name = cls_name
        self.mixins: List[str] = []
        self.name: Optional[str] = None
        self.columns: List[Dict[str, Any]] = []
        self.uniques: List[Tuple[List[str], str]] = []
        self.indexes: List[Tuple[str, List[str]]] = []
        self.checks: List[Tuple[str, str]] = []
        self.has_timestamps = False


def parse_mixins(tree: ast.Module) -> Dict[str, List[Dict[str, Any]]]:
    """Registry of {mixin_name: [column, ...]}.

    A mixin is any class that declares mapped_column attributes but has no
    __tablename__ -- i.e. it contributes columns to models rather than being
    one. Parsed with the SAME _parse_column used for model bodies, so mixin
    columns get identical type, nullability, default and index handling.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(getattr(t.targets[0], "id", "") == "__tablename__"
               for t in node.body if isinstance(t, ast.Assign) and t.targets):
            continue
        cols: List[Dict[str, Any]] = []
        for st in node.body:
            if isinstance(st, ast.AnnAssign) and st.value is not None:
                src = _txt(st.value)
                if "mapped_column" in src or "_uuid_pk" in src:
                    c = _parse_column(st, src)
                    if c:
                        cols.append(c)
        if cols:
            out[node.name] = cols
    return out


def parse_models(path: Path) -> Tuple[List[Table], List[Tuple[str, List[str]]], Dict[str, List[Dict[str, Any]]]]:
    tree = ast.parse(path.read_text())
    mixins = parse_mixins(tree)
    enums: List[Tuple[str, List[str]]] = []
    enum_members: Dict[str, List[str]] = {}
    tables: List[Table] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [_txt(b) for b in node.bases]

        # Enum classes -> Postgres enum types
        if any("Enum" in b for b in bases) and "str" in bases:
            vals = []
            for st in node.body:
                if isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant):
                    vals.append(str(st.value.value))
            if vals:
                enum_members[node.name] = vals
            continue

        if not any(b in ("Base", "Base, TimestampMixin") or "Base" in b for b in bases):
            continue

        t = Table(node.name)
        # Columns declared on a MIXIN live outside this class body, so the walk
        # below cannot see them. Record which mixins apply and splice their
        # columns in at render time from the registry built above.
        #
        # This was a real defect, not a hypothetical: tenant_id is declared on
        # TenantMixin and was silently absent from EVERY generated migration
        # until a UNIQUE(tenant_id, style_code) constraint made SQLite reject
        # the unknown identifier. It was invisible because the coverage
        # cross-check omitted mixin columns on both sides. Handling mixins
        # generically means the next one added cannot repeat it.
        t.mixins = [b for b in bases if b in mixins]

        for st in node.body:
            # __tablename__
            if isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__":
                t.name = st.value.value  # type: ignore[attr-defined]
            # __table_args__
            elif isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__table_args__":
                for el in getattr(st.value, "elts", []):
                    src = _txt(el)
                    # ast.unparse normalises literals to single quotes, so every
                    # pattern here must accept either quote style.
                    if src.startswith("UniqueConstraint"):
                        cols = STR.findall(src)
                        nm = NAMED.search(src)
                        if nm:
                            key = nm.group(1) or nm.group(2)
                            t.uniques.append(([c for c in cols if c != key], key))
                    elif src.startswith("Index"):
                        parts = STR.findall(src)
                        if parts:
                            t.indexes.append((parts[0], parts[1:]))
                    elif src.startswith("CheckConstraint"):
                        cond = CHECK.search(src)
                        nm = NAMED.search(src)
                        if cond and nm:
                            t.checks.append((nm.group(1) or nm.group(2),
                                             (cond.group(1) or cond.group(2)).replace('"', "'")))
            # mapped_column columns
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                src = _txt(st.value)
                if "mapped_column" not in src and "_uuid_pk" not in src:
                    continue
                col = _parse_column(st, src)
                if col:
                    t.columns.append(col)

        if t.name:
            tables.append(t)

    # Only emit enums actually referenced by a column
    used: Dict[str, str] = {}
    for t in tables:
        for c in t.columns:
            if c["kind"] == "enum":
                used[c["enum_py"]] = c["enum_name"]
    for py_name, pg_name in used.items():
        enums.append((pg_name, enum_members.get(py_name, [])))
    return tables, enums, mixins


def _parse_column(st: ast.AnnAssign, src: str) -> Optional[Dict[str, Any]]:
    name = getattr(st.target, "id", None)
    if not name:
        return None
    col: Dict[str, Any] = {
        "name": name, "kind": "plain", "type": "sa.String()",
        "nullable": True, "pk": False, "fk": None,
        "server_default": None, "autoincrement": False,
        "index": "index=True" in src,
    }
    ann = _txt(st.annotation)
    col["nullable"] = "Optional[" in ann

    if "_uuid_pk()" in src:
        col.update(kind="uuid", type="postgresql.UUID(as_uuid=True)", nullable=False, pk=True)
        return col

    if "primary_key=True" in src:
        col["pk"] = True
        col["nullable"] = False
    if "nullable=False" in src:
        col["nullable"] = False
    if "autoincrement=True" in src:
        col["autoincrement"] = True

    m = FK.search(src)
    if m:
        col["fk"] = m.group(1)
        m2 = ONDEL.search(src)
        col["fk_ondelete"] = (m2.group(1) or m2.group(2)) if m2 else None

    sd = SDEF.search(src)
    if sd:
        col["server_default"] = f'sa.text("{sd.group(1)}")'
    elif "server_default=func.now()" in src:
        col["server_default"] = "sa.text('now()')"

    m = ENUMPAT.search(src)
    if m:
        col.update(kind="enum", enum_py=m.group(1), enum_name=m.group(2),
                   type=f'postgresql.ENUM(name="{m.group(2)}", create_type=False)')
        return col

    m = re.search(r"Numeric\((\d+),\s*(\d+)\)", src)
    if m:
        col["type"] = TYPE_MAP["Numeric"].format(m.group(1), m.group(2)); return col
    m = re.search(r"String\((\d+)\)", src)
    if m:
        col["type"] = TYPE_MAP["String"].format(m.group(1)); return col
    for key in ("JSONB", "BigInteger", "DateTime", "Boolean", "Integer", "Text", "Date"):
        if key in src:
            col["type"] = TYPE_MAP[key]; return col
    if "UUID(as_uuid=True)" in src or (col["fk"] and "uuid.UUID" in ann):
        # A bare mapped_column(ForeignKey(...)) carries no type; take it from
        # the Mapped[Optional[uuid.UUID]] annotation instead of defaulting to
        # String, which would break the FK on Postgres.
        col["type"] = TYPE_MAP["UUID"]
    return col


# Creation order respecting foreign keys.
ORDER = [
    # product_styles precedes products: products.style_id references it.
    "product_styles", "products", "locations", "customers", "suppliers", "supplier_products",
    "replenishment_runs", "purchase_orders", "purchase_order_lines",
    "inventory_levels", "stock_movements", "demand_history",
    "goods_receipts", "lead_time_observations", "recommendations",
    "sales_invoices", "sales_invoice_lines", "payments", "payment_allocations",
    "policy_parameters", "audit_log",
]


def render(tables: List[Table], enums: List[Tuple[str, List[str]]],
           mixins: Dict[str, List[Dict[str, Any]]]) -> str:
    by_name = {t.name: t for t in tables}
    ordered = [by_name[n] for n in ORDER if n in by_name]
    ordered += [t for t in tables if t.name not in ORDER]

    L: List[str] = []
    a = L.append
    a('"""initial schema')
    a("")
    a("Generated from services/erp-api/app/db/models.py by scripts/gen_migration.py.")
    a("Covers every table, column, enum type, index, unique and check constraint.")
    a("")
    a("Revision ID: 0001_initial")
    a("Revises:")
    a('"""')
    a("from alembic import op")
    a("import sqlalchemy as sa")
    a("from sqlalchemy.dialects import postgresql")
    a("")
    a('revision = "0001_initial"')
    a("down_revision = None")
    a("branch_labels = None")
    a("depends_on = None")
    a("")
    a("# Postgres enum types. Created explicitly so downgrade can drop them --")
    a("# SQLAlchemy will not clean these up on its own and a re-run then fails")
    a('# with "type already exists", which is the classic Alembic footgun.')
    a("ENUMS = {")
    for pg, vals in enums:
        a(f'    "{pg}": {vals!r},')
    a("}")
    a("")
    a("")
    a("def upgrade() -> None:")
    a("    bind = op.get_bind()")
    a('    is_pg = bind.dialect.name == "postgresql"')
    a("    if is_pg:")
    a("        for name, values in ENUMS.items():")
    a("            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)")
    a("")

    for t in ordered:
        a(f'    op.create_table(')
        a(f'        "{t.name}",')
        cols = list(t.columns)
        for mx in t.mixins:                      # generic: any registered mixin
            cols.extend(mixins.get(mx, []))
        for c in cols:
            bits = [f'"{c["name"]}"', c["type"]]
            if c.get("fk"):
                od = c.get("fk_ondelete")
                fk = f'sa.ForeignKey("{c["fk"]}"'
                if od:
                    fk += f', ondelete="{od}"'
                fk += ")"
                bits.append(fk)
            bits.append(f'nullable={c["nullable"]}')
            if c["pk"]:
                bits.append("primary_key=True")
            if c.get("autoincrement"):
                bits.append("autoincrement=True")
            if c.get("server_default"):
                bits.append(f'server_default={c["server_default"]}')
            a(f'        sa.Column({", ".join(bits)}),')
        for cols_u, nm in t.uniques:
            quoted = ", ".join(f'"{c}"' for c in cols_u)
            a(f'        sa.UniqueConstraint({quoted}, name="{nm}"),')
        for nm, cond in t.checks:
            a(f'        sa.CheckConstraint("{cond}", name="{nm}"),')
        a("    )")
        for mx in t.mixins:                      # index=True on a mixin column
            for mc in mixins.get(mx, []):
                if mc.get("index"):
                    a(f'    op.create_index("ix_{t.name}_{mc["name"]}", "{t.name}", ["{mc["name"]}"])')
        for idx_name, idx_cols in t.indexes:
            quoted = ", ".join(f'"{c}"' for c in idx_cols)
            a(f'    op.create_index("{idx_name}", "{t.name}", [{quoted}])')
        a("")

    a("")
    a("def downgrade() -> None:")
    a("    bind = op.get_bind()")
    for t in reversed(ordered):
        for idx_name, _ in t.indexes:
            a(f'    op.drop_index("{idx_name}", table_name="{t.name}")')
        for mx in t.mixins:
            for mc in mixins.get(mx, []):
                if mc.get("index"):
                    a(f'    op.drop_index("ix_{t.name}_{mc["name"]}", table_name="{t.name}")')
        a(f'    op.drop_table("{t.name}")')
    a('    if bind.dialect.name == "postgresql":')
    a("        for name in ENUMS:")
    a("            postgresql.ENUM(name=name).drop(bind, checkfirst=True)")
    a("")
    return "\n".join(L)


def main() -> int:
    tables, enums, mixins = parse_models(MODELS)
    out = Path(__file__).resolve().parents[1] / "services/erp-api/alembic/versions/0001_initial.py"
    out.write_text(render(tables, enums, mixins))
    ncols = sum(len(t.columns) + sum(len(mixins.get(m, [])) for m in t.mixins) for t in tables)
    nidx = sum(len(t.indexes) for t in tables)
    nuq = sum(len(t.uniques) for t in tables)
    nck = sum(len(t.checks) for t in tables)
    print(f"wrote {out.name}: {len(tables)} tables, {ncols} columns, {len(enums)} enum types, "
          f"{nidx} indexes, {nuq} unique constraints, {nck} check constraints")
    print(f"  mixin registry: " + ", ".join(
        f"{k}({', '.join(c['name'] for c in v)})" for k, v in sorted(mixins.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
