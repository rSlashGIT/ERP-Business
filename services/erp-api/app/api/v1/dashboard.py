"""Dashboard aggregates. One round trip, not twelve."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    InventoryLevel, POStatus, Product, PurchaseOrder, Recommendation,
    RecommendationStatus, ReplenishmentRun, RunStatus,
)
from ...db.session import get_session
from ...security.core import Principal, scope_query
from ...security.deps import current_principal

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Tenant-scoped dashboard. Every one of the seven aggregates below is
    filtered on the token's tenant; an unscoped COUNT or SUM here would leak
    another tenant's scale even without exposing a single row."""
    stock_value = (
        await session.execute(
            scope_query(
                select(func.coalesce(func.sum(InventoryLevel.on_hand * Product.unit_cost), 0))
                .join(Product, Product.id == InventoryLevel.product_id),
                InventoryLevel, principal)
        )
    ).scalar_one()

    below = (
        await session.execute(
            scope_query(select(func.count()).select_from(InventoryLevel), InventoryLevel, principal)
            .where(
                InventoryLevel.reorder_point.isnot(None),
                InventoryLevel.on_hand + InventoryLevel.on_order <= InventoryLevel.reorder_point,
            )
        )
    ).scalar_one()

    stockouts = (
        await session.execute(
            scope_query(select(func.count()).select_from(InventoryLevel), InventoryLevel, principal)
            .where(InventoryLevel.on_hand <= 0)
        )
    ).scalar_one()

    pending = (
        await session.execute(
            scope_query(
                select(func.count(), func.coalesce(func.sum(Recommendation.line_value), 0)),
                Recommendation, principal)
            .where(Recommendation.status == RecommendationStatus.PENDING)
        )
    ).one()

    critical = (
        await session.execute(
            scope_query(select(func.count()).select_from(Recommendation), Recommendation, principal)
            .where(
                Recommendation.status == RecommendationStatus.PENDING,
                Recommendation.urgency == "critical",
            )
        )
    ).scalar_one()

    open_pos = (
        await session.execute(
            scope_query(
                select(func.count(), func.coalesce(func.sum(PurchaseOrder.total_value), 0)),
                PurchaseOrder, principal)
            .where(PurchaseOrder.status.in_([
                POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED,
            ]))
        )
    ).one()

    last_run = (
        await session.execute(
            scope_query(select(ReplenishmentRun), ReplenishmentRun, principal)
            .order_by(ReplenishmentRun.run_date.desc()).limit(1)
        )
    ).scalar_one_or_none()

    return {
        "inventory": {
            "stock_value": float(stock_value),
            "skus_below_reorder": below,
            "skus_out_of_stock": stockouts,
        },
        "procurement": {
            "pending_recommendations": pending[0],
            "pending_value": float(pending[1]),
            "critical_recommendations": critical,
            "open_purchase_orders": open_pos[0],
            "open_po_value": float(open_pos[1]),
        },
        "last_run": None if last_run is None else {
            "id": str(last_run.id),
            "run_date": last_run.run_date.isoformat(),
            "status": last_run.status.value,
            "policy_version": last_run.policy_version,
            "lines_recommended": last_run.lines_recommended,
            "duration_ms": last_run.duration_ms,
            "error": last_run.error,
        },
    }
