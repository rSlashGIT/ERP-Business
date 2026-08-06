"""initial schema

Generated from services/erp-api/app/db/models.py by scripts/gen_migration.py.
Covers every table, column, enum type, index, unique and check constraint.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Postgres enum types. Created explicitly so downgrade can drop them --
# SQLAlchemy will not clean these up on its own and a re-run then fails
# with "type already exists", which is the classic Alembic footgun.
ENUMS = {
    "location_type": ['distribution_center', 'store', 'transit', 'supplier'],
    "movement_type": ['receipt', 'sale', 'transfer_in', 'transfer_out', 'adjustment', 'return_in', 'return_out', 'scrap'],
    "po_status": ['draft', 'pending_approval', 'approved', 'sent', 'partially_received', 'received', 'cancelled', 'rejected'],
    "run_status": ['queued', 'running', 'succeeded', 'failed', 'partial'],
    "recommendation_status": ['pending', 'approved', 'modified', 'rejected', 'expired'],
    "invoice_status": ['draft', 'posted', 'paid', 'part_paid', 'cancelled'],
    "payment_method": ['cash', 'upi', 'card', 'bank', 'credit'],
    "refund_mode": ['credit', 'refund'],
    "return_condition": ['resaleable', 'damaged', 'soiled', 'faulty'],
}


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        for name, values in ENUMS.items():
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "product_styles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("style_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("season", sa.String(length=32), nullable=True),
        sa.Column("hsn_code", sa.String(length=8), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "style_code", name="uq_style_tenant_code"),
    )
    op.create_index("ix_product_styles_tenant_id", "product_styles", ["tenant_id"])
    op.create_index("ix_styles_tenant_active", "product_styles", ["tenant_id", "is_active"])

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("uom", sa.String(length=16), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("shelf_life_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("product_styles.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("size", sa.String(length=32), nullable=True),
        sa.Column("size_seq", sa.Integer(), nullable=True),
        sa.Column("colour", sa.String(length=64), nullable=True),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        sa.UniqueConstraint("tenant_id", "barcode", name="uq_products_tenant_barcode"),
        sa.UniqueConstraint("style_id", "size", "colour", name="uq_products_variant_axis"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_products_cost_nonneg"),
        sa.CheckConstraint("unit_price >= 0", name="ck_products_price_nonneg"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_index("ix_products_active_category", "products", ["is_active", "category"])
    op.create_index("ix_products_style", "products", ["style_id"])
    op.create_index("ix_products_tenant_barcode", "products", ["tenant_id", "barcode"])

    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", postgresql.ENUM(name="location_type", create_type=False), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=True),
        sa.Column("capacity_units", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "code", name="uq_locations_tenant_code"),
    )
    op.create_index("ix_locations_tenant_id", "locations", ["tenant_id"])

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("gstin", sa.String(length=15), nullable=True),
        sa.Column("state_code", sa.String(length=2), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("credit_limit", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("credit_days", sa.Integer(), nullable=False),
        sa.Column("is_walkin", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "code", name="uq_customers_tenant_code"),
        sa.CheckConstraint("credit_limit >= 0", name="ck_customers_credit_nonneg"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_index("ix_customers_tenant_active", "customers", ["tenant_id", "is_active"])

    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("contract_lead_days", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("contract_lead_cv", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("reliability_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "code", name="uq_suppliers_tenant_code"),
        sa.CheckConstraint("contract_lead_days >= 0", name="ck_suppliers_lead_nonneg"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])

    op.create_table(
        "supplier_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_sku", sa.String(length=64), nullable=True),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("moq", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("order_multiple", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("max_order_qty", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("lead_days_override", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("supplier_id", "product_id", name="uq_supplier_product"),
        sa.CheckConstraint("order_multiple > 0", name="ck_sp_multiple_pos"),
        sa.CheckConstraint("moq >= 0", name="ck_sp_moq_nonneg"),
    )
    op.create_index("ix_supplier_products_tenant_id", "supplier_products", ["tenant_id"])
    op.create_index("ix_sp_preferred", "supplier_products", ["product_id", "is_preferred"])

    op.create_table(
        "replenishment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("status", postgresql.ENUM(name="run_status", create_type=False), nullable=False),
        sa.Column("triggered_by", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=True),
        sa.Column("engine_version", sa.String(length=32), nullable=True),
        sa.Column("items_sent", sa.Integer(), nullable=False),
        sa.Column("lines_recommended", sa.Integer(), nullable=False),
        sa.Column("total_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "run_date", "triggered_by", name="uq_run_tenant_date_trigger"),
    )
    op.create_index("ix_replenishment_runs_tenant_id", "replenishment_runs", ["tenant_id"])
    op.create_index("ix_runs_date", "replenishment_runs", ["run_date"])

    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("po_number", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("status", postgresql.ENUM(name="po_status", create_type=False), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("replenishment_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("replenishment_runs.id"), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "po_number", name="uq_po_tenant_number"),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])
    op.create_index("ix_po_status_created", "purchase_orders", ["status", "created_at"])
    op.create_index("ix_po_run", "purchase_orders", ["replenishment_run_id"])

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("ai_recommended_qty", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("ordered_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("received_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("ai_rationale", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("purchase_order_id", "line_no", name="uq_po_line_no"),
        sa.CheckConstraint("ordered_qty >= 0", name="ck_pol_qty_nonneg"),
        sa.CheckConstraint("received_qty >= 0", name="ck_pol_recv_nonneg"),
    )
    op.create_index("ix_purchase_order_lines_tenant_id", "purchase_order_lines", ["tenant_id"])

    op.create_table(
        "inventory_levels",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("on_hand", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("on_order", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("reserved", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("backorder", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("reorder_point", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("order_up_to", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("safety_stock", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("last_counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("product_id", "location_id", name="uq_inventory_product_location"),
        sa.CheckConstraint("on_order >= 0", name="ck_inv_onorder_nonneg"),
    )
    op.create_index("ix_inventory_levels_tenant_id", "inventory_levels", ["tenant_id"])
    op.create_index("ix_inventory_reorder", "inventory_levels", ["location_id", "reorder_point"])

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("movement_type", postgresql.ENUM(name="movement_type", create_type=False), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_type", sa.String(length=32), nullable=True),
        sa.Column("reference_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_movement_tenant_idempotency"),
        sa.CheckConstraint("quantity <> 0", name="ck_movement_qty_nonzero"),
    )
    op.create_index("ix_stock_movements_tenant_id", "stock_movements", ["tenant_id"])
    op.create_index("ix_movements_prod_loc_time", "stock_movements", ["product_id", "location_id", "occurred_at"])

    op.create_table(
        "demand_history",
        sa.Column("id", sa.BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("was_stocked_out", sa.Boolean(), nullable=False),
        sa.Column("lost_sales_estimate", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("availability_ratio", sa.Numeric(precision=5, scale=4), nullable=False, server_default=sa.text("1.0")),
        sa.Column("demand_uncensored", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("product_id", "location_id", "bucket_date", name="uq_demand_bucket"),
    )
    op.create_index("ix_demand_history_tenant_id", "demand_history", ["tenant_id"])
    op.create_index("ix_demand_lookup", "demand_history", ["product_id", "location_id", "bucket_date"])

    op.create_table(
        "goods_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("grn_number", sa.String(length=32), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("supplier_invoice", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("total_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("received_by", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "grn_number", name="uq_grn_tenant_number"),
    )
    op.create_index("ix_goods_receipts_tenant_id", "goods_receipts", ["tenant_id"])
    op.create_index("ix_grn_tenant_date", "goods_receipts", ["tenant_id", "received_date"])

    op.create_table(
        "lead_time_observations",
        sa.Column("id", sa.BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_days", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("fill_ratio", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.CheckConstraint("lead_days >= 0", name="ck_lto_lead_nonneg"),
    )
    op.create_index("ix_lead_time_observations_tenant_id", "lead_time_observations", ["tenant_id"])
    op.create_index("ix_lto_supplier_product", "lead_time_observations", ["supplier_id", "product_id", "received_at"])

    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("replenishment_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("recommended_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unconstrained_qty", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("status", postgresql.ENUM(name="recommendation_status", create_type=False), nullable=False),
        sa.Column("final_qty", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("rationale", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("purchase_order_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_order_lines.id"), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "product_id", "location_id", name="uq_reco_run_prod_loc"),
        sa.CheckConstraint("recommended_qty >= 0", name="ck_reco_qty_nonneg"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_reco_conf_range"),
    )
    op.create_index("ix_recommendations_tenant_id", "recommendations", ["tenant_id"])
    op.create_index("ix_reco_status_urgency", "recommendations", ["status", "urgency"])

    op.create_table(
        "sales_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("invoice_number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", postgresql.ENUM(name="invoice_status", create_type=False), nullable=False),
        sa.Column("place_of_supply", sa.String(length=2), nullable=True),
        sa.Column("is_interstate", sa.Boolean(), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("discount_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("taxable_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("cgst_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("sgst_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("igst_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("round_off", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("grand_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("amount_paid", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "invoice_number", name="uq_invoice_tenant_number"),
        sa.CheckConstraint("grand_total >= 0", name="ck_invoice_total_nonneg"),
        sa.CheckConstraint("amount_paid >= 0", name="ck_invoice_paid_nonneg"),
    )
    op.create_index("ix_sales_invoices_tenant_id", "sales_invoices", ["tenant_id"])
    op.create_index("ix_invoices_tenant_date", "sales_invoices", ["tenant_id", "invoice_date"])
    op.create_index("ix_invoices_customer", "sales_invoices", ["customer_id", "status"])

    op.create_table(
        "sales_invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("taxable_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("gst_rate", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("cgst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("sgst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("igst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("hsn_code", sa.String(length=8), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("invoice_id", "line_no", name="uq_invoice_line_no"),
        sa.CheckConstraint("quantity > 0", name="ck_invline_qty_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_invline_price_nonneg"),
    )
    op.create_index("ix_sales_invoice_lines_tenant_id", "sales_invoice_lines", ["tenant_id"])
    op.create_index("ix_invline_product", "sales_invoice_lines", ["product_id"])

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("payment_number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("method", postgresql.ENUM(name="payment_method", create_type=False), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=True),
        sa.Column("received_by", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "payment_number", name="uq_payment_tenant_number"),
        sa.CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_customer_date", "payments", ["customer_id", "payment_date"])

    op.create_table(
        "payment_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("payment_id", "invoice_id", name="uq_alloc_payment_invoice"),
        sa.CheckConstraint("amount > 0", name="ck_alloc_amount_positive"),
    )
    op.create_index("ix_payment_allocations_tenant_id", "payment_allocations", ["tenant_id"])
    op.create_index("ix_alloc_invoice", "payment_allocations", ["invoice_id"])

    op.create_table(
        "policy_parameters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("segment", sa.String(length=64), nullable=False),
        sa.Column("raw_theta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("readable_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fit_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("policy_version", "segment", name="uq_policy_version_segment"),
    )
    op.create_index("ix_policy_active", "policy_parameters", ["is_active"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_id", "occurred_at"])

    op.create_table(
        "goods_receipt_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("goods_receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purchase_order_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_order_lines.id"), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("accepted_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("rejected_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("goods_receipt_id", "line_no", name="uq_grn_line_no"),
        sa.CheckConstraint("accepted_qty >= 0", name="ck_grn_accepted_non_negative"),
        sa.CheckConstraint("rejected_qty >= 0", name="ck_grn_rejected_non_negative"),
    )
    op.create_index("ix_goods_receipt_lines_tenant_id", "goods_receipt_lines", ["tenant_id"])

    op.create_table(
        "supplier_bills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("bill_number", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", postgresql.ENUM(name="invoice_status", create_type=False), nullable=False),
        sa.Column("goods_receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goods_receipts.id"), nullable=True),
        sa.Column("supplier_invoice_number", sa.String(length=64), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tax_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("round_off", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("grand_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("amount_paid", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "bill_number", name="uq_sb_tenant_number"),
        sa.CheckConstraint("grand_total >= 0", name="ck_sb_total_nonneg"),
        sa.CheckConstraint("amount_paid >= 0", name="ck_sb_paid_nonneg"),
    )
    op.create_index("ix_supplier_bills_tenant_id", "supplier_bills", ["tenant_id"])
    op.create_index("ix_sb_tenant_date", "supplier_bills", ["tenant_id", "bill_date"])
    op.create_index("ix_sb_supplier", "supplier_bills", ["supplier_id", "status"])

    op.create_table(
        "supplier_bill_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("bill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("supplier_bills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("taxable_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("gst_rate", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("cgst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("sgst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("igst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("hsn_code", sa.String(length=8), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("bill_id", "line_no", name="uq_sb_line_no"),
        sa.CheckConstraint("quantity > 0", name="ck_sbl_qty_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_sbl_price_nonneg"),
    )
    op.create_index("ix_supplier_bill_lines_tenant_id", "supplier_bill_lines", ["tenant_id"])

    op.create_table(
        "supplier_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("payment_number", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("method", postgresql.ENUM(name="payment_method", create_type=False), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=True),
        sa.Column("paid_by", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "payment_number", name="uq_sp_tenant_number"),
        sa.CheckConstraint("amount > 0", name="ck_sp_amount_positive"),
    )
    op.create_index("ix_supplier_payments_tenant_id", "supplier_payments", ["tenant_id"])
    op.create_index("ix_sp_supplier_date", "supplier_payments", ["supplier_id", "payment_date"])

    op.create_table(
        "supplier_payment_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("supplier_payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("supplier_bills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("payment_id", "bill_id", name="uq_spa_payment_bill"),
        sa.CheckConstraint("amount > 0", name="ck_spa_amount_positive"),
    )
    op.create_index("ix_supplier_payment_allocations_tenant_id", "supplier_payment_allocations", ["tenant_id"])

    op.create_table(
        "credit_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("cn_number", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_invoices.id"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("note_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("taxable_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("cgst_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("sgst_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("igst_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("round_off", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("grand_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("refund_mode", postgresql.ENUM(name="refund_mode", create_type=False), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "cn_number", name="uq_cn_tenant_number"),
    )
    op.create_index("ix_credit_notes_tenant_id", "credit_notes", ["tenant_id"])
    op.create_index("ix_cn_tenant_date", "credit_notes", ["tenant_id", "note_date"])

    op.create_table(
        "credit_note_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("credit_note_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("credit_notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_invoice_lines.id"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column("taxable_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("gst_rate", sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column("cgst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("sgst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("igst", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("restock", sa.Boolean(), nullable=False),
        sa.Column("condition", postgresql.ENUM(name="return_condition", create_type=False), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("credit_note_id", "line_no", name="uq_cn_line_no"),
        sa.CheckConstraint("quantity > 0", name="ck_cn_qty_positive"),
    )
    op.create_index("ix_credit_note_lines_tenant_id", "credit_note_lines", ["tenant_id"])
    op.create_index("ix_cnl_invoice_line", "credit_note_lines", ["invoice_line_id"])

    op.create_table(
        "stocktakes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("stocktake_number", sa.String(length=64), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "stocktake_number", name="uq_stocktake_tenant_number"),
    )
    op.create_index("ix_stocktakes_tenant_id", "stocktakes", ["tenant_id"])

    op.create_table(
        "stocktake_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("stocktake_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stocktakes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("expected_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("counted_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("variance_qty", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("stocktake_id", "product_id", name="uq_stocktake_line_product"),
    )
    op.create_index("ix_stocktake_lines_tenant_id", "stocktake_lines", ["tenant_id"])

    op.create_table(
        "stock_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("transfer_number", sa.String(length=64), nullable=False),
        sa.Column("from_location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("to_location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "transfer_number", name="uq_transfer_tenant_number"),
    )
    op.create_index("ix_stock_transfers_tenant_id", "stock_transfers", ["tenant_id"])

    op.create_table(
        "stock_transfer_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("transfer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("transfer_id", "product_id", name="uq_transfer_line_product"),
        sa.CheckConstraint("quantity > 0", name="ck_transfer_qty_positive"),
    )
    op.create_index("ix_stock_transfer_lines_tenant_id", "stock_transfer_lines", ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_stock_transfer_lines_tenant_id", table_name="stock_transfer_lines")
    op.drop_table("stock_transfer_lines")
    op.drop_index("ix_stock_transfers_tenant_id", table_name="stock_transfers")
    op.drop_table("stock_transfers")
    op.drop_index("ix_stocktake_lines_tenant_id", table_name="stocktake_lines")
    op.drop_table("stocktake_lines")
    op.drop_index("ix_stocktakes_tenant_id", table_name="stocktakes")
    op.drop_table("stocktakes")
    op.drop_index("ix_cnl_invoice_line", table_name="credit_note_lines")
    op.drop_index("ix_credit_note_lines_tenant_id", table_name="credit_note_lines")
    op.drop_table("credit_note_lines")
    op.drop_index("ix_cn_tenant_date", table_name="credit_notes")
    op.drop_index("ix_credit_notes_tenant_id", table_name="credit_notes")
    op.drop_table("credit_notes")
    op.drop_index("ix_supplier_payment_allocations_tenant_id", table_name="supplier_payment_allocations")
    op.drop_table("supplier_payment_allocations")
    op.drop_index("ix_sp_supplier_date", table_name="supplier_payments")
    op.drop_index("ix_supplier_payments_tenant_id", table_name="supplier_payments")
    op.drop_table("supplier_payments")
    op.drop_index("ix_supplier_bill_lines_tenant_id", table_name="supplier_bill_lines")
    op.drop_table("supplier_bill_lines")
    op.drop_index("ix_sb_tenant_date", table_name="supplier_bills")
    op.drop_index("ix_sb_supplier", table_name="supplier_bills")
    op.drop_index("ix_supplier_bills_tenant_id", table_name="supplier_bills")
    op.drop_table("supplier_bills")
    op.drop_index("ix_goods_receipt_lines_tenant_id", table_name="goods_receipt_lines")
    op.drop_table("goods_receipt_lines")
    op.drop_index("ix_audit_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_policy_active", table_name="policy_parameters")
    op.drop_table("policy_parameters")
    op.drop_index("ix_alloc_invoice", table_name="payment_allocations")
    op.drop_index("ix_payment_allocations_tenant_id", table_name="payment_allocations")
    op.drop_table("payment_allocations")
    op.drop_index("ix_payments_customer_date", table_name="payments")
    op.drop_index("ix_payments_tenant_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_invline_product", table_name="sales_invoice_lines")
    op.drop_index("ix_sales_invoice_lines_tenant_id", table_name="sales_invoice_lines")
    op.drop_table("sales_invoice_lines")
    op.drop_index("ix_invoices_tenant_date", table_name="sales_invoices")
    op.drop_index("ix_invoices_customer", table_name="sales_invoices")
    op.drop_index("ix_sales_invoices_tenant_id", table_name="sales_invoices")
    op.drop_table("sales_invoices")
    op.drop_index("ix_reco_status_urgency", table_name="recommendations")
    op.drop_index("ix_recommendations_tenant_id", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index("ix_lto_supplier_product", table_name="lead_time_observations")
    op.drop_index("ix_lead_time_observations_tenant_id", table_name="lead_time_observations")
    op.drop_table("lead_time_observations")
    op.drop_index("ix_grn_tenant_date", table_name="goods_receipts")
    op.drop_index("ix_goods_receipts_tenant_id", table_name="goods_receipts")
    op.drop_table("goods_receipts")
    op.drop_index("ix_demand_lookup", table_name="demand_history")
    op.drop_index("ix_demand_history_tenant_id", table_name="demand_history")
    op.drop_table("demand_history")
    op.drop_index("ix_movements_prod_loc_time", table_name="stock_movements")
    op.drop_index("ix_stock_movements_tenant_id", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("ix_inventory_reorder", table_name="inventory_levels")
    op.drop_index("ix_inventory_levels_tenant_id", table_name="inventory_levels")
    op.drop_table("inventory_levels")
    op.drop_index("ix_purchase_order_lines_tenant_id", table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")
    op.drop_index("ix_po_status_created", table_name="purchase_orders")
    op.drop_index("ix_po_run", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_tenant_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index("ix_runs_date", table_name="replenishment_runs")
    op.drop_index("ix_replenishment_runs_tenant_id", table_name="replenishment_runs")
    op.drop_table("replenishment_runs")
    op.drop_index("ix_sp_preferred", table_name="supplier_products")
    op.drop_index("ix_supplier_products_tenant_id", table_name="supplier_products")
    op.drop_table("supplier_products")
    op.drop_index("ix_suppliers_tenant_id", table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_index("ix_customers_tenant_active", table_name="customers")
    op.drop_index("ix_customers_tenant_id", table_name="customers")
    op.drop_table("customers")
    op.drop_index("ix_locations_tenant_id", table_name="locations")
    op.drop_table("locations")
    op.drop_index("ix_products_active_category", table_name="products")
    op.drop_index("ix_products_style", table_name="products")
    op.drop_index("ix_products_tenant_barcode", table_name="products")
    op.drop_index("ix_products_tenant_id", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_styles_tenant_active", table_name="product_styles")
    op.drop_index("ix_product_styles_tenant_id", table_name="product_styles")
    op.drop_table("product_styles")
    if bind.dialect.name == "postgresql":
        for name in ENUMS:
            postgresql.ENUM(name=name).drop(bind, checkfirst=True)
