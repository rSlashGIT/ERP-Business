#!/usr/bin/env python3
"""Would an EXISTING deployment actually receive the current schema?

    python3 scripts/audit_upgrade_path.py      (or: make audit-upgrade)

THE BUG THIS EXISTS TO CATCH
----------------------------
`scripts/gen_migration.py` regenerates `0001_initial` in place. For a fresh
install that is correct and convenient. For a database that already ran the
OLD 0001 it is silently destructive:

    the database is stamped 0003_size_seq
    0001 is rewritten to include nine new tables
    `alembic upgrade head` sees nothing to do and exits 0
    the tables are never created
    the app dies on first use with `relation ... does not exist`

`verify_migration.py` cannot see this. It builds from an EMPTY database, which
is exactly the one starting point where a regenerated 0001 looks complete. The
defect only exists on the upgrade path, so the upgrade path is what this
checks: replay the migrations the way an OLD deployment would have, then ask
whether the result matches `models.py`.

HOW
---
`0001_initial` is treated as frozen at whatever an old deployment ran — which
we cannot know exactly — so the conservative test is stronger: assume the old
0001 created only the tables that the INCREMENTAL migrations reference or that
predate them, then require every remaining model table to be created by some
migration AFTER 0001. Anything reachable only through a rewritten 0001 is
reported, because that is precisely what an existing database will not get.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "services" / "erp-api" / "alembic" / "versions"
MODELS = ROOT / "services" / "erp-api" / "app" / "db" / "models.py"


def model_tables() -> set[str]:
    src = MODELS.read_text()
    return set(re.findall(r'__tablename__\s*=\s*"([a-z_]+)"', src))


def created_in(path: Path) -> set[str]:
    """Tables a migration creates, whether by op.create_table or create_all."""
    src = path.read_text()
    tables = set(re.findall(r'create_table\(\s*"([a-z_]+)"', src))
    if "metadata.create_all" in src:
        # A reconciliation migration creates whatever the metadata holds, so it
        # covers every model table by construction.
        tables |= model_tables()
    return tables


def main() -> int:
    migs = sorted(VERSIONS.glob("0*.py"))
    if not migs:
        print(f"no migrations under {VERSIONS}")
        return 1

    initial = next((m for m in migs if m.name.startswith("0001")), None)
    later = [m for m in migs if m is not initial]

    want = model_tables()
    in_initial = created_in(initial) if initial else set()
    in_later: set[str] = set()
    for m in later:
        in_later |= created_in(m)

    print("Upgrade-path audit — can an EXISTING database reach the current schema?\n")
    print(f"  model tables                 : {len(want)}")
    print(f"  created by 0001_initial      : {len(in_initial)}")
    print(f"  created by later migrations  : {len(in_later)}")
    print(f"  migrations after 0001        : {', '.join(m.stem for m in later) or 'none'}\n")

    missing = sorted(want - in_initial - in_later)
    unreachable = sorted(want - in_later)

    failures = 0

    if missing:
        print(f"  FAIL  every model table is created by some migration")
        print(f"        never created anywhere: {missing}")
        failures += 1
    else:
        print(f"  PASS  every model table is created by some migration")

    # The real test. 0001 is rewritten, so an existing database only ever sees
    # the later migrations. Anything not creatable from those is unreachable
    # for every customer who is already live.
    if unreachable:
        print(f"  FAIL  an existing deployment can reach every model table")
        print(f"        reachable ONLY through the regenerated 0001, so a live")
        print(f"        database will never get {len(unreachable)}:")
        for t in unreachable[:12]:
            print(f"          - {t}")
        if len(unreachable) > 12:
            print(f"          ... and {len(unreachable) - 12} more")
        print(f"        FIX: add a reconciliation migration after the newest")
        print(f"             revision that creates what is missing.")
        failures += 1
    else:
        print(f"  PASS  an existing deployment can reach every model table")

    # The reconciliation migration must be last, or later work lands ahead of it.
    heads = {m.stem: None for m in migs}
    downs = {}
    for m in migs:
        src = m.read_text()
        rev = re.search(r'^revision\s*=\s*"([^"]+)"', src, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', src, re.M)
        if rev:
            downs[rev.group(1)] = down.group(1) if down and down.group(1) else None
    tips = [r for r in downs if r not in set(downs.values())]
    if len(tips) == 1:
        print(f"  PASS  the revision chain has a single head", f"  ({tips[0]})")
    else:
        print(f"  FAIL  the revision chain has a single head   heads: {tips}")
        failures += 1

    print("\n" + "=" * 70)
    if failures:
        print(f"{failures} FAILURE(S) — an upgrade would leave a live database broken")
        return 1
    print("upgrade path is sound for both a fresh install and a live database")
    print("\nNOTE: still a static check. `alembic upgrade head` against real")
    print("      PostgreSQL has not been executed here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
