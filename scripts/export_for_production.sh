#!/usr/bin/env bash
#
# Package this workspace so the three environment-blocked jobs can be run on a
# machine with a real network.
#
#     bash scripts/export_for_production.sh [output-dir]
#
# WHY THIS EXISTS
# ---------------
# Three things cannot be done in the dev sandbox, and no amount of cleverness
# changes that:
#
#   * `alembic upgrade head` against real PostgreSQL — no postgres binary, no
#     docker daemon, and pip is 403.
#   * `npm run build` in apps/web — the npm registry is 403.
#   * the full 8,523-row BigMart dataset — the fetch tool caps a response at
#     ~62 KB, and other URL-fetch methods are not permitted here.
#
# Everything else in this repo is verified. These three are not "probably
# fine"; they are UNRUN, and this script exists so they stop being unrun.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
STAMP="$(date +%Y%m%d-%H%M)"
NAME="erp-smartstock-$STAMP"
ARCHIVE="$OUT/$NAME.zip"

mkdir -p "$OUT"

echo "packaging $ROOT"

# Packaged with Python's stdlib zipfile rather than the `zip` binary, and not
# for purity: `zip -x` still WALKS every excluded path before discarding it.
# `demo/A 2/` is a vendored reference folder of 7,396 files, so on a network or
# mounted filesystem that walk alone took longer than the whole archive should.
# os.walk with in-place `dirs[:]` pruning never descends into it at all.
cd "$ROOT"
python3 - "$ARCHIVE" <<'PYEOF'
import os
import sys
import zipfile

out = sys.argv[1]
SKIP_DIRS = {"__pycache__", "node_modules", "dist", ".git", ".venv",
             ".pytest_cache", ".ruff_cache", "A 2"}
SKIP_EXT = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".db-journal")

n = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for base, dirs, files in os.walk("."):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith(SKIP_EXT):
                continue
            path = os.path.join(base, f)
            z.write(path, os.path.relpath(path, "."))
            n += 1
print(f"  {n} files packaged")
PYEOF

SIZE="$(du -h "$ARCHIVE" | cut -f1)"

cat <<EOF

════════════════════════════════════════════════════════════════════════
  $ARCHIVE  ($SIZE)
════════════════════════════════════════════════════════════════════════

Copy that to a machine with a normal internet connection and run the three
jobs below. Each one is currently UNRUN, not "probably fine".

────────────────────────────────────────────────────────────────────────
1 · PostgreSQL + Alembic          the one most likely to surface a real bug
────────────────────────────────────────────────────────────────────────

    docker run -d --name erp-pg -e POSTGRES_PASSWORD=erp \\
        -e POSTGRES_DB=erp -p 5432:5432 postgres:16

    cd services/erp-api
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt        # or: alembic sqlalchemy psycopg2-binary
    export DATABASE_URL=postgresql+psycopg2://postgres:erp@localhost:5432/erp

    alembic upgrade head                   # <-- from EMPTY
    alembic revision --autogenerate -m probe   # <-- must produce an EMPTY diff

  What to check:
    * 32 tables and 449 columns, matching \`make migrate-verify\`.
    * The autogenerate diff is empty. Anything in it is real model/migration
      drift that the SQLite shim could not see.
    * Then test the UPGRADE path, which is the case that actually broke once:
        alembic downgrade 0003_size_seq
        alembic upgrade head               # 0004 must create what is missing
      A live database is stamped 0003, and \`0001_initial\` gets REGENERATED —
      so 0004_reconcile_schema is the only thing standing between an existing
      customer and a schema that silently never arrives.

  Known Postgres-specific risks the SQLite shim cannot test:
    enum type creation order, JSONB defaults, \`server_default\` syntax, and
    NOT NULL columns added to a table that already has rows.
    \`make audit-pg\` checks these statically — 12/12 clean — but statically.

────────────────────────────────────────────────────────────────────────
2 · The web build
────────────────────────────────────────────────────────────────────────

    cd apps/web
    rm -f typecheck-shims.d.ts tsconfig.shim.json    # sandbox-only crutches
    npm install
    npm run build            # tsc --noEmit && vite build
    npm run preview          # then open the served build

  What to check:
    * The source already typechecks clean against hand-written shims
      (\`make typecheck-web\`): 379 errors down to 9, all of them callback
      parameters whose types come from library signatures. No real defect was
      found — but a typecheck is not a build, and no dist/ has ever existed.
    * \`tsconfig.json\` uses \`baseUrl\`, which TypeScript 6 deprecates. Harmless
      at the pinned ^5.5.3; it will shout if you upgrade.

────────────────────────────────────────────────────────────────────────
3 · The full BigMart dataset
────────────────────────────────────────────────────────────────────────

    curl -L -o demo/data/bigmart_train_full.csv \\
      https://raw.githubusercontent.com/akki8087/Big-Mart-Sales/master/Train.csv

    # point the validator at the full file, then:
    python3 scripts/validate_pricing_bigmart.py

  What to check:
    * 8,523 rows and 1,559 products, against the 612 rows / 514 products the
      sandbox could fetch.
    * The current finding is that dearer products do NOT sell fewer units —
      47.5% of 1,693 held-out pairs, z = -2.02, significant in the direction
      OPPOSITE to a price effect, which is what quality confounding looks like.
    * That sample is NOT underpowered: SE 1.22 pp, so it detects a 2.4 pp
      shift. Any effect large enough to price on is already excluded. Expect
      the full file to sharpen this, not reverse it. If it DOES reverse,
      that is a genuine finding and Price Advisor's \`estimated\` tier should
      be revisited.

────────────────────────────────────────────────────────────────────────
Before you trust any of it
────────────────────────────────────────────────────────────────────────

    make verify        # every suite, both audits, demo-check, verify-ui, mutate

  On this machine that is: 7 test suites, 4 audits, 106 demo assertions,
  128 UI render checks and 45 mutations, all green.

EOF
