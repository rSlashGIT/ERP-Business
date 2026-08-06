"""
Background task loop.

SCHEDULE
--------
  02:00 local  replenishment.nightly     -> generate draft POs for tomorrow
  03:30 Sunday policy.weekly_refit       -> CMA-ES refit on 12 months of demand
  every 15 min inventory.reconcile       -> rebuild inventory_levels from the ledger
  hourly       leadtime.materialise      -> new goods receipts -> observations

WHY 02:00
---------
After end-of-day close (so today's demand is in demand_history) and before
buyers arrive (so the queue is waiting for them). The window is deliberately
wide: a 50k-SKU generate takes seconds, but a refit takes minutes and must not
collide with the nightly run.

RELIABILITY
-----------
acks_late=True + reject_on_worker_lost=True means a task survives a worker
kill, and WILL be redelivered. Every task below is therefore idempotent —
see run_replenishment's (run_date, triggered_by) uniqueness.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta

from celery import Celery
from celery.schedules import crontab

from ..clients.smartstock import SmartStockClient
from ..db.session import async_session_factory
from ..services.replenishment import run_replenishment

logger = logging.getLogger(__name__)

celery_app = Celery(
    "erp",
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,       # long tasks: do not hoard the queue
    timezone=os.getenv("TZ", "UTC"),
    beat_schedule={
        "nightly-replenishment": {
            "task": "erp.replenishment.nightly",
            "schedule": crontab(hour=2, minute=0),
        },
        "weekly-policy-refit": {
            "task": "erp.policy.refit",
            "schedule": crontab(hour=3, minute=30, day_of_week=0),
        },
        "inventory-reconcile": {
            "task": "erp.inventory.reconcile",
            "schedule": crontab(minute="*/15"),
        },
        "leadtime-materialise": {
            "task": "erp.leadtime.materialise",
            "schedule": crontab(minute=5),
        },
    },
)


def _client() -> SmartStockClient:
    return SmartStockClient(
        base_url=os.getenv("SMARTSTOCK_URL", "http://smartstock:8100"),
        api_key=os.getenv("SMARTSTOCK_API_KEY", ""),
        timeout=float(os.getenv("SMARTSTOCK_TIMEOUT", "300")),
    )


def _run(coro):
    """Celery workers are sync; the ERP data layer is async."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="erp.replenishment.nightly", bind=True, max_retries=3)
def nightly_replenishment(self, run_date_iso: str | None = None):
    async def _go():
        client = _client()
        try:
            async with async_session_factory() as session:
                run = await run_replenishment(
                    session, client,
                    run_date=date.fromisoformat(run_date_iso) if run_date_iso else date.today(),
                    triggered_by="scheduler",
                )
                return {
                    "run_id": str(run.id),
                    "status": run.status.value,
                    "lines": run.lines_recommended,
                    "value": float(run.total_value),
                }
        finally:
            await client.aclose()

    try:
        return _run(_go())
    except Exception as exc:
        # Exponential backoff: SmartStock may be mid-deploy.
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(name="erp.policy.refit", bind=True, max_retries=1)
def weekly_policy_refit(self):
    """Submit a CMA-ES refit and poll it to completion."""
    async def _go():
        import time as _t
        from ..services.replenishment import _fetch_state
        client = _client()
        try:
            async with async_session_factory() as session:
                items = await _fetch_state(session)
            if not items:
                return {"skipped": "no inventory"}
            job = await client.optimize({
                "items": items,
                "max_generations": int(os.getenv("REFIT_GENERATIONS", "80")),
                "seed": 42,
                "sku_batch_size": int(os.getenv("REFIT_SKU_BATCH", "500")),
            })
            job_id = job["job_id"]
            deadline = _t.time() + 3000
            while _t.time() < deadline:
                await asyncio.sleep(10)
                status = await client.job(job_id)
                if status["status"] in ("succeeded", "failed"):
                    logger.info("refit %s -> %s", job_id, status["status"])
                    return status
            return {"job_id": job_id, "status": "timeout"}
        finally:
            await client.aclose()

    return _run(_go())


@celery_app.task(name="erp.inventory.reconcile")
def reconcile_inventory():
    """Rebuild inventory_levels.on_hand from the stock_movements ledger.

    Discrepancies are logged, not silently corrected: a drift means something
    upstream is writing inventory without a movement, and that bug needs
    finding, not masking.
    """
    from sqlalchemy import func, select
    from ..db.models import InventoryLevel, StockMovement

    async def _go():
        async with async_session_factory() as session:
            truth = (
                await session.execute(
                    select(
                        StockMovement.product_id,
                        StockMovement.location_id,
                        func.coalesce(func.sum(StockMovement.quantity), 0),
                    ).group_by(StockMovement.product_id, StockMovement.location_id)
                )
            ).all()
            ledger = {(p, l): q for p, l, q in truth}
            drift = 0
            for inv in (await session.execute(select(InventoryLevel))).scalars():
                expected = ledger.get((inv.product_id, inv.location_id), 0)
                if abs(float(inv.on_hand) - float(expected)) > 1e-6:
                    logger.warning(
                        "inventory drift product=%s location=%s cached=%s ledger=%s",
                        inv.product_id, inv.location_id, inv.on_hand, expected,
                    )
                    inv.on_hand = expected
                    drift += 1
            await session.commit()
            return {"rows_corrected": drift}

    return _run(_go())


@celery_app.task(name="erp.leadtime.materialise")
def materialise_lead_times():
    """Turn new GoodsReceipts into LeadTimeObservations. Idempotent per receipt."""
    from datetime import datetime, time as dtime
    from sqlalchemy import select
    from ..db.models import (
        GoodsReceipt, GoodsReceiptLine, LeadTimeObservation, PurchaseOrder,
        PurchaseOrderLine,
    )

    async def _go():
        async with async_session_factory() as session:
            existing = set(
                (await session.execute(select(LeadTimeObservation.purchase_order_id))).scalars()
            )
            rows = (
                await session.execute(
                    # A receipt is now a DOCUMENT with lines: the PO line it
                    # fulfils is on the line, the date is on the header. Lead
                    # time is still (received - ordered); only the join moved.
                    select(GoodsReceiptLine, GoodsReceipt, PurchaseOrderLine, PurchaseOrder)
                    .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.goods_receipt_id)
                    .join(PurchaseOrderLine,
                          PurchaseOrderLine.id == GoodsReceiptLine.purchase_order_line_id)
                    .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
                    .where(PurchaseOrder.ordered_at.isnot(None))
                )
            ).all()
            added = 0
            for grl, gr, pol, po in rows:
                if po.id in existing:
                    continue
                received_at = datetime.combine(gr.received_date, dtime.min,
                                               tzinfo=po.ordered_at.tzinfo)
                lead = (received_at - po.ordered_at).total_seconds() / 86400.0
                if lead < 0:
                    logger.warning("receipt %s predates PO %s; skipping", gr.id, po.id)
                    continue
                session.add(LeadTimeObservation(
                    supplier_id=po.supplier_id,
                    product_id=pol.product_id,
                    location_id=po.location_id,
                    purchase_order_id=po.id,
                    ordered_at=po.ordered_at,
                    received_at=received_at,
                    lead_days=round(lead, 2),
                    fill_ratio=(grl.accepted_qty / pol.ordered_qty) if pol.ordered_qty else None,
                ))
                added += 1
            await session.commit()
            return {"observations_added": added}

    return _run(_go())
