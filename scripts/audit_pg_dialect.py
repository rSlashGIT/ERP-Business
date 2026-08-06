#!/usr/bin/env python3
"""Find what the SQLite shim cannot see about the Postgres migration.

    python3 scripts/audit_pg_dialect.py        (or: make audit-pg)

READ THIS FIRST — WHAT THIS IS NOT
----------------------------------
This is **not** a substitute for `alembic upgrade head` against a real
PostgreSQL. It cannot be. There is no Postgres binary, no docker daemon, and
pip/apt/npm are all 403 in this sandbox, so the real check has never run and
this file does not pretend otherwise.

What it IS: a static audit for the specific defect classes that SQLite silently
tolerates and Postgres rejects at DDL time. `verify_migration.py` executes the
chain against SQLite through an op-shim, and that shim ignores enum types
entirely, accepts any server_default string, and does not care about statement
ordering across foreign keys. So a migration can be fully green there and still
fail on the first line of a real deploy.

Each check below corresponds to a way that has actually happened to somebody.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIG = ROOT / "services" / "erp-api" / "alembic" / "versions"
MODELS = ROOT / "services" / "erp-api" / "app" / "db" / "models.py"
APP = ROOT / "services" / "erp-api" / "app"

findings: list[tuple[str, str, str]] = []      # (severity, check, detail)
checks_run = 0


def finding(sev: str, check: str, detail: str) -> None:
    findings.append((sev, check, detail))


def ok(check: str, detail: str = "") -> None:
    global checks_run
    checks_run += 1
    print(f"  PASS  {check}" + (f"   {detail}" if detail else ""))


def fail(check: str, detail: str) -> None:
    global checks_run
    checks_run += 1
    print(f"  FAIL  {check}   {detail}")
    finding("ERROR", check, detail)


def warn(check: str, detail: str) -> None:
    global checks_run
    checks_run += 1
    print(f"  WARN  {check}   {detail}")
    finding("WARN", check, detail)


def main() -> int:
    initial = MIG / "0001_initial.py"
    if not initial.exists():
        print(f"missing {initial}")
        return 1
    src = initial.read_text()
    models_src = MODELS.read_text()

    print("PostgreSQL dialect audit (static — the real upgrade has NOT been run)\n")

    # ── 1. every enum referenced must be created first ──
    # SQLite has no user types, so the shim never notices a missing CREATE TYPE.
    # On Postgres this is `type "x" does not exist` on the first create_table.
    declared = set(re.findall(r'^\s*"([a-z_]+)":\s*\[', src, re.M))
    used = set(re.findall(r'postgresql\.ENUM\(name="([a-z_]+)"', src))
    missing = used - declared
    if missing:
        fail("every ENUM used is created up front",
             f"used but never CREATE TYPE'd: {sorted(missing)}")
    else:
        ok("every ENUM used is created up front", f"{len(used)} types")

    model_enums = set(re.findall(r'SAEnum\([A-Za-z]+,\s*name="([a-z_]+)"', models_src))
    orphan = model_enums - declared
    if orphan:
        fail("every enum on a model reaches the migration", f"missing: {sorted(orphan)}")
    else:
        ok("every enum on a model reaches the migration", f"{len(model_enums)} model enums")

    # ── 2. downgrade must drop the types it created ──
    if "postgresql.ENUM(name=name).drop" in src or ".drop(bind" in src:
        ok("downgrade drops the enum types")
    else:
        fail("downgrade drops the enum types",
             "re-running upgrade after a downgrade will hit 'type already exists'")

    # ── 3. foreign keys must point at tables created EARLIER ──
    # SQLite defers FK resolution and the shim ignores order entirely; Postgres
    # rejects a reference to a table that does not exist yet.
    order: list[str] = []
    fks: list[tuple[str, str, int]] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_table"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        tbl = node.args[0].value
        order.append(tbl)
        seg = ast.get_source_segment(src, node) or ""
        for target in re.findall(r'ForeignKey\("([a-z_]+)\.', seg):
            fks.append((tbl, target, len(order) - 1))
    pos = {t: i for i, t in enumerate(order)}
    bad_order = [(t, tgt) for t, tgt, i in fks
                 if tgt in pos and pos[tgt] > i and tgt != t]
    unknown = sorted({tgt for _, tgt, _ in fks if tgt not in pos})
    if bad_order:
        fail("every FK target is created before it is referenced",
             "; ".join(f"{t} -> {tgt}" for t, tgt in bad_order[:6]))
    else:
        ok("every FK target is created before it is referenced",
           f"{len(fks)} FKs across {len(order)} tables")
    if unknown:
        fail("every FK target table exists in this migration", str(unknown))
    else:
        ok("every FK target table exists in this migration")

    # ── 4. indexes must name real columns of the table ──
    cols: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_table"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        seg = ast.get_source_segment(src, node) or ""
        cols[node.args[0].value] = set(re.findall(r'sa\.Column\("([a-z_0-9]+)"', seg))
    bad_idx = []
    for m in re.finditer(r'create_index\(\s*"([^"]+)",\s*"([a-z_]+)",\s*\[([^\]]*)\]', src):
        _name, tbl, colspec = m.groups()
        for c in re.findall(r'"([a-z_0-9]+)"', colspec):
            if tbl in cols and c not in cols[tbl]:
                bad_idx.append(f"{tbl}.{c}")
    if bad_idx:
        fail("indexed columns exist on their table", ", ".join(bad_idx[:6]))
    else:
        ok("indexed columns exist on their table")

    # ── 5. server_default strings Postgres will actually accept ──
    # SQLite's shim rewrites now() and swallows the rest.
    SQLITE_ONLY = ("datetime('now')", "CURRENT_TIMESTAMP()", "strftime(",
                   "AUTOINCREMENT", "IFNULL(", "julianday(", "randomblob(")
    hits = [tok for tok in SQLITE_ONLY if tok in src]
    if hits:
        fail("no SQLite-only SQL in the migration", f"found {hits}")
    else:
        ok("no SQLite-only SQL in the migration")

    # ── 6. and none in the PRODUCTION application code either ──
    # The demo server is sqlite by design; app/ has to run on Postgres.
    app_hits = []
    for py in APP.rglob("*.py"):
        text = py.read_text()
        for tok in SQLITE_ONLY:
            if tok in text:
                app_hits.append(f"{py.relative_to(ROOT)}: {tok}")
    if app_hits:
        fail("no SQLite-only SQL in services/erp-api/app", "; ".join(app_hits[:6]))
    else:
        ok("no SQLite-only SQL in services/erp-api/app",
           f"{sum(1 for _ in APP.rglob('*.py'))} modules scanned")

    # ── 7. reserved words used bare as identifiers ──
    RESERVED = {"user", "order", "group", "table", "column", "check", "default",
                "references", "primary", "constraint", "session", "authorization",
                "grant", "limit", "offset", "window", "collation"}
    bad_ident = set()
    for tbl, cs in cols.items():
        if tbl in RESERVED:
            bad_ident.add(f"table {tbl}")
        for c in cs:
            if c in RESERVED:
                bad_ident.add(f"{tbl}.{c}")
    if bad_ident:
        warn("no reserved words as bare identifiers", ", ".join(sorted(bad_ident)[:6]))
    else:
        ok("no reserved words as bare identifiers")

    # ── 8. JSONB columns need a jsonb-shaped default, not '{}' as text ──
    bad_json = re.findall(r'JSONB[^\n]*server_default=(?!text\("\'\{\}\'::jsonb"\))[^\n,)]+', src)
    if bad_json:
        warn("JSONB defaults are cast to jsonb", f"{len(bad_json)} suspicious")
    else:
        ok("JSONB defaults are cast to jsonb")

    # ── 9. the downgrade must drop tables in reverse creation order ──
    drops = re.findall(r'op\.drop_table\("([a-z_]+)"\)', src)
    expected = list(reversed(order))
    if drops and drops != expected:
        first = next((d for d, e in zip(drops, expected) if d != e), None)
        warn("downgrade drops tables in reverse creation order",
             f"diverges at {first!r}; FK-dependent drops may fail on Postgres")
    else:
        ok("downgrade drops tables in reverse creation order", f"{len(drops)} drops")

    # ── 10. NOT NULL added to an existing table needs a default or a backfill ──
    risky = re.findall(r'add_column\([^)]*nullable=False(?![^)]*server_default)', src)
    for f in sorted(MIG.glob("000[2-9]*.py")):
        t = f.read_text()
        risky += re.findall(r'add_column\([^)]*nullable=False(?![^)]*server_default)', t)
    if risky:
        fail("NOT NULL columns added later carry a server_default",
             f"{len(risky)} would fail on a non-empty table")
    else:
        ok("NOT NULL columns added later carry a server_default")

    print("\n" + "=" * 70)
    errors = [f for f in findings if f[0] == "ERROR"]
    warns = [f for f in findings if f[0] == "WARN"]
    print(f"{checks_run - len(findings)}/{checks_run} static checks clean"
          + (f", {len(errors)} ERROR" if errors else "")
          + (f", {len(warns)} WARN" if warns else ""))
    print("\nThis is a STATIC audit. `alembic upgrade head` against a real")
    print("PostgreSQL has still never been executed here — no pg binary, no")
    print("docker, and pip/apt are 403. Run it before you deploy.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
