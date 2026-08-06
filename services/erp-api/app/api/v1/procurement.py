"""
Procurement API — the routes behind the PO Approval screen.

APPROVAL SEMANTICS
------------------
Approving recommendations does NOT mutate them into POs one by one. It groups
the approved lines by (supplier, location) and emits one PurchaseOrder per
group inside a single transaction. Buyers think in "the order I send Acme",
not in individual SKU lines, and a partial failure that creates three of five
POs is unrecoverable in a procurement workflow.

The AI's original quantity is preserved on every line (ai_recommended_qty)
alongside what the human actually ordered (ordered_qty). The variance between
them is the model's report card.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db.models import (
    AuditLog, Location, POStatus, Product, PurchaseOrder, PurchaseOrderLine,
    Recommendation, RecommendationStatus, ReplenishmentRun, Supplier,
)
from ...db.session import get_session
from ...security.core import Principal, Role, assert_same_tenant, scope_query
from ...security.deps import assert_can_approve, current_principal, requires

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/procurement", tags=["procurement"])


# ───────────── schemas ─────────────

class LineDecision(BaseModel):
    recommendation_id: uuid.UUID
    action: str = Field(description="approve | reject | modify")
    final_qty: Optional[Decimal] = None
    note: Optional[str] = None

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        allowed = {"approve", "reject", "modify"}
        if v not in allowed:
            raise ValueError(f"action must be one of {sorted(allowed)}")
        return v

    @field_validator("final_qty")
    @classmethod
    def _qty(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("final_qty must be >= 0")
        return v


class ApprovalRequest(BaseModel):
    decisions: List[LineDecision]
    create_purchase_orders: bool = True
    # `actor` was removed on purpose. It is now taken from the bearer token;
    # a client-supplied actor makes every audit row unverifiable.


class ApprovalResult(BaseModel):
    approved: int
    rejected: int
    modified: int
    purchase_orders_created: List[str]
    errors: List[Dict[str, str]] = Field(default_factory=list)


# ───────────── read ─────────────

@router.get("/recommendations")
async def list_recommendations(
    run_id: Optional[uuid.UUID] = None,
    status: str = Query("pending"),
    urgency: Optional[str] = None,
    location_code: Optional[str] = None,
    supplier_id: Optional[uuid.UUID] = None,
    min_value: Optional[float] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Approval queue. Ordered by urgency then value — highest-risk first.

    Tenant-scoped from the token. There is deliberately no tenant parameter on
    this endpoint: if the caller could name a tenant, they could name someone
    else's.
    """
    if run_id is None:
        run_id = (
            await session.execute(
                scope_query(select(ReplenishmentRun.id), ReplenishmentRun, principal)
                .order_by(ReplenishmentRun.run_date.desc(), ReplenishmentRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if run_id is None:
            return {"run_id": None, "total": 0, "items": [], "summary": {}}

    stmt = (
        select(Recommendation)
        .options(
            selectinload(Recommendation.product),
            selectinload(Recommendation.location),
            selectinload(Recommendation.supplier),
        )
        .where(Recommendation.run_id == run_id)
    )
    stmt = scope_query(stmt, Recommendation, principal)
    if status != "all":
        try:
            stmt = stmt.where(Recommendation.status == RecommendationStatus(status))
        except ValueError:
            raise HTTPException(422, f"invalid status '{status}'")
    if urgency:
        stmt = stmt.where(Recommendation.urgency == urgency)
    if supplier_id:
        stmt = stmt.where(Recommendation.supplier_id == supplier_id)
    if min_value is not None:
        stmt = stmt.where(Recommendation.line_value >= Decimal(str(min_value)))
    if location_code:
        stmt = stmt.join(Location, Location.id == Recommendation.location_id).where(
            Location.code == location_code
        )

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    rows = (
        await session.execute(
            # Order by value in SQL, then re-rank by urgency in Python. Urgency
            # is a string column and its lexical order is wrong ("critical" <
            # "high" < "low" < "medium"); a DB-side CASE would work but differs
            # across Postgres/SQLite, and the page is capped at 1000 rows.
            stmt.order_by(Recommendation.line_value.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    rows = sorted(rows, key=lambda r: (rank.get(r.urgency, 9), -float(r.line_value)))

    summary_rows = (
        await session.execute(
            select(Recommendation.urgency, func.count(), func.sum(Recommendation.line_value))
            .where(Recommendation.run_id == run_id,
                   Recommendation.status == RecommendationStatus.PENDING)
            .group_by(Recommendation.urgency)
        )
    ).all()

    return {
        "run_id": str(run_id),
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": {
            u: {"count": c, "value": float(v or 0)} for u, c, v in summary_rows
        },
        "items": [
            {
                "id": str(r.id),
                "sku": r.product.sku,
                "product_name": r.product.name,
                "location_code": r.location.code,
                "location_name": r.location.name,
                "supplier_id": str(r.supplier_id) if r.supplier_id else None,
                "supplier_name": r.supplier.name if r.supplier else None,
                "recommended_qty": float(r.recommended_qty),
                "unconstrained_qty": float(r.unconstrained_qty or 0),
                "unit_cost": float(r.unit_cost),
                "line_value": float(r.line_value),
                "urgency": r.urgency,
                "confidence": float(r.confidence),
                "status": r.status.value,
                "rationale": r.rationale,
                "warnings": r.warnings,
            }
            for r in rows
        ],
    }


@router.get("/runs")
async def list_runs(limit: int = 30, session: AsyncSession = Depends(get_session),
                    principal: Principal = Depends(current_principal)) -> List[Dict[str, Any]]:
    runs = (
        await session.execute(
            scope_query(select(ReplenishmentRun), ReplenishmentRun, principal)
            .order_by(ReplenishmentRun.run_date.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(r.id), "run_date": r.run_date.isoformat(), "status": r.status.value,
            "policy_version": r.policy_version, "items_sent": r.items_sent,
            "lines_recommended": r.lines_recommended, "total_value": float(r.total_value),
            "duration_ms": r.duration_ms, "error": r.error,
        }
        for r in runs
    ]


# ───────────── write ─────────────

@router.post(
    "/recommendations/decide",
    response_model=ApprovalResult,
    dependencies=[Depends(requires(Role.BUYER, Role.APPROVER))],
)
async def decide(
    body: ApprovalRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> ApprovalResult:
    """Apply approve / reject / modify decisions and emit grouped POs.

    All-or-nothing: any unrecoverable error rolls the whole batch back. A buyer
    who clicks "approve 40 lines" must not end up with 17 approved and no idea
    which. Per-line validation failures are collected and returned instead of
    raising, so the UI can highlight exactly which rows to fix.
    """
    if not body.decisions:
        raise HTTPException(422, "decisions must not be empty")

    # The actor is the authenticated subject, NEVER the body. Accepting an
    # actor string from the client makes the audit log fiction.
    actor = principal.subject

    ids = [d.recommendation_id for d in body.decisions]
    recos = {
        r.id: r for r in (
            await session.execute(
                scope_query(
                    select(Recommendation).options(selectinload(Recommendation.product)),
                    Recommendation, principal,
                ).where(Recommendation.id.in_(ids))
            )
        ).scalars()
    }
    missing = set(ids) - set(recos)
    if missing:
        # Either genuinely absent or owned by another tenant. Same response
        # either way -- distinguishing them leaks the existence of other
        # tenants' records.
        logger.warning("actor=%s tenant=%s requested %d unreachable recommendations",
                       actor, principal.tenant_id, len(missing))

    # Value gate BEFORE any mutation, so a batch that a buyer may not approve
    # is rejected whole rather than half-applied.
    staged_value = Decimal(0)
    for d in body.decisions:
        r = recos.get(d.recommendation_id)
        if r is None or d.action == "reject":
            continue
        qty = d.final_qty if d.action == "modify" and d.final_qty is not None else r.recommended_qty
        staged_value += Decimal(str(qty)) * r.unit_cost
    if body.create_purchase_orders and staged_value > 0:
        assert_can_approve(principal, staged_value)

    errors: List[Dict[str, str]] = []
    approved: List[tuple] = []
    n_app = n_rej = n_mod = 0
    now = datetime.now(timezone.utc)

    for d in body.decisions:
        r = recos.get(d.recommendation_id)
        if r is None:
            errors.append({"id": str(d.recommendation_id), "error": "not found"})
            continue
        if r.status != RecommendationStatus.PENDING:
            errors.append({
                "id": str(d.recommendation_id),
                "error": f"already {r.status.value}; refresh the queue",
            })
            continue

        r.decided_by = actor
        r.decided_at = now
        r.decision_note = d.note

        if d.action == "reject":
            r.status = RecommendationStatus.REJECTED
            r.final_qty = Decimal(0)
            n_rej += 1
        else:
            qty = d.final_qty if d.action == "modify" else r.recommended_qty
            if qty is None:
                errors.append({"id": str(r.id), "error": "modify requires final_qty"})
                continue
            if qty <= 0:
                r.status = RecommendationStatus.REJECTED
                r.final_qty = Decimal(0)
                n_rej += 1
                continue
            r.final_qty = Decimal(str(qty))
            r.status = (
                RecommendationStatus.MODIFIED if d.action == "modify"
                else RecommendationStatus.APPROVED
            )
            n_mod += d.action == "modify"
            n_app += d.action == "approve"
            approved.append((r, r.final_qty, d.note))

        session.add(AuditLog(
            entity_type="recommendation", entity_id=str(r.id), action=d.action,
            actor=actor,
            before={"status": "pending", "qty": float(r.recommended_qty)},
            after={"status": r.status.value, "qty": float(r.final_qty or 0)},
        ))

    created: List[str] = []
    if body.create_purchase_orders and approved:
        created = await _emit_pos(session, approved, principal)

    await session.commit()
    return ApprovalResult(
        approved=n_app, rejected=n_rej, modified=n_mod,
        purchase_orders_created=created, errors=errors,
    )


async def _emit_pos(session: AsyncSession, approved: List[tuple],
                    principal: Principal) -> List[str]:
    """Group approved lines into one PO per (supplier, location).

    Takes the principal rather than a bare tenant_id so the sequence query can
    go through scope_query like everything else -- the PO number was previously
    derived from a COUNT over EVERY tenant's purchase orders, which leaked one
    tenant's order volume into another's numbering and made a tenant's own
    sequence jump whenever an unrelated tenant placed an order.
    """
    actor = principal.subject
    tenant_id = principal.tenant_id
    groups: Dict[tuple, List[tuple]] = {}
    for r, qty, note in approved:
        groups.setdefault((r.supplier_id, r.location_id), []).append((r, qty, note))

    seq = (
        await session.execute(
            scope_query(select(func.count()).select_from(PurchaseOrder),
                        PurchaseOrder, principal)
        )
    ).scalar_one()
    created: List[str] = []

    for (supplier_id, location_id), lines in groups.items():
        if supplier_id is None:
            logger.warning("skipping PO creation for %d lines with no supplier", len(lines))
            continue
        seq += 1
        po = PurchaseOrder(
            tenant_id=tenant_id,
            po_number=f"PO-{date.today():%Y%m}-{seq:05d}",
            supplier_id=supplier_id,
            location_id=location_id,
            status=POStatus.APPROVED,
            source="smartstock",
            replenishment_run_id=lines[0][0].run_id,
            approved_by=actor,
            approved_at=datetime.now(timezone.utc),
            total_value=Decimal(0),
        )
        session.add(po)
        await session.flush()

        total = Decimal(0)
        for i, (r, qty, note) in enumerate(lines, start=1):
            value = Decimal(str(qty)) * r.unit_cost
            pol = PurchaseOrderLine(
                tenant_id=tenant_id,
                purchase_order_id=po.id, product_id=r.product_id, line_no=i,
                ai_recommended_qty=r.recommended_qty, ordered_qty=Decimal(str(qty)),
                unit_cost=r.unit_cost, line_value=value,
                override_reason=note if qty != r.recommended_qty else None,
                ai_rationale=r.rationale or {},
            )
            session.add(pol)
            await session.flush()
            r.purchase_order_line_id = pol.id
            total += value

        po.total_value = total
        created.append(po.po_number)
        session.add(AuditLog(
            entity_type="purchase_order", entity_id=str(po.id), action="create",
            actor=actor, after={"po_number": po.po_number, "value": float(total),
                                "lines": len(lines), "source": "smartstock"},
        ))
    return created


@router.get("/purchase-orders")
async def list_pos(
    status: Optional[str] = None, limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> List[Dict[str, Any]]:
    stmt = scope_query(select(PurchaseOrder), PurchaseOrder, principal).options(
        selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.supplier)
    ).order_by(PurchaseOrder.created_at.desc()).limit(limit)
    if status:
        try:
            stmt = stmt.where(PurchaseOrder.status == POStatus(status))
        except ValueError:
            raise HTTPException(422, f"invalid status '{status}'")
    pos = (await session.execute(stmt)).scalars().unique().all()
    return [
        {
            "id": str(p.id), "po_number": p.po_number, "status": p.status.value,
            "supplier_name": p.supplier.name if p.supplier else None,
            "total_value": float(p.total_value), "line_count": len(p.lines),
            "source": p.source, "approved_by": p.approved_by,
            "expected_delivery_date": p.expected_delivery_date.isoformat() if p.expected_delivery_date else None,
            "created_at": p.created_at.isoformat(),
        }
        for p in pos
    ]


@router.get("/variance")
async def ai_variance(
    days: int = 90,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """How often, and by how much, humans override the AI. The model's report card."""
    rows = (
        await session.execute(
            scope_query(
                select(Recommendation.status, func.count(), func.sum(Recommendation.line_value)),
                Recommendation, principal)
            .where(Recommendation.decided_at.isnot(None))
            .group_by(Recommendation.status)
        )
    ).all()
    mods = (
        await session.execute(
            scope_query(select(Recommendation.recommended_qty, Recommendation.final_qty),
                        Recommendation, principal)
            .where(Recommendation.status == RecommendationStatus.MODIFIED)
        )
    ).all()
    deltas = [
        float(f - r) / float(r) for r, f in mods if r and float(r) > 0 and f is not None
    ]
    return {
        "by_status": {s.value: {"count": c, "value": float(v or 0)} for s, c, v in rows},
        "modification_count": len(deltas),
        "mean_relative_override": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
        "override_bias": (
            "humans order MORE than the model" if deltas and sum(deltas) > 0
            else "humans order LESS than the model" if deltas else "no overrides yet"
        ),
    }
