"""demand_history: censored-demand support

SmartStock v2.1 de-censors demand before forecasting (core/forecast.py:decensor).
On a stockout day recorded sales are a LOWER BOUND on demand, and training on
them teaches the model to stay out of stock. `was_stocked_out` already existed;
this migration adds the two columns the correction actually needs and an index
for the per-SKU history pull that the nightly run does 50k times.

Revision ID: 0002_stocked_out_flags
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_stocked_out_flags"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    """True if the column already exists.

    CONVENTION for every incremental migration in this repo: guard additive
    column changes. scripts/gen_migration.py regenerates 0001_initial from
    models.py, so once a column reaches the model it is present on any FRESH
    database and this migration would fail with "duplicate column"; on a
    database deployed before the model changed, it is not. Guarding is correct
    in both directions, which is what upgrade -> downgrade -> upgrade tests.
    """
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    # Fraction of the bucket the SKU was actually available. 1.0 = never out.
    # Partial-day stockouts are the common case in retail and a boolean throws
    # that information away.
    if not _has_column("demand_history", "availability_ratio"):
        op.add_column(
            "demand_history",
            sa.Column("availability_ratio", sa.Numeric(precision=5, scale=4),
                      nullable=False, server_default=sa.text("1.0")),
        )
    # The de-censored estimate, persisted so a recommendation can be re-derived
    # exactly months later without re-running the estimator.
    if not _has_column("demand_history", "demand_uncensored"):
        op.add_column(
            "demand_history",
            sa.Column("demand_uncensored", sa.Numeric(precision=18, scale=4), nullable=True),
        )
    op.create_index(
        "ix_demand_censored",
        "demand_history",
        ["product_id", "location_id", "was_stocked_out"],
    )
    op.create_check_constraint(
        "ck_demand_availability_range",
        "demand_history",
        "availability_ratio >= 0 AND availability_ratio <= 1",
    ) if hasattr(op, "create_check_constraint") else None


def downgrade() -> None:
    try:
        op.drop_constraint("ck_demand_availability_range", "demand_history", type_="check")
    except Exception:
        pass  # SQLite cannot drop check constraints; harmless on downgrade
    op.drop_index("ix_demand_censored", table_name="demand_history")
    op.drop_column("demand_history", "demand_uncensored")
    op.drop_column("demand_history", "availability_ratio")
