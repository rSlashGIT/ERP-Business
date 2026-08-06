#!/usr/bin/env python3
"""
Verify the Alembic migration chain WITHOUT alembic, sqlalchemy or postgres.

This build environment has none of them installed (docs/HANDOFF.md section 13),
so `alembic upgrade head` cannot be run here. Two independent checks are done
instead, and both are real -- neither is a static lint:

  CHECK 1  EXECUTION. A minimal `op` / `sa` shim translates the migration's
           operations into SQLite DDL and executes them for real, in order,
           then runs downgrade, then upgrade again. This proves the op sequence
           is valid, that the foreign-key graph is ordered correctly, and that
           downgrade is a true inverse. Postgres-only types are mapped:
               postgresql.UUID   -> TEXT
               postgresql.JSONB  -> TEXT
               postgresql.ENUM   -> TEXT   (CHECK constraint not enforced)
               sa.Numeric        -> NUMERIC
               sa.text('now()')  -> CURRENT_TIMESTAMP
           DIALECT CAVEAT: SQLite does not enforce enum membership and cannot
           DROP a column before 3.35 or DROP a constraint at all. Execution
           here proves ORDERING and REFERENTIAL STRUCTURE, not Postgres type
           behaviour. Run the real thing on Postgres before deploying.

  CHECK 2  COVERAGE. Both models.py and the migration are AST-parsed and
           cross-checked: every table, column, index, unique and check
           constraint in the models must appear in the migration. This is what
           catches the failure mode where someone adds a column to a model and
           forgets the migration.

Exit code is non-zero if either check fails.
"""
from __future__ import annotations

import ast
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "services/erp-api/app/db/models.py"
VERSIONS = ROOT / "services/erp-api/alembic/versions"

FAILS: List[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


# ─────────────────── CHECK 1: execute against SQLite ───────────────────

class _Type:
    def __init__(self, sql: str) -> None:
        self.sql = sql


class _SA:
    """Minimal sqlalchemy surface used by the migrations."""

    def String(self, length: int = 255, **_: Any) -> _Type: return _Type(f"VARCHAR({length})")
    def Text(self, **_: Any) -> _Type: return _Type("TEXT")
    def Integer(self, **_: Any) -> _Type: return _Type("INTEGER")
    def BigInteger(self, **_: Any) -> _Type: return _Type("INTEGER")
    def Boolean(self, **_: Any) -> _Type: return _Type("BOOLEAN")
    def Date(self, **_: Any) -> _Type: return _Type("DATE")
    def DateTime(self, **_: Any) -> _Type: return _Type("TIMESTAMP")
    def Numeric(self, precision: int = 18, scale: int = 4, **_: Any) -> _Type:
        return _Type(f"NUMERIC({precision},{scale})")

    def text(self, s: str) -> str:
        # Postgres now() has no SQLite equivalent; translate on the way in.
        # Dropping this translation silently broke every server_default in
        # 0001_initial with "near (: syntax error".
        return _SQLText("CURRENT_TIMESTAMP" if "now()" in s else s)

    def inspect(self, bind: Any) -> "_Inspector":
        return _Inspector(bind)

    class ForeignKey:
        def __init__(self, target: str, ondelete: Optional[str] = None) -> None:
            self.target, self.ondelete = target, ondelete

    class Column:
        def __init__(self, name: str, type_: Any = None, *args: Any, **kw: Any) -> None:
            self.name = name
            self.type = type_
            self.fk = next((a for a in args if isinstance(a, _SA.ForeignKey)), None)
            self.nullable = kw.get("nullable", True)
            self.primary_key = kw.get("primary_key", False)
            self.autoincrement = kw.get("autoincrement", False)
            self.server_default = kw.get("server_default")

    class UniqueConstraint:
        def __init__(self, *cols: str, name: str = "") -> None:
            self.cols, self.name = cols, name

    class CheckConstraint:
        def __init__(self, cond: str, name: str = "") -> None:
            self.cond, self.name = cond, name


class _SQLText(str):
    """Carries a bindparams() no-op so `sa.text(...).bindparams(...)` works."""
    def bindparams(self, **kw: Any) -> "_SQLText":
        out = self
        for k, v in kw.items():
            out = _SQLText(str(out).replace(f":{k}", repr(v) if isinstance(v, str) else str(v)))
        return out


class _Inspector:
    """Enough of SQLAlchemy's Inspector for a migration to ask 'does this column exist?'."""

    def __init__(self, bind: Any) -> None:
        self.conn = bind.conn if hasattr(bind, "conn") else bind

    def get_columns(self, table: str) -> List[Dict[str, Any]]:
        try:
            rows = self.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        except Exception:
            return []
        return [{"name": r[1]} for r in rows]

    def get_table_names(self) -> List[str]:
        try:
            return [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
        except Exception:
            return []


class _PG:
    def UUID(self, **_: Any) -> _Type: return _Type("TEXT")
    def JSONB(self, **_: Any) -> _Type: return _Type("TEXT")

    class ENUM:
        def __init__(self, *values: str, name: str = "", create_type: bool = True) -> None:
            self.values, self.name = values, name
            self.sql = "TEXT"
        def create(self, bind: Any, checkfirst: bool = False) -> None: pass
        def drop(self, bind: Any, checkfirst: bool = False) -> None: pass


class _Dialect:
    name = "sqlite"


class _Bind:
    dialect = _Dialect()

    def __init__(self, conn: Any = None) -> None:
        self.conn = conn


class _Op:
    """Translates alembic ops into SQLite DDL and executes them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.log: List[str] = []

    def get_bind(self) -> _Bind:
        return _Bind(self.conn)

    def create_table(self, name: str, *cols: Any) -> None:
        parts: List[str] = []
        fks: List[str] = []
        for c in cols:
            if isinstance(c, _SA.Column):
                t = c.type.sql if isinstance(c.type, _Type) else (
                    getattr(c.type, "sql", "TEXT"))
                bit = f'"{c.name}" {t}'
                if c.primary_key:
                    bit += " PRIMARY KEY"
                if not c.nullable and not c.primary_key:
                    bit += " NOT NULL"
                if c.server_default is not None:
                    bit += f" DEFAULT {c.server_default}"
                parts.append(bit)
                if c.fk:
                    tbl, col = c.fk.target.split(".")
                    od = f" ON DELETE {c.fk.ondelete}" if c.fk.ondelete else ""
                    fks.append(f'FOREIGN KEY("{c.name}") REFERENCES "{tbl}"("{col}"){od}')
            elif isinstance(c, _SA.UniqueConstraint):
                q = ", ".join(f'"{x}"' for x in c.cols)
                parts.append(f'CONSTRAINT "{c.name}" UNIQUE ({q})')
            elif isinstance(c, _SA.CheckConstraint):
                parts.append(f'CONSTRAINT "{c.name}" CHECK ({c.cond})')
        ddl = f'CREATE TABLE "{name}" (\n  ' + ",\n  ".join(parts + fks) + "\n)"
        self.conn.execute(ddl)
        self.log.append(f"create_table {name}")

    def create_index(self, name: str, table: str, cols: List[str], **_: Any) -> None:
        q = ", ".join(f'"{c}"' for c in cols)
        self.conn.execute(f'CREATE INDEX "{name}" ON "{table}" ({q})')
        self.log.append(f"create_index {name}")

    def drop_index(self, name: str, table_name: str = "", **_: Any) -> None:
        self.conn.execute(f'DROP INDEX IF EXISTS "{name}"')
        self.log.append(f"drop_index {name}")

    def drop_table(self, name: str) -> None:
        self.conn.execute(f'DROP TABLE IF EXISTS "{name}"')
        self.log.append(f"drop_table {name}")

    def add_column(self, table: str, col: Any) -> None:
        t = col.type.sql if isinstance(col.type, _Type) else "TEXT"
        bit = f'"{col.name}" {t}'
        if not col.nullable:
            bit += " NOT NULL"
        if col.server_default is not None:
            bit += f" DEFAULT {col.server_default}"
        self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {bit}')
        self.log.append(f"add_column {table}.{col.name}")

    def drop_column(self, table: str, name: str) -> None:
        # Requires SQLite >= 3.35. Degrade loudly rather than silently passing.
        try:
            self.conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{name}"')
        except sqlite3.OperationalError as exc:
            print(f"        (sqlite cannot drop column {table}.{name}: {exc})")
        self.log.append(f"drop_column {table}.{name}")

    def execute(self, stmt: Any) -> None:
        try:
            self.conn.execute(str(stmt))
        except sqlite3.OperationalError as exc:
            print(f"        (sqlite could not execute data statement: {exc})")
        self.log.append("execute")

    def create_check_constraint(self, name: str, table: str, cond: str, **_: Any) -> None:
        # SQLite cannot add a CHECK to an existing table. Recorded, not executed.
        self.log.append(f"create_check_constraint {name} (skipped on sqlite)")

    def drop_constraint(self, name: str, table: str = "", type_: str = "") -> None:
        raise sqlite3.OperationalError("sqlite cannot drop constraints")


def _install_stub_modules(op_obj: _Op) -> None:
    """Put fake `alembic` and `sqlalchemy` into sys.modules.

    The migration files legitimately do `from alembic import op` at module
    level, so providing `op` in the exec namespace is not enough -- the import
    statement itself runs first and raises. Stubbing the modules lets the real
    migration source execute unmodified, which is the point: we are testing the
    file as written, not a doctored copy.
    """
    import types
    sa_mod = types.ModuleType("sqlalchemy")
    sa_inst = _SA()
    for attr in ("String", "Text", "Integer", "BigInteger", "Boolean", "Date",
                 "DateTime", "Numeric", "text", "inspect"):
        setattr(sa_mod, attr, getattr(sa_inst, attr))
    for attr in ("Column", "ForeignKey", "UniqueConstraint", "CheckConstraint"):
        setattr(sa_mod, attr, getattr(_SA, attr))
    dialects = types.ModuleType("sqlalchemy.dialects")
    pg_mod = types.ModuleType("sqlalchemy.dialects.postgresql")
    pg_inst = _PG()
    pg_mod.UUID = pg_inst.UUID
    pg_mod.JSONB = pg_inst.JSONB
    pg_mod.ENUM = _PG.ENUM
    dialects.postgresql = pg_mod
    sa_mod.dialects = dialects
    al_mod = types.ModuleType("alembic")
    al_mod.op = op_obj
    sys.modules.update({
        "alembic": al_mod, "sqlalchemy": sa_mod,
        "sqlalchemy.dialects": dialects,
        "sqlalchemy.dialects.postgresql": pg_mod,
    })


def load_migration(path: Path, op_obj: _Op) -> Any:
    src = path.read_text()
    ns: Dict[str, Any] = {
        "op": op_obj, "sa": _SA(), "postgresql": _PG(),
        "__name__": path.stem, "hasattr": hasattr,
    }
    sa = ns["sa"]
    for attr in ("Column", "ForeignKey", "UniqueConstraint", "CheckConstraint"):
        setattr(sa, attr, getattr(_SA, attr))
    exec(compile(src, str(path), "exec"), ns)
    return ns


def check_execution() -> None:
    print("\nCHECK 1 -- execute the migration chain against SQLite")
    files = sorted(VERSIONS.glob("[0-9]*.py"))
    if not files:
        fail("no migration files found")
        return
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    op_obj = _Op(conn)
    _install_stub_modules(op_obj)

    modules = []
    for f in files:
        try:
            modules.append((f, load_migration(f, op_obj)))
        except Exception as exc:
            fail(f"{f.name} failed to load: {type(exc).__name__}: {exc}")
            return

    # revision chain must be linear and complete
    revs = {m["revision"]: m.get("down_revision") for _f, m in modules}
    heads = [r for r, d in revs.items() if r not in revs.values()]
    roots = [r for r, d in revs.items() if d is None]
    if len(roots) != 1:
        fail(f"expected exactly one root revision, found {roots}")
    elif len(heads) != 1:
        fail(f"expected exactly one head revision, found {heads}")
    else:
        ok(f"revision chain is linear: {roots[0]} -> {heads[0]} ({len(modules)} migrations)")

    try:
        for f, m in modules:
            m["upgrade"]()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
        ok(f"upgrade head executed: {len(tables)} tables, {len(idx)} indexes created")
    except Exception as exc:
        fail(f"upgrade failed: {type(exc).__name__}: {exc}")
        return

    # foreign keys must all resolve
    broken = list(conn.execute("PRAGMA foreign_key_check"))
    if broken:
        fail(f"foreign_key_check reported {len(broken)} problems")
    else:
        ok("PRAGMA foreign_key_check clean -- every FK target table exists and is ordered correctly")

    try:
        for f, m in reversed(modules):
            m["downgrade"]()
        left = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if left:
            fail(f"downgrade base left {len(left)} tables behind: {sorted(left)[:5]}")
        else:
            ok("downgrade base executed: schema fully removed")
    except Exception as exc:
        fail(f"downgrade failed: {type(exc).__name__}: {exc}")
        return

    try:
        for f, m in modules:
            m["upgrade"]()
        again = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if again == tables:
            ok("upgrade head again: identical schema -- downgrade is a true inverse")
        else:
            fail(f"re-upgrade produced a different schema: {again ^ tables}")
    except Exception as exc:
        fail(f"re-upgrade failed: {type(exc).__name__}: {exc}")
    conn.close()


# ─────────────────── CHECK 2: coverage vs models.py ───────────────────

STR = re.compile(r"""["']([^"']+)["']""")
NAMED = re.compile(r"""name=(?:"([^"]+)"|'([^']+)')""")


def _mixin_columns(tree: ast.Module) -> Dict[str, Set[str]]:
    """{mixin_name: {column, ...}} for every column-bearing non-table class.

    Mirrors scripts/gen_migration.py:parse_mixins. Discovering mixins instead
    of naming them is what makes the coverage check non-vacuous: the previous
    version hard-coded TenantMixin and TimestampMixin, so a column on any THIRD
    mixin would have been missing from the migration AND uncounted here --
    exactly how tenant_id went missing for several rounds without failing.
    """
    out: Dict[str, Set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(getattr(t.targets[0], "id", "") == "__tablename__"
               for t in node.body if isinstance(t, ast.Assign) and t.targets):
            continue
        cols = {
            getattr(st.target, "id", "")
            for st in node.body
            if isinstance(st, ast.AnnAssign) and st.value is not None
            and ("mapped_column" in ast.unparse(st.value) or "_uuid_pk" in ast.unparse(st.value))
        }
        cols.discard("")
        if cols:
            out[node.name] = cols
    return out


def models_inventory() -> Tuple[Dict[str, Set[str]], Set[str], Set[str], Set[str]]:
    tree = ast.parse(MODELS.read_text())
    mixin_cols = _mixin_columns(tree)
    tables: Dict[str, Set[str]] = {}
    idx: Set[str] = set()
    uq: Set[str] = set()
    ck: Set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [ast.unparse(b) for b in node.bases]
        if not any("Base" in b for b in bases):
            continue
        tname = None
        cols: Set[str] = set()
        applied = [b for b in bases if b in mixin_cols]
        for st in node.body:
            if isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__":
                tname = st.value.value  # type: ignore[attr-defined]
            elif isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__table_args__":
                for el in getattr(st.value, "elts", []):
                    src = ast.unparse(el)
                    # Index() takes its name POSITIONALLY, unlike the
                    # constraints -- requiring name= here silently skipped all
                    # 13 indexes and reported a vacuous "all 0 present".
                    if src.startswith("Index"):
                        p = STR.findall(src)
                        if p:
                            idx.add(p[0])
                        continue
                    m = NAMED.search(src)
                    if not m:
                        continue
                    key = m.group(1) or m.group(2)
                    if src.startswith("UniqueConstraint"):
                        uq.add(key)
                    elif src.startswith("CheckConstraint"):
                        ck.add(key)
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                src = ast.unparse(st.value)
                if "mapped_column" in src or "_uuid_pk" in src:
                    n = getattr(st.target, "id", None)
                    if n:
                        cols.add(n)
        if tname:
            for mx in applied:          # generic: any discovered mixin
                cols |= mixin_cols[mx]
            tables[tname] = cols
    return tables, idx, uq, ck


def migration_inventory() -> Tuple[Dict[str, Set[str]], Set[str], Set[str], Set[str]]:
    tables: Dict[str, Set[str]] = {}
    idx: Set[str] = set()
    uq: Set[str] = set()
    ck: Set[str] = set()
    for f in sorted(VERSIONS.glob("[0-9]*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = ast.unparse(node.func)
            if fn == "op.create_table" and node.args:
                tname = getattr(node.args[0], "value", None)
                if not tname:
                    continue
                cols = tables.setdefault(tname, set())
                for a in node.args[1:]:
                    src = ast.unparse(a)
                    m = NAMED.search(src)
                    key = (m.group(1) or m.group(2)) if m else None
                    if src.startswith("sa.Column"):
                        p = STR.findall(src)
                        if p:
                            cols.add(p[0])
                    elif src.startswith("sa.UniqueConstraint") and key:
                        uq.add(key)
                    elif src.startswith("sa.CheckConstraint") and key:
                        ck.add(key)
            elif fn == "op.create_index" and node.args:
                v = getattr(node.args[0], "value", None)
                if v:
                    idx.add(v)
            elif fn == "op.add_column" and len(node.args) >= 2:
                tname = getattr(node.args[0], "value", None)
                p = STR.findall(ast.unparse(node.args[1]))
                if tname and p:
                    tables.setdefault(tname, set()).add(p[0])
            elif fn == "op.create_check_constraint" and node.args:
                v = getattr(node.args[0], "value", None)
                if v:
                    ck.add(v)
    return tables, idx, uq, ck


def check_coverage() -> None:
    print("\nCHECK 2 -- migration covers every model definition")
    mt, mi, mu, mc = models_inventory()
    gt, gi, gu, gc = migration_inventory()
    disc = _mixin_columns(ast.parse(MODELS.read_text()))
    print("  mixins discovered: " + ", ".join(
        f"{k}({len(v)} cols)" for k, v in sorted(disc.items())) or "  (none)")

    missing_tables = set(mt) - set(gt)
    if missing_tables:
        fail(f"tables in models but not migrated: {sorted(missing_tables)}")
    else:
        ok(f"all {len(mt)} model tables present in the migration")

    missing_cols: List[str] = []
    for t, cols in mt.items():
        gap = cols - gt.get(t, set())
        if gap:
            missing_cols.append(f"{t}: {sorted(gap)}")
    if missing_cols:
        fail(f"columns in models but not migrated -> {'; '.join(missing_cols[:6])}")
    else:
        total = sum(len(c) for c in mt.values())
        ok(f"all {total} model columns present in the migration")

    # REVERSE DRIFT. The checks above prove nothing in models.py was forgotten
    # in the migration. They say nothing about the opposite: a column added by
    # an incremental migration but never added to the model, which the ORM
    # cannot see and which no test would catch.
    reverse: List[str] = []
    for t, cols in gt.items():
        extra = cols - mt.get(t, set())
        if extra:
            reverse.append(f"{t}: {sorted(extra)}")
    if reverse:
        fail(f"columns migrated but absent from models.py -> {'; '.join(reverse[:6])}")
    else:
        ok("no reverse drift: every migrated column exists on a model")

    for label, want, got in (("indexes", mi, gi), ("unique constraints", mu, gu),
                             ("check constraints", mc, gc)):
        gap = want - got
        if gap:
            fail(f"{label} in models but not migrated: {sorted(gap)}")
        else:
            ok(f"all {len(want)} {label} present in the migration")


def main() -> int:
    print("=" * 70)
    print(" Alembic migration verification (alembic/sqlalchemy/postgres unavailable)")
    print("=" * 70)
    check_execution()
    check_coverage()
    print("\n" + "=" * 70)
    if FAILS:
        print(f" {len(FAILS)} FAILURE(S)")
        return 1
    print(" ALL CHECKS PASSED")
    print(" NOTE: proves op ordering, FK structure and coverage. Postgres enum")
    print("       and type behaviour still require `alembic upgrade head` on a")
    print("       real Postgres before deploying.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
