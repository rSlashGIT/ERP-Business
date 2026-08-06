"""Reconcile an already-deployed database with the current models.

Revision ID: 0004_reconcile_schema
Revises: 0003_size_seq

WHY THIS EXISTS — a real bug, not housekeeping
----------------------------------------------
`scripts/gen_migration.py` REGENERATES `0001_initial` from `models.py`. That is
fine for a fresh install: the regenerated 0001 contains every table and the
database comes up complete.

It is silently wrong for a database that already exists.

An existing deployment ran the OLD 0001 and is stamped at `0003_size_seq`.
Regenerating 0001 does not change that stamp, so `alembic upgrade head` has
nothing to do and returns success. Every table added since — `customers`,
`sales_invoices`, `sales_invoice_lines`, `payments`, `payment_allocations`,
`goods_receipts`, `goods_receipt_lines`, `credit_notes`, `credit_note_lines`
— is never created. The application then dies on first use with
`relation "goods_receipts" does not exist`, on a deploy that reported clean.

`verify_migration.py` could not catch this because it always builds from an
EMPTY database, which is the one case where the bug does not appear.

WHAT THIS DOES
--------------
Converges whatever is there towards `models.py`:

  * creates any table in the metadata that the database is missing, in
    dependency order, with the right enum types;
  * adds the handful of columns that were introduced on EXISTING tables after
    0003 and therefore live only in the regenerated 0001.

Idempotent by construction. On a fresh database everything already exists and
this is a no-op; on an old one it fills the gap. Safe to run twice.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_reconcile_schema"
down_revision = "0003_size_seq"
branch_labels = None
depends_on = None


#: Columns added to tables that ALREADY existed at 0003, so `create_all` cannot
#: help — it only creates whole tables. (table, column, DDL type, default)
LATE_COLUMNS = [
    ("purchase_order_lines", "received_qty", sa.Numeric(18, 4), "0"),
    ("products", "style_id", sa.dialects.postgresql.UUID(as_uuid=True), None),
    ("products", "size", sa.String(16), None),
    ("products", "size_seq", sa.Integer(), None),
    ("products", "colour", sa.String(64), None),
    ("products", "barcode", sa.String(64), None),
]


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # The metadata is the single source of truth; importing it here rather than
    # restating the DDL means this migration cannot drift from the models.
    #
    # Under `alembic upgrade head` this import always works — env.py puts the
    # service on sys.path. It fails only in the offline SQLite verifier, which
    # has neither SQLAlchemy nor the app package, and which starts from an
    # EMPTY database where 0001 has already created everything and there is by
    # definition nothing to reconcile. Say so out loud rather than skipping
    # quietly, so a silent no-op is never mistaken for a successful run.
    try:
        from app.db.models import Base  # noqa: PLC0415
    except ImportError as exc:                                  # pragma: no cover
        print(f"  0004: models not importable ({exc.name}); nothing to reconcile "
              f"from an empty database. This is expected ONLY in the offline "
              f"verifier — under real alembic this import must succeed.")
        return

    before = _tables(bind)

    # Postgres needs the enum types to exist before any table that uses them.
    # checkfirst=True makes this a no-op where they already do.
    if bind.dialect.name == "postgresql":
        for enum in {
            col.type for table in Base.metadata.tables.values()
            for col in table.columns
            if isinstance(col.type, sa.Enum)
        }:
            enum.create(bind, checkfirst=True)

    # create_all resolves dependency order itself and skips what exists.
    Base.metadata.create_all(bind, checkfirst=True)

    created = sorted(_tables(bind) - before)
    if created:
        print(f"  reconciled: created {len(created)} missing table(s): {created}")

    for table, column, coltype, default in LATE_COLUMNS:
        if table in _tables(bind) and column not in _columns(bind, table):
            op.add_column(
                table,
                sa.Column(column, coltype, nullable=True,
                          server_default=sa.text(default) if default else None),
            )
            print(f"  reconciled: added {table}.{column}")


def downgrade() -> None:
    """Deliberately a no-op.

    This migration only ever CREATES what was missing. Dropping those tables on
    downgrade would destroy a fresh installation's core schema — on a database
    built from the regenerated 0001, these tables belong to 0001, not to this
    revision. Reversing a reconciliation is not meaningful; use 0001's own
    downgrade to tear the schema down.
    """
