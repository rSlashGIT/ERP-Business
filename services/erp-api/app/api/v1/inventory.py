"""Inventory read + adjustment routes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db.models import (
    AuditLog, InventoryLevel, Location, MovementType, Product, ProductStyle,
    StockMovement,
)
from ...db.session import get_session
from ...security.core import Principal, scope_query
from ...security.deps import current_principal

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


class AdjustmentRequest(BaseModel):
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal = Field(description="Signed delta. Negative writes stock off.")
    reason: str = Field(min_length=3, max_length=255)
    idempotency_key: Optional[str] = None
    # `actor` removed: it now comes from the bearer token. A client-supplied
    # actor makes every audit row unverifiable.


@router.get("")
async def list_inventory(
    location_code: Optional[str] = None,
    below_reorder: bool = False,
    search: Optional[str] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Tenant-scoped inventory list.

    There is deliberately no tenant parameter: the value comes from the signed
    token. Before this was added the endpoint returned every tenant's stock to
    any caller.
    """
    stmt = (
        select(InventoryLevel)
        .options(selectinload(InventoryLevel.product), selectinload(InventoryLevel.location))
        .join(Product).join(Location)
        .where(Product.is_active.is_(True), Product.deleted_at.is_(None))
    )
    stmt = scope_query(stmt, InventoryLevel, principal)
    if location_code:
        stmt = stmt.where(Location.code == location_code)
    if below_reorder:
        stmt = stmt.where(
            InventoryLevel.reorder_point.isnot(None),
            InventoryLevel.on_hand + InventoryLevel.on_order <= InventoryLevel.reorder_point,
        )
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Product.sku.ilike(like) | Product.name.ilike(like))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return {
        "total": total, "limit": limit, "offset": offset,
        "items": [
            {
                "product_id": str(i.product_id), "sku": i.product.sku, "name": i.product.name,
                "location_code": i.location.code, "on_hand": float(i.on_hand),
                "on_order": float(i.on_order), "reserved": float(i.reserved),
                "backorder": float(i.backorder),
                "available": float(i.available), "inventory_position": float(i.inventory_position),
                "reorder_point": float(i.reorder_point) if i.reorder_point is not None else None,
                "order_up_to": float(i.order_up_to) if i.order_up_to is not None else None,
                "safety_stock": float(i.safety_stock) if i.safety_stock is not None else None,
                "unit_cost": float(i.product.unit_cost),
                "stock_value": float(i.on_hand * i.product.unit_cost),
                "below_reorder": (
                    i.reorder_point is not None
                    and (i.on_hand + i.on_order) <= i.reorder_point
                ),
            }
            for i in rows
        ],
    }


@router.post("/adjustments")
async def adjust(
    body: AdjustmentRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Write an adjustment to the ledger and update the cache in one transaction.

    The ledger row is written FIRST. If the cache update fails the transaction
    rolls back both, so the two can never disagree as a result of this path.
    """
    if body.quantity == 0:
        raise HTTPException(422, "quantity must be non-zero")

    actor = principal.subject

    if body.idempotency_key:
        dup = (
            await session.execute(
                scope_query(select(StockMovement.id), StockMovement, principal).where(
                    StockMovement.idempotency_key == body.idempotency_key
                )
            )
        ).scalar_one_or_none()
        if dup:
            return {"status": "duplicate_ignored", "movement_id": dup}

    inv = (
        await session.execute(
            scope_query(select(InventoryLevel), InventoryLevel, principal).where(
                InventoryLevel.product_id == body.product_id,
                InventoryLevel.location_id == body.location_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if inv is None:
        # Either genuinely absent or owned by another tenant. Identical response
        # either way -- distinguishing them leaks the existence of other
        # tenants' records.
        raise HTTPException(404, "no inventory row for that product/location")

    new_on_hand = inv.on_hand + body.quantity
    if new_on_hand < 0:
        raise HTTPException(
            422,
            f"adjustment would drive on_hand negative ({inv.on_hand} {body.quantity:+})",
        )

    mv = StockMovement(
        tenant_id=principal.tenant_id,
        product_id=body.product_id, location_id=body.location_id,
        movement_type=MovementType.ADJUSTMENT, quantity=body.quantity,
        occurred_at=datetime.now(timezone.utc), reference_type="adjustment",
        reference_id=body.reason[:64], idempotency_key=body.idempotency_key,
    )
    session.add(mv)
    before = float(inv.on_hand)
    inv.on_hand = new_on_hand
    session.add(AuditLog(
        tenant_id=principal.tenant_id,
        entity_type="inventory_level", entity_id=str(inv.id), action="adjust",
        actor=actor, before={"on_hand": before},
        after={"on_hand": float(new_on_hand), "reason": body.reason},
    ))
    await session.commit()
    return {"status": "ok", "on_hand": float(new_on_hand)}


# ─────────────────────────── apparel variants ───────────────────────────

# Sizes with no size_seq sort after every sequenced size rather than before,
# so an unrecognised label never masquerades as the smallest size.
_UNSEQUENCED = 10_000


def _variant_sort_key(v: Any) -> tuple:
    return (v.size_seq if v.size_seq is not None else _UNSEQUENCED,
            (v.size or ""), (v.colour or ""))


def _size_axis(variants: List[Any]) -> List[tuple]:
    """Distinct sizes in wearing order: (seq, label)."""
    seen: Dict[str, int] = {}
    for v in variants:
        if v.size and v.size not in seen:
            seen[v.size] = v.size_seq if v.size_seq is not None else _UNSEQUENCED
    return sorted(((seq, label) for label, seq in seen.items()))


@router.get("/styles")
async def list_styles(
    search: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Apparel styles with their variant counts and aggregate stock.

    A style is the parent of the size x colour grid; the sellable Product rows
    beneath it carry the stock. Tenant-scoped on BOTH tables: style codes are
    unique per tenant, not globally, so two retailers legitimately both use
    "SHIRT-001" and neither may see the other's.
    """
    stmt = scope_query(select(ProductStyle), ProductStyle, principal).where(
        ProductStyle.is_active.is_(True)
    )
    if category:
        stmt = stmt.where(ProductStyle.category == category)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(ProductStyle.style_code.ilike(like) | ProductStyle.name.ilike(like))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    styles = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()

    # Variants are fetched in one scoped query, not per style: a 200-style page
    # would otherwise issue 200 round trips.
    style_ids = [s.id for s in styles]
    variants = []
    if style_ids:
        variants = (
            await session.execute(
                scope_query(select(Product), Product, principal).where(
                    Product.style_id.in_(style_ids), Product.is_active.is_(True)
                )
            )
        ).scalars().all()
    by_style: Dict[Any, List[Any]] = {}
    for v in variants:
        by_style.setdefault(v.style_id, []).append(v)

    return {
        "total": total, "limit": limit, "offset": offset,
        "items": [
            {
                "id": str(s.id), "style_code": s.style_code, "name": s.name,
                "brand": s.brand, "category": s.category, "season": s.season,
                "hsn_code": s.hsn_code,
                "variant_count": len(by_style.get(s.id, [])),
                # Ordered by size_seq, NOT alphabetically. Sorting apparel
                # sizes as strings yields L, M, S, XL -- Large first, Small
                # third -- which is wrong in every size-curve report and pick
                # list. Variants with no size_seq (non-apparel, or an
                # unrecognised scale) sort last by their label.
                "sizes": [x[1] for x in _size_axis(by_style.get(s.id, []))],
                "colours": sorted({v.colour for v in by_style.get(s.id, []) if v.colour}),
                "variants": [
                    {
                        "product_id": str(v.id), "sku": v.sku, "size": v.size,
                        "size_seq": v.size_seq, "colour": v.colour,
                        "barcode": v.barcode, "unit_price": float(v.unit_price),
                    }
                    for v in sorted(by_style.get(s.id, []), key=_variant_sort_key)
                ],
            }
            for s in styles
        ],
    }


@router.get("/variants/by-barcode/{barcode}")
async def variant_by_barcode(
    barcode: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Resolve a scanned barcode to one variant, within the caller's tenant.

    Barcodes are unique per tenant, not globally: EAN ranges get reused across
    unrelated retailers and private-label codes collide constantly. An unscoped
    lookup here would return another retailer's garment to a till, which is the
    worst possible place for a cross-tenant leak.
    """
    if not barcode or len(barcode) > 64:
        raise HTTPException(422, "barcode must be 1-64 characters")

    product = (
        await session.execute(
            scope_query(select(Product), Product, principal).where(
                Product.barcode == barcode, Product.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if product is None:
        # Identical response whether it is absent or another tenant's, so the
        # endpoint cannot be used to probe for other tenants' barcodes.
        raise HTTPException(404, "no active variant with that barcode")

    style = None
    if product.style_id is not None:
        style = (
            await session.execute(
                scope_query(select(ProductStyle), ProductStyle, principal).where(
                    ProductStyle.id == product.style_id
                )
            )
        ).scalar_one_or_none()

    levels = (
        await session.execute(
            scope_query(select(InventoryLevel), InventoryLevel, principal).where(
                InventoryLevel.product_id == product.id
            )
        )
    ).scalars().all()

    return {
        "product_id": str(product.id), "sku": product.sku, "name": product.name,
        "barcode": product.barcode, "size": product.size, "colour": product.colour,
        "unit_price": float(product.unit_price), "unit_cost": float(product.unit_cost),
        "style": None if style is None else {
            "id": str(style.id), "style_code": style.style_code,
            "name": style.name, "brand": style.brand, "hsn_code": style.hsn_code,
        },
        "stock": [
            {"location_id": str(l.location_id), "on_hand": float(l.on_hand),
             "on_order": float(l.on_order),
             "inventory_position": float(l.inventory_position)}
            for l in levels
        ],
        "total_on_hand": float(sum(l.on_hand for l in levels)),
    }
