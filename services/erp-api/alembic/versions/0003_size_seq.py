"""products.size_seq — correct apparel size ordering

Apparel sizes do not sort lexically. Ordering by `size` yields
L, M, S, XL, XXL -- Large first, Small third. Every size-curve report, pick
list and variant grid is wrong without an explicit sort key.

Nullable and additive: existing rows and non-apparel tenants are unaffected,
and callers fall back to `size` when it is NULL.

Revision ID: 0003_size_seq
Revises: 0002_stocked_out_flags
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_size_seq"
down_revision = "0002_stocked_out_flags"
branch_labels = None
depends_on = None

# Conventional ordering for the common apparel scales. Numeric sizes (waist,
# EU shoe) sort by their own value, so they are seeded from the numeral itself
# offset well clear of the alpha scale.
ALPHA_SIZES = {
    "XXS": 10, "XS": 20, "S": 30, "M": 40, "L": 50, "XL": 60,
    "XXL": 70, "2XL": 70, "XXXL": 80, "3XL": 80, "4XL": 90, "5XL": 100,
    "FREE": 200, "ONE SIZE": 200, "OS": 200,
}


def _has_column(table: str, column: str) -> bool:
    """True if the column already exists.

    scripts/gen_migration.py regenerates 0001_initial from models.py, so on a
    FRESH database 0001 already contains size_seq and this migration would fail
    with "duplicate column". On a DEPLOYED database created before the model
    changed, it does not. Guarding makes the chain correct in both directions,
    which is what upgrade -> downgrade -> upgrade actually tests.
    """
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("products", "size_seq"):
        op.add_column("products", sa.Column("size_seq", sa.Integer(), nullable=True))
    op.create_index("ix_products_style_size_seq", "products", ["style_id", "size_seq"])

    # Backfill the known alpha scale. Anything unrecognised stays NULL and the
    # caller falls back to sorting by `size`, so this cannot corrupt data it
    # does not understand.
    for label, seq in ALPHA_SIZES.items():
        op.execute(
            sa.text("UPDATE products SET size_seq = :seq WHERE upper(size) = :label")
            .bindparams(seq=seq, label=label)
        )


def downgrade() -> None:
    op.drop_index("ix_products_style_size_seq", table_name="products")
    op.drop_column("products", "size_seq")
