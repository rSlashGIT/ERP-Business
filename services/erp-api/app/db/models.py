"""
ERP relational schema (SQLAlchemy 2.0 declarative, PostgreSQL 16).

DESIGN RULES ENFORCED HERE
--------------------------
1. Money is NUMERIC(18,4). Never float. A float unit cost compounds into a
   wrong PO total, and procurement will (correctly) stop trusting the system.
2. Quantities are NUMERIC(18,4) too — catch weights, kg, litres are real.
3. Every mutable business row is soft-deleted and audited, never hard-deleted.
   Procurement is a regulated-adjacent domain; "who changed this PO line and
   when" must be answerable years later.
4. Inventory is DERIVED from an append-only stock_movements ledger, with
   inventory_levels as a materialised cache. The ledger is the truth. A
   mutable stock column that drifts from its movement history is the single
   most common ERP data-integrity failure.
5. lead_time_observations is a materialised table, not a view. SmartStock
   reads it on every run and it must be O(1) per (supplier, sku), not a join
   across the full goods-receipt history.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index,
    Integer, Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TenantMixin:
    """Row-level tenant ownership.

    UNIQUENESS RULE FOR TENANT-SCOPED TABLES
    ----------------------------------------
    A UniqueConstraint on a tenant-scoped table must include `tenant_id` IF AND
    ONLY IF its columns are a NATURAL key -- a value the tenant chooses, which
    another tenant may legitimately choose too: location codes, supplier codes,
    PO numbers, idempotency keys, SKUs, barcodes, style codes.

    It must NOT include `tenant_id` when the columns are SURROGATE keys -- UUID
    foreign keys to rows that are themselves tenant-scoped. Two tenants cannot
    share a product_id or a style_id, so (product_id, location_id) is already
    per-tenant and adding tenant_id would be redundant noise that hides the
    real constraint.

    scripts/audit_uniqueness.py enforces exactly this distinction.

    Every table a user can reach through the API carries this. `tenant_id` is
    populated from the SIGNED TOKEN (app/security/core.py:scope_query), never
    from request input -- trusting a client-supplied tenant id is the single
    most common multi-tenant data leak.

    Indexed on every table because every tenant-scoped query filters on it, so
    without the index each one degrades to a full scan as the largest tenant
    grows.
    """

    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'default'"), index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ───────────────────────── enums ─────────────────────────

class LocationType(str, enum.Enum):
    DISTRIBUTION_CENTER = "distribution_center"
    STORE = "store"
    TRANSIT = "transit"
    SUPPLIER = "supplier"


class MovementType(str, enum.Enum):
    RECEIPT = "receipt"
    SALE = "sale"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    ADJUSTMENT = "adjustment"
    RETURN_IN = "return_in"
    RETURN_OUT = "return_out"
    SCRAP = "scrap"


class POStatus(str, enum.Enum):
    DRAFT = "draft"                 # AI-generated, awaiting human review
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class RecommendationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    MODIFIED = "modified"          # human changed the quantity
    REJECTED = "rejected"
    EXPIRED = "expired"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    POSTED = "posted"        # stock moved, receivable created
    PAID = "paid"
    PART_PAID = "part_paid"
    CANCELLED = "cancelled"


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    UPI = "upi"
    CARD = "card"
    BANK = "bank"
    CREDIT = "credit"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


# ───────────────────────── master data ─────────────────────────

class ProductStyle(Base, TenantMixin, TimestampMixin):
    """An apparel style: the thing a buyer names, above the sellable variant.

    WHY A PARENT RATHER THAN SPLITTING Product
    ------------------------------------------
    `products.id` is already the atomic stock-keeping unit -- eight tables key
    off it (inventory_levels, demand_history, stock_movements,
    purchase_order_lines, recommendations, supplier_products,
    lead_time_observations, and inventory's uniqueness on product+location).
    A Product row already IS one size/colour combination: one barcode, one
    stock balance, one demand series, one reorder point.

    Splitting Product into style+variant would repoint all eight foreign keys,
    change what `sku_id` means in the SmartStock contract, and require a data
    migration rather than a DDL one. Adding a parent ABOVE the existing Product
    gets the same model with zero FK churn: "Oxford Shirt" is the style,
    "Oxford Shirt / M / Blue" is the Product that carries stock.

    HSN sits here, not on the variant: every size and colour of one garment
    shares an HSN code. The GST RATE deliberately does not live here -- Indian
    garments are 5% up to Rs 2,500 per piece and 18% above, so the slab depends
    on the transaction value of the individual variant and must be derived at
    billing time, never stored on the master.
    """

    __tablename__ = "product_styles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    style_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(128))
    category: Mapped[Optional[str]] = mapped_column(String(128))
    season: Mapped[Optional[str]] = mapped_column(String(32))
    hsn_code: Mapped[Optional[str]] = mapped_column(String(8))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    variants: Mapped[List["Product"]] = relationship(back_populates="style")

    __table_args__ = (
        # Per-TENANT, not global. Two retailers both using style code
        # "SHIRT-001" is normal and must not collide.
        UniqueConstraint("tenant_id", "style_code", name="uq_style_tenant_code"),
        Index("ix_styles_tenant_active", "tenant_id", "is_active"),
    )


class Product(Base, TenantMixin, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    uom: Mapped[str] = mapped_column(String(16), nullable=False, default="EA")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    shelf_life_days: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # ── apparel variant axis ──
    # Nullable throughout: non-apparel tenants keep using Product exactly as
    # before, and existing rows migrate without a backfill.
    style_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("product_styles.id", ondelete="RESTRICT")
    )
    size: Mapped[Optional[str]] = mapped_column(String(32))
    # Sort key for `size`. Apparel sizes do not sort lexically -- "L" < "M" <
    # "S" < "XL" puts Large first and Small third, which is wrong in every
    # report, size-curve chart and pick list. Nullable because non-apparel
    # products have no size; ordering falls back to `size` when it is NULL.
    size_seq: Mapped[Optional[int]] = mapped_column(Integer)
    colour: Mapped[Optional[str]] = mapped_column(String(64))
    # The scanned code. Nullable because not every line is barcoded, and
    # Postgres permits many NULLs under a UNIQUE constraint.
    barcode: Mapped[Optional[str]] = mapped_column(String(64))

    style: Mapped[Optional["ProductStyle"]] = relationship(back_populates="variants")
    inventory: Mapped[List["InventoryLevel"]] = relationship(back_populates="product")
    supplier_links: Mapped[List["SupplierProduct"]] = relationship(back_populates="product")

    __table_args__ = (
        # WAS UniqueConstraint("sku") -- GLOBAL, so two tenants could not both
        # stock "SHIRT-M-BLU". Same defect class as the unscoped queries in
        # inventory.py: tenant identity omitted from a constraint.
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        # A barcode identifies one variant within one retailer. Globally unique
        # would be wrong -- EAN reuse across unrelated retailers is common, and
        # private-label ranges collide constantly.
        UniqueConstraint("tenant_id", "barcode", name="uq_products_tenant_barcode"),
        # One row per (style, size, colour). style_id already implies the
        # tenant, so this needs no tenant column of its own.
        UniqueConstraint("style_id", "size", "colour", name="uq_products_variant_axis"),
        CheckConstraint("unit_cost >= 0", name="ck_products_cost_nonneg"),
        CheckConstraint("unit_price >= 0", name="ck_products_price_nonneg"),
        Index("ix_products_active_category", "is_active", "category"),
        Index("ix_products_style", "style_id"),
        Index("ix_products_tenant_barcode", "tenant_id", "barcode"),
    )


class Location(Base, TenantMixin, TimestampMixin):
    """A node in the supply network. `parent_id` gives the echelon structure —
    a store's parent is the DC that replenishes it. SmartStock's multi-echelon
    topology is read straight off this self-reference."""

    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[LocationType] = mapped_column(Enum(LocationType, name="location_type"), nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("locations.id"))
    capacity_units: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    parent: Mapped[Optional["Location"]] = relationship(remote_side="Location.id")

    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_locations_tenant_code"),)


class Supplier(Base, TenantMixin, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # Contract lead time is the PRIOR. Actual lead time is measured from
    # goods receipts and stored in lead_time_observations. The gap between
    # the two is one of the most valuable things this system surfaces.
    contract_lead_days: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=7)
    contract_lead_cv: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.35"))
    reliability_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_suppliers_tenant_code"),
        CheckConstraint("contract_lead_days >= 0", name="ck_suppliers_lead_nonneg"),
    )


class SupplierProduct(Base, TenantMixin, TimestampMixin):
    """Sourcing terms. `is_preferred` picks the default supplier per product."""

    __tablename__ = "supplier_products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    supplier_sku: Mapped[Optional[str]] = mapped_column(String(64))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    moq: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    order_multiple: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=1)
    max_order_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    lead_days_override: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    product: Mapped["Product"] = relationship(back_populates="supplier_links")
    supplier: Mapped["Supplier"] = relationship()

    __table_args__ = (
        UniqueConstraint("supplier_id", "product_id", name="uq_supplier_product"),
        CheckConstraint("order_multiple > 0", name="ck_sp_multiple_pos"),
        CheckConstraint("moq >= 0", name="ck_sp_moq_nonneg"),
        Index("ix_sp_preferred", "product_id", "is_preferred"),
    )


# ───────────────────────── inventory ─────────────────────────

class StockMovement(Base, TenantMixin):
    """Append-only ledger. THE source of truth for stock.

    No updated_at, no deleted_at: rows are immutable. A correction is a new
    compensating movement, never an edit. `idempotency_key` makes replayed
    integration messages safe.
    """

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    movement_type: Mapped[MovementType] = mapped_column(Enum(MovementType, name="movement_type"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # signed
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reference_type: Mapped[Optional[str]] = mapped_column(String(32))
    reference_id: Mapped[Optional[str]] = mapped_column(String(64))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_movement_tenant_idempotency"),
        Index("ix_movements_prod_loc_time", "product_id", "location_id", "occurred_at"),
        CheckConstraint("quantity <> 0", name="ck_movement_qty_nonzero"),
    )


class InventoryLevel(Base, TenantMixin, TimestampMixin):
    """Materialised cache of the movement ledger, plus reservation state.

    `on_hand` is rebuilt from stock_movements by a reconciliation job; a
    mismatch is an alert, not something to paper over. `on_order` is derived
    from open PO lines, `reserved` from allocated sales orders.
    """

    __tablename__ = "inventory_levels"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    on_order: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    backorder: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    reorder_point: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    order_up_to: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    safety_stock: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    last_counted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    product: Mapped["Product"] = relationship(back_populates="inventory")
    location: Mapped["Location"] = relationship()

    __table_args__ = (
        UniqueConstraint("product_id", "location_id", name="uq_inventory_product_location"),
        CheckConstraint("on_order >= 0", name="ck_inv_onorder_nonneg"),
        Index("ix_inventory_reorder", "location_id", "reorder_point"),
    )

    @property
    def available(self) -> Decimal:
        return self.on_hand - self.reserved

    @property
    def inventory_position(self) -> Decimal:
        """on_hand + on_order - backorder. THE quantity the (s,S) policy compares
        against the reorder point. Using on_hand alone re-orders stock that is
        already in transit — the bug present in the legacy SmartStock policy."""
        return self.on_hand + self.on_order - self.backorder


class DemandHistory(Base, TenantMixin):
    """Daily demand aggregate per (product, location). Feeds SmartStock.

    Stored rather than computed from sales at query time because SmartStock
    asks for 365 days x 50k SKUs on every nightly run, and that aggregation
    is not something you want to do inside the request.
    """

    __tablename__ = "demand_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    bucket_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    # Demand != sales. If you were out of stock, sales understate demand and a
    # model trained on sales learns to stay out of stock. Censored demand is
    # flagged so the forecaster can correct for it.
    was_stocked_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lost_sales_estimate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    # Fraction of the bucket the SKU was actually available. 1.0 = never out.
    # Partial-day stockouts are the common case and a boolean discards that.
    availability_ratio: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("1.0")
    )
    # De-censored demand estimate, persisted so a historical recommendation can
    # be re-derived exactly without re-running the estimator.
    demand_uncensored: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))

    __table_args__ = (
        UniqueConstraint("product_id", "location_id", "bucket_date", name="uq_demand_bucket"),
        Index("ix_demand_lookup", "product_id", "location_id", "bucket_date"),
    )


# ───────────────────────── procurement ─────────────────────────

class PurchaseOrder(Base, TenantMixin, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    po_number: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    status: Mapped[POStatus] = mapped_column(Enum(POStatus, name="po_status"), nullable=False, default=POStatus.DRAFT)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    expected_delivery_date: Mapped[Optional[date]] = mapped_column(Date)
    ordered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Provenance: which AI run proposed this, and who signed it off.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")  # manual|smartstock
    replenishment_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("replenishment_runs.id"))
    approved_by: Mapped[Optional[str]] = mapped_column(String(128))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    lines: Mapped[List["PurchaseOrderLine"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )
    supplier: Mapped["Supplier"] = relationship()
    location: Mapped["Location"] = relationship()

    __table_args__ = (
        UniqueConstraint("tenant_id", "po_number", name="uq_po_tenant_number"),
        Index("ix_po_status_created", "status", "created_at"),
        Index("ix_po_run", "replenishment_run_id"),
    )


class PurchaseOrderLine(Base, TenantMixin, TimestampMixin):
    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Both numbers are kept forever. The delta between what the AI proposed and
    # what the human ordered IS the training signal for trust calibration, and
    # the report procurement leadership actually asks for.
    ai_recommended_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    received_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    override_reason: Mapped[Optional[str]] = mapped_column(Text)
    ai_rationale: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("purchase_order_id", "line_no", name="uq_po_line_no"),
        CheckConstraint("ordered_qty >= 0", name="ck_pol_qty_nonneg"),
        CheckConstraint("received_qty >= 0", name="ck_pol_recv_nonneg"),
    )


class LeadTimeObservation(Base, TenantMixin):
    """Materialised (supplier, product) lead-time facts.

    Written by a trigger-equivalent in the application layer when a
    GoodsReceipt lands. SmartStock reads this directly; it never joins across
    purchase_orders at request time.
    """

    __tablename__ = "lead_time_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    purchase_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("purchase_orders.id"))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lead_days: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    fill_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_lto_supplier_product", "supplier_id", "product_id", "received_at"),
        CheckConstraint("lead_days >= 0", name="ck_lto_lead_nonneg"),
    )


# ───────────────────────── AI integration ─────────────────────────

class ReplenishmentRun(Base, TenantMixin, TimestampMixin):
    """One nightly SmartStock invocation. Idempotent on (run_date, location)."""

    __tablename__ = "replenishment_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, name="run_status"), nullable=False, default=RunStatus.QUEUED)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="scheduler")
    policy_version: Mapped[Optional[str]] = mapped_column(String(64))
    engine_version: Mapped[Optional[str]] = mapped_column(String(32))
    items_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_recommended: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("tenant_id", "run_date", "triggered_by", name="uq_run_tenant_date_trigger"),
        Index("ix_runs_date", "run_date"),
    )


class Recommendation(Base, TenantMixin, TimestampMixin):
    """One AI-proposed line, before it becomes a PO line.

    Kept separately from PurchaseOrderLine so that rejected and expired advice
    is retained. Measuring how often humans override the model — and in which
    direction — is the only way to know whether it is worth running.
    """

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("replenishment_runs.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("suppliers.id"))
    recommended_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unconstrained_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, name="recommendation_status"),
        nullable=False, default=RecommendationStatus.PENDING,
    )
    final_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    decided_by: Mapped[Optional[str]] = mapped_column(String(128))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    warnings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    purchase_order_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("purchase_order_lines.id"))

    product: Mapped["Product"] = relationship()
    location: Mapped["Location"] = relationship()
    supplier: Mapped[Optional["Supplier"]] = relationship()

    __table_args__ = (
        UniqueConstraint("run_id", "product_id", "location_id", name="uq_reco_run_prod_loc"),
        Index("ix_reco_status_urgency", "status", "urgency"),
        CheckConstraint("recommended_qty >= 0", name="ck_reco_qty_nonneg"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_reco_conf_range"),
    )


class PolicyParameter(Base, TimestampMixin):
    """Versioned SmartStock policy parameters, per segment. Append-only.

    Retained so any historical recommendation can be re-derived exactly. If a
    buyer asks "why did it order 3000 units last March", you need the March
    parameters, not today's."""

    __tablename__ = "policy_parameters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    segment: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_theta: Mapped[dict] = mapped_column(JSONB, nullable=False)
    readable_params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    fit_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("policy_version", "segment", name="uq_policy_version_segment"),
        Index("ix_policy_active", "is_active"),
    )




# ───────────────────────── sales / receivables ─────────────────────────

class Customer(Base, TenantMixin, TimestampMixin):
    """A buyer. Walk-in counter sales use a single 'CASH' customer per tenant.

    Separate from Supplier rather than a shared `parties` table: the two carry
    genuinely different fields (credit limit and ageing vs lead time and
    reliability), and merging them means every query filters on a type
    discriminator forever.
    """

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    gstin: Mapped[Optional[str]] = mapped_column(String(15))
    # State code drives CGST+SGST vs IGST. Held here, not derived from the
    # GSTIN, because unregistered customers have no GSTIN but still have a
    # place of supply.
    state_code: Mapped[Optional[str]] = mapped_column(String(2))
    address: Mapped[Optional[str]] = mapped_column(Text)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    credit_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_walkin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_customers_tenant_code"),
        CheckConstraint("credit_limit >= 0", name="ck_customers_credit_nonneg"),
        Index("ix_customers_tenant_active", "tenant_id", "is_active"),
    )


class SalesInvoice(Base, TenantMixin, TimestampMixin):
    """A GST tax invoice. Posting one decrements stock through the ledger.

    Tax is stored as computed AMOUNTS, never as a rate on the header: the slab
    is a per-line property (see app/domain/gst.py), and an apparel invoice
    routinely mixes 5% and 18% lines when some garments cross Rs 2,500.
    """

    __tablename__ = "sales_invoices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"), nullable=False, default=InvoiceStatus.DRAFT
    )
    place_of_supply: Mapped[Optional[str]] = mapped_column(String(2))
    is_interstate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    igst_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    round_off: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(128))

    lines: Mapped[List["SalesInvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    customer: Mapped["Customer"] = relationship()

    @property
    def balance_due(self) -> Decimal:
        return self.grand_total - self.amount_paid

    __table_args__ = (
        UniqueConstraint("tenant_id", "invoice_number", name="uq_invoice_tenant_number"),
        CheckConstraint("grand_total >= 0", name="ck_invoice_total_nonneg"),
        CheckConstraint("amount_paid >= 0", name="ck_invoice_paid_nonneg"),
        Index("ix_invoices_tenant_date", "tenant_id", "invoice_date"),
        Index("ix_invoices_customer", "customer_id", "status"),
    )


class SalesInvoiceLine(Base, TenantMixin, TimestampMixin):
    __tablename__ = "sales_invoice_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False, default=0)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    # The slab APPLIED to this line, recorded for audit. It is derived at
    # billing time from the per-piece taxable value, never read off the master.
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    cgst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    sgst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    igst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    hsn_code: Mapped[Optional[str]] = mapped_column(String(8))

    invoice: Mapped["SalesInvoice"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no", name="uq_invoice_line_no"),
        CheckConstraint("quantity > 0", name="ck_invline_qty_positive"),
        CheckConstraint("unit_price >= 0", name="ck_invline_price_nonneg"),
        Index("ix_invline_product", "product_id"),
    )


class Payment(Base, TenantMixin, TimestampMixin):
    """Money received from a customer. Allocated across invoices."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_number: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method"), nullable=False, default=PaymentMethod.CASH
    )
    reference: Mapped[Optional[str]] = mapped_column(String(64))
    received_by: Mapped[Optional[str]] = mapped_column(String(128))

    allocations: Mapped[List["PaymentAllocation"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "payment_number", name="uq_payment_tenant_number"),
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        Index("ix_payments_customer_date", "customer_id", "payment_date"),
    )


class PaymentAllocation(Base, TenantMixin, TimestampMixin):
    """How much of a payment settles which invoice.

    Separate table rather than a column on the payment: one payment routinely
    clears several invoices, and one invoice is often settled in instalments.
    """

    __tablename__ = "payment_allocations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    payment: Mapped["Payment"] = relationship(back_populates="allocations")

    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id", name="uq_alloc_payment_invoice"),
        CheckConstraint("amount > 0", name="ck_alloc_amount_positive"),
        Index("ix_alloc_invoice", "invoice_id"),
    )


class AuditLog(Base, TenantMixin):
    """Append-only audit trail.

    Tenant-scoped because an audit row IS tenant data: who adjusted whose stock
    is exactly the sort of thing one tenant must not read about another. Without
    tenant_id, `scope_query` refuses this model (by design) and the audit write
    in api/v1/inventory.py could not be made tenant-correct.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    before: Mapped[Optional[dict]] = mapped_column(JSONB)
    after: Mapped[Optional[dict]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "occurred_at"),)


class ReturnCondition(str, enum.Enum):
    """Whether a returned garment can be sold again.

    Kept explicit rather than a boolean because the reason drives two different
    things: whether stock goes back up, and what the shop can claim from the
    supplier for a manufacturing fault.
    """
    RESALEABLE = "resaleable"
    DAMAGED = "damaged"
    SOILED = "soiled"
    FAULTY = "faulty"


class RefundMode(str, enum.Enum):
    CREDIT = "credit"               # reduces what the customer owes
    REFUND = "refund"               # money leaves the till


class GoodsReceipt(Base, TenantMixin, TimestampMixin):
    """A delivery, booked in.

    Its own document rather than a flag on the purchase order, because partial
    deliveries are the norm — a supplier sends 40 of 60 and the rest next week,
    and each arrival has its own date, its own supplier invoice and its own
    prices.
    """
    __tablename__ = "goods_receipts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    grn_number: Mapped[str] = mapped_column(String(32), nullable=False)
    purchase_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("purchase_orders.id")
    )
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("suppliers.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    supplier_invoice: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    received_by: Mapped[Optional[str]] = mapped_column(String(128))

    lines: Mapped[List["GoodsReceiptLine"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "grn_number", name="uq_grn_tenant_number"),
        Index("ix_grn_tenant_date", "tenant_id", "received_date"),
    )


class GoodsReceiptLine(Base, TenantMixin, TimestampMixin):
    __tablename__ = "goods_receipt_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False
    )
    purchase_order_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("purchase_order_lines.id")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Accepted and rejected are separate columns, not one signed number: only
    # accepted stock exists and only accepted stock is paid for, while rejected
    # is the shop's claim against the supplier.
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    rejected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)

    receipt: Mapped["GoodsReceipt"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("goods_receipt_id", "line_no", name="uq_grn_line_no"),
        CheckConstraint("accepted_qty >= 0", name="ck_grn_accepted_non_negative"),
        CheckConstraint("rejected_qty >= 0", name="ck_grn_rejected_non_negative"),
    )


class SupplierBill(Base, TenantMixin, TimestampMixin):
    __tablename__ = "supplier_bills"

    id: Mapped[uuid.UUID] = _uuid_pk()
    bill_number: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"), nullable=False, default=InvoiceStatus.POSTED
    )
    goods_receipt_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("goods_receipts.id"))
    supplier_invoice_number: Mapped[Optional[str]] = mapped_column(String(64))

    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    round_off: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(128))

    lines: Mapped[List["SupplierBillLine"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )
    supplier: Mapped["Supplier"] = relationship()
    goods_receipt: Mapped[Optional["GoodsReceipt"]] = relationship()

    @property
    def balance_due(self) -> Decimal:
        return self.grand_total - self.amount_paid

    __table_args__ = (
        UniqueConstraint("tenant_id", "bill_number", name="uq_sb_tenant_number"),
        CheckConstraint("grand_total >= 0", name="ck_sb_total_nonneg"),
        CheckConstraint("amount_paid >= 0", name="ck_sb_paid_nonneg"),
        Index("ix_sb_tenant_date", "tenant_id", "bill_date"),
        Index("ix_sb_supplier", "supplier_id", "status"),
    )


class SupplierBillLine(Base, TenantMixin, TimestampMixin):
    __tablename__ = "supplier_bill_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_bills.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    cgst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    sgst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    igst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    hsn_code: Mapped[Optional[str]] = mapped_column(String(8))

    bill: Mapped["SupplierBill"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("bill_id", "line_no", name="uq_sb_line_no"),
        CheckConstraint("quantity > 0", name="ck_sbl_qty_positive"),
        CheckConstraint("unit_price >= 0", name="ck_sbl_price_nonneg"),
    )


class SupplierPayment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "supplier_payments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_number: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method"), nullable=False, default=PaymentMethod.BANK
    )
    reference: Mapped[Optional[str]] = mapped_column(String(64))
    paid_by: Mapped[Optional[str]] = mapped_column(String(128))

    allocations: Mapped[List["SupplierPaymentAllocation"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "payment_number", name="uq_sp_tenant_number"),
        CheckConstraint("amount > 0", name="ck_sp_amount_positive"),
        Index("ix_sp_supplier_date", "supplier_id", "payment_date"),
    )


class SupplierPaymentAllocation(Base, TenantMixin, TimestampMixin):
    __tablename__ = "supplier_payment_allocations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_payments.id", ondelete="CASCADE"), nullable=False
    )
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_bills.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    payment: Mapped["SupplierPayment"] = relationship(back_populates="allocations")

    __table_args__ = (
        UniqueConstraint("payment_id", "bill_id", name="uq_spa_payment_bill"),
        CheckConstraint("amount > 0", name="ck_spa_amount_positive"),
    )


class CreditNote(Base, TenantMixin, TimestampMixin):
    """A sale reversed, in part or in full.

    Always points at the invoice it reverses. Tax is stored as amounts, exactly
    as on the invoice, because one credit note can span two GST slabs.
    """
    __tablename__ = "credit_notes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cn_number: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)

    taxable_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    igst_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    round_off: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    refund_mode: Mapped[RefundMode] = mapped_column(
        SAEnum(RefundMode, name="refund_mode"), nullable=False, default=RefundMode.CREDIT
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(128))

    lines: Mapped[List["CreditNoteLine"]] = relationship(
        back_populates="credit_note", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "cn_number", name="uq_cn_tenant_number"),
        Index("ix_cn_tenant_date", "tenant_id", "note_date"),
    )


class CreditNoteLine(Base, TenantMixin, TimestampMixin):
    """One returned line, pinned to the invoice line it reverses.

    `invoice_line_id` is not a convenience — it is what makes the GST correct.
    The rate is copied from that line, so a garment sold at 18% is refunded at
    18% even if the shop has since marked it down under the Rs 2,500 boundary
    and it would be a 5% line today.
    """
    __tablename__ = "credit_note_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    credit_note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credit_notes.id", ondelete="CASCADE"), nullable=False
    )
    invoice_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoice_lines.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False, default=0)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False, default=0)
    cgst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    sgst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    igst: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    restock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    condition: Mapped[ReturnCondition] = mapped_column(
        SAEnum(ReturnCondition, name="return_condition"),
        nullable=False, default=ReturnCondition.RESALEABLE,
    )

    credit_note: Mapped["CreditNote"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("credit_note_id", "line_no", name="uq_cn_line_no"),
        CheckConstraint("quantity > 0", name="ck_cn_qty_positive"),
        Index("ix_cnl_invoice_line", "invoice_line_id"),
    )

# ───────────────────────── stocktakes & transfers ─────────────────────────

class Stocktake(Base, TenantMixin, TimestampMixin):
    __tablename__ = "stocktakes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    stocktake_number: Mapped[str] = mapped_column(String(64), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    lines: Mapped[List["StocktakeLine"]] = relationship(
        back_populates="stocktake", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "stocktake_number", name="uq_stocktake_tenant_number"),
    )


class StocktakeLine(Base, TenantMixin):
    __tablename__ = "stocktake_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    stocktake_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stocktakes.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    expected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    counted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    variance_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    stocktake: Mapped["Stocktake"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("stocktake_id", "product_id", name="uq_stocktake_line_product"),
    )


class StockTransfer(Base, TenantMixin, TimestampMixin):
    __tablename__ = "stock_transfers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    transfer_number: Mapped[str] = mapped_column(String(64), nullable=False)
    from_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    to_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    lines: Mapped[List["StockTransferLine"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "transfer_number", name="uq_transfer_tenant_number"),
    )


class StockTransferLine(Base, TenantMixin):
    __tablename__ = "stock_transfer_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    transfer: Mapped["StockTransfer"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        UniqueConstraint("transfer_id", "product_id", name="uq_transfer_line_product"),
        CheckConstraint("quantity > 0", name="ck_transfer_qty_positive"),
    )
