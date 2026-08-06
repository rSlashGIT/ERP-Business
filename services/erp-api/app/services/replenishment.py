"""
Replenishment orchestration: assemble the payload, call SmartStock, persist.

RUN LIFECYCLE
-------------
    QUEUED -> RUNNING -> SUCCEEDED | PARTIAL | FAILED

Idempotency: (run_date, triggered_by) is unique. A retried Celery task finds
the existing run and returns it instead of double-creating recommendations.
This matters because Celery's default `acks_late` + worker restart WILL replay
the task, and duplicated draft POs destroy trust in the queue immediately.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.smartstock import SmartStockClient, SmartStockError
from ..db.models import (
    DemandHistory, InventoryLevel, LeadTimeObservation, Location, Product,
    Recommendation, RecommendationStatus, ReplenishmentRun, RunStatus,
    Supplier, SupplierProduct,
)

logger = logging.getLogger(__name__)

HISTORY_DAYS = 365
MAX_LEAD_OBSERVATIONS = 30
PAGE_SIZE = 5_000


async def _fetch_state(
    session: AsyncSession, location_ids: Optional[Sequence[uuid.UUID]] = None
) -> List[Dict[str, Any]]:
    """Build the SkuNodeState payload from the ERP's own tables."""
    stmt = (
        select(InventoryLevel, Product, Location)
        .join(Product, Product.id == InventoryLevel.product_id)
        .join(Location, Location.id == InventoryLevel.location_id)
        .where(Product.is_active.is_(True), Location.is_active.is_(True),
               Product.deleted_at.is_(None))
    )
    if location_ids:
        stmt = stmt.where(InventoryLevel.location_id.in_(location_ids))
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    product_ids = {r[1].id for r in rows}
    since = date.today() - timedelta(days=HISTORY_DAYS)

    # Demand history, one query for the whole page.
    dh_rows = (
        await session.execute(
            select(
                DemandHistory.product_id, DemandHistory.location_id,
                DemandHistory.bucket_date, DemandHistory.quantity,
            )
            .where(DemandHistory.product_id.in_(product_ids),
                   DemandHistory.bucket_date >= since)
            .order_by(DemandHistory.bucket_date)
        )
    ).all()
    hist: Dict[tuple, List[float]] = {}
    for pid, lid, _d, qty in dh_rows:
        hist.setdefault((pid, lid), []).append(float(qty))

    # Preferred supplier per product.
    sp_rows = (
        await session.execute(
            select(SupplierProduct, Supplier)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(SupplierProduct.product_id.in_(product_ids), Supplier.is_active.is_(True))
            .order_by(SupplierProduct.is_preferred.desc())
        )
    ).all()
    sourcing: Dict[uuid.UUID, tuple] = {}
    for sp, sup in sp_rows:
        sourcing.setdefault(sp.product_id, (sp, sup))

    # Lead-time observations, most recent N per (supplier, product).
    lt_rows = (
        await session.execute(
            select(LeadTimeObservation.supplier_id, LeadTimeObservation.product_id,
                   LeadTimeObservation.lead_days)
            .where(LeadTimeObservation.product_id.in_(product_ids))
            .order_by(LeadTimeObservation.received_at.desc())
        )
    ).all()
    leads: Dict[tuple, List[float]] = {}
    for sid, pid, days in lt_rows:
        bucket = leads.setdefault((sid, pid), [])
        if len(bucket) < MAX_LEAD_OBSERVATIONS:
            bucket.append(float(days))

    items: List[Dict[str, Any]] = []
    for inv, prod, loc in rows:
        src = sourcing.get(prod.id)
        sp, sup = src if src else (None, None)
        supplier_block = None
        constraints = None
        if sup is not None:
            supplier_block = {
                "supplier_id": str(sup.id),
                "name": sup.name,
                "contract_lead_days": float(sp.lead_days_override or sup.contract_lead_days),
                "contract_lead_cv": float(sup.contract_lead_cv),
                "reliability_score": float(sup.reliability_score) if sup.reliability_score else None,
            }
            constraints = {
                "moq": float(sp.moq),
                "order_multiple": float(sp.order_multiple),
                "max_order_qty": float(sp.max_order_qty) if sp.max_order_qty else None,
                "max_inventory_position": float(loc.capacity_units) if loc.capacity_units else None,
                "shelf_life_days": prod.shelf_life_days,
            }
        items.append({
            "sku_id": prod.sku,
            "node_id": loc.code,
            "on_hand": float(inv.on_hand),
            "on_order": float(inv.on_order),
            "backorder": float(inv.backorder),
            "unit_cost": float(sp.unit_cost if sp and sp.unit_cost else prod.unit_cost),
            "unit_price": float(prod.unit_price),
            "demand_history": hist.get((prod.id, loc.id), []),
            "supplier": supplier_block,
            "lead_time_observations": leads.get((sup.id, prod.id), []) if sup else [],
            "constraints": constraints,
        })
    return items


async def run_replenishment(
    session: AsyncSession,
    client: SmartStockClient,
    run_date: Optional[date] = None,
    triggered_by: str = "scheduler",
    location_ids: Optional[Sequence[uuid.UUID]] = None,
    dry_run: bool = False,
) -> ReplenishmentRun:
    run_date = run_date or date.today()

    existing = (
        await session.execute(
            select(ReplenishmentRun).where(
                ReplenishmentRun.run_date == run_date,
                ReplenishmentRun.triggered_by == triggered_by,
            )
        )
    ).scalar_one_or_none()
    if existing and existing.status in (RunStatus.SUCCEEDED, RunStatus.RUNNING):
        logger.info("run for %s already %s; skipping", run_date, existing.status)
        return existing

    run = existing or ReplenishmentRun(run_date=run_date, triggered_by=triggered_by)
    run.status = RunStatus.RUNNING
    run.error = None
    session.add(run)
    await session.flush()

    t0 = time.perf_counter()
    try:
        items = await _fetch_state(session, location_ids)
        if not items:
            run.status = RunStatus.SUCCEEDED
            run.items_sent = 0
            run.stats = {"note": "no active inventory rows matched"}
            await session.commit()
            return run

        payload = {
            "run_id": str(run.id),
            "as_of_date": run_date.isoformat(),
            "items": items,
            "review_period_days": 1,
            "service_level_target": 0.95,
            "dry_run": dry_run,
        }
        response = await client.generate(payload)

        run.policy_version = response.get("policy_version")
        run.engine_version = response.get("engine_version")
        run.items_sent = len(items)
        stats = response.get("stats", {})
        run.lines_recommended = int(stats.get("lines_recommended", 0))
        run.total_value = Decimal(str(stats.get("total_value", 0)))
        run.stats = stats

        if not dry_run:
            await _persist(session, run, response)

        skipped = response.get("skipped") or []
        run.status = RunStatus.PARTIAL if skipped else RunStatus.SUCCEEDED
        run.duration_ms = int((time.perf_counter() - t0) * 1000)
        await session.commit()
        logger.info(
            "replenishment run %s: %d items -> %d lines, %s",
            run.id, run.items_sent, run.lines_recommended, run.status,
        )
        return run

    except SmartStockError as exc:
        await session.rollback()
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.duration_ms = int((time.perf_counter() - t0) * 1000)
        session.add(run)
        await session.commit()
        logger.error("replenishment run failed: %s", exc)
        raise
    except Exception as exc:
        await session.rollback()
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        session.add(run)
        await session.commit()
        logger.exception("replenishment run crashed")
        raise


async def _persist(session: AsyncSession, run: ReplenishmentRun, response: Dict[str, Any]) -> None:
    """Map draft POs onto Recommendation rows, keyed by SKU/location code."""
    prod_map = {
        p.sku: p.id for p in (await session.execute(select(Product))).scalars()
    }
    loc_map = {
        l.code: l.id for l in (await session.execute(select(Location))).scalars()
    }
    seen: set = set()
    for dpo in response.get("draft_purchase_orders", []):
        supplier_id = dpo.get("supplier_id")
        try:
            supplier_uuid = uuid.UUID(supplier_id) if supplier_id else None
        except (ValueError, TypeError):
            supplier_uuid = None
        for line in dpo.get("lines", []):
            pid = prod_map.get(line["sku_id"])
            lid = loc_map.get(line["node_id"])
            if pid is None or lid is None:
                logger.warning("unmapped sku/node %s/%s", line["sku_id"], line["node_id"])
                continue
            key = (pid, lid)
            if key in seen:   # unique constraint guard
                continue
            seen.add(key)
            session.add(Recommendation(
                run_id=run.id,
                product_id=pid,
                location_id=lid,
                supplier_id=supplier_uuid,
                recommended_qty=Decimal(str(line["recommended_qty"])),
                unconstrained_qty=Decimal(str(line.get("unconstrained_qty", 0))),
                unit_cost=Decimal(str(line.get("unit_cost", 0))),
                line_value=Decimal(str(line.get("line_value", 0))),
                urgency=line.get("urgency", "low"),
                confidence=Decimal(str(line.get("confidence", 0))),
                status=RecommendationStatus.PENDING,
                rationale=line.get("rationale") or {},
                warnings=line.get("warnings") or [],
            ))
