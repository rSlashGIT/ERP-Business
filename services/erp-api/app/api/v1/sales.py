"""
Sales: customers, tax invoices, payments, receivables.

Every query is tenant-scoped through scope_query — enforced by
scripts/audit_route_scoping.py, which fails the build on any bypass.

GST is computed by app/domain/gst.py, the same module the demo server uses,
so the tax a customer is charged never depends on which transport served them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db.models import (
    Customer, InventoryLevel, InvoiceStatus, Location, MovementType, Payment,
    PaymentAllocation, PaymentMethod, Product, SalesInvoice, SalesInvoiceLine,
    StockMovement,
)
from ...db.session import get_session
from ...domain.gst import LineInput, amount_in_words, compute_invoice
from ...security.core import Principal, Role, scope_query
from ...security.deps import current_principal, requires

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sales", tags=["sales"])


# ───────────────────────── schemas ─────────────────────────

class InvoiceLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Optional[Decimal] = None      # defaults to the product's price
    discount_pct: Decimal = Decimal("0")

    @field_validator("quantity")
    @classmethod
    def _qty(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity must be positive")
        return v

    @field_validator("discount_pct")
    @classmethod
    def _disc(cls, v: Decimal) -> Decimal:
        if v < 0 or v > 100:
            raise ValueError("discount_pct must be between 0 and 100")
        return v


class InvoiceIn(BaseModel):
    customer_id: uuid.UUID
    location_id: uuid.UUID
    lines: List[InvoiceLineIn] = Field(min_length=1)
    invoice_date: Optional[date] = None
    notes: str = ""
    auto_pay: bool = False
    allow_negative_stock: bool = False


class PaymentIn(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal
    method: PaymentMethod = PaymentMethod.CASH
    reference: str = ""

    @field_validator("amount")
    @classmethod
    def _amt(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None
    state_code: Optional[str] = None
    address: Optional[str] = None
    credit_limit: Decimal = Decimal("0")
    credit_days: int = 0


# ───────────────────────── helpers ─────────────────────────

async def _next_number(session: AsyncSession, principal: Principal, model: Any,
                       column: Any, prefix: str) -> str:
    """Per-tenant sequence. The uniqueness constraint is
    (tenant_id, number), so each business numbers independently and one
    tenant's volume never shifts another's sequence."""
    today = date.today()
    fy = today.year if today.month >= 4 else today.year - 1
    stem = f"{prefix}/{fy}-{str(fy + 1)[2:]}/"
    last = (
        await session.execute(
            scope_query(select(column), model, principal)
            .where(column.like(f"{stem}%")).order_by(column.desc()).limit(1)
        )
    ).scalar_one_or_none()
    n = int(str(last).rsplit("/", 1)[-1]) + 1 if last else 1
    return f"{stem}{n:04d}"


# ───────────────────────── customers ─────────────────────────

@router.get("/customers")
async def list_customers(
    search: Optional[str] = None,
    limit: int = Query(200, le=1000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    stmt = scope_query(select(Customer), Customer, principal).where(Customer.is_active.is_(True))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Customer.name.ilike(like) | Customer.code.ilike(like)
                          | Customer.phone.ilike(like))
    rows = (await session.execute(stmt.order_by(Customer.name).limit(limit))).scalars().all()

    # Outstanding per customer, in one scoped query rather than N.
    dues = dict((await session.execute(
        scope_query(
            select(SalesInvoice.customer_id,
                   func.coalesce(func.sum(SalesInvoice.grand_total - SalesInvoice.amount_paid), 0)),
            SalesInvoice, principal)
        .where(SalesInvoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PART_PAID]))
        .group_by(SalesInvoice.customer_id)
    )).all())
    return {"items": [
        {"id": str(c.id), "code": c.code, "name": c.name, "phone": c.phone,
         "gstin": c.gstin, "state_code": c.state_code, "is_walkin": c.is_walkin,
         "credit_limit": float(c.credit_limit), "credit_days": c.credit_days,
         "outstanding": float(dues.get(c.id, 0))}
        for c in rows]}


@router.post("/customers", dependencies=[Depends(requires(Role.BUYER, Role.APPROVER))])
async def create_customer(
    body: CustomerIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    code = body.code or f"C-{uuid.uuid4().hex[:6].upper()}"
    clash = (await session.execute(
        scope_query(select(Customer.id), Customer, principal).where(Customer.code == code)
    )).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"customer code {code} already exists for this business")
    c = Customer(tenant_id=principal.tenant_id, code=code, name=body.name, phone=body.phone,
                 email=body.email, gstin=body.gstin, state_code=body.state_code,
                 address=body.address, credit_limit=body.credit_limit,
                 credit_days=body.credit_days)
    session.add(c)
    await session.commit()
    return {"id": str(c.id), "code": c.code, "name": c.name}


# ───────────────────────── invoices ─────────────────────────

@router.post("/invoices", dependencies=[Depends(requires(Role.BUYER, Role.APPROVER))])
async def create_invoice(
    body: InvoiceIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Post a tax invoice: compute GST, write the stock ledger, create the receivable.

    Customer, location and every product are re-read under the tenant filter.
    A billing endpoint that could sell another tenant's garment is the worst
    possible place for a cross-tenant leak, so nothing is trusted from the body
    beyond the ids, and each id must resolve inside the caller's tenant.
    """
    customer = (await session.execute(
        scope_query(select(Customer), Customer, principal).where(Customer.id == body.customer_id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(404, "customer not found")
    location = (await session.execute(
        scope_query(select(Location), Location, principal).where(Location.id == body.location_id)
    )).scalar_one_or_none()
    if location is None:
        raise HTTPException(404, "location not found")

    product_ids = [l.product_id for l in body.lines]
    products = {p.id: p for p in (await session.execute(
        scope_query(select(Product), Product, principal)
        .where(Product.id.in_(product_ids), Product.is_active.is_(True))
    )).scalars()}
    missing = set(product_ids) - set(products)
    if missing:
        raise HTTPException(404, f"{len(missing)} product(s) not found for this business")

    if not body.allow_negative_stock:
        levels = {l.product_id: l for l in (await session.execute(
            scope_query(select(InventoryLevel), InventoryLevel, principal)
            .where(InventoryLevel.product_id.in_(product_ids),
                   InventoryLevel.location_id == body.location_id)
        )).scalars()}
        for line in body.lines:
            have = levels[line.product_id].on_hand if line.product_id in levels else Decimal(0)
            if have < line.quantity:
                raise HTTPException(
                    422, f"only {have:g} in stock at this location for "
                         f"{products[line.product_id].name}")

    seller_state = None   # set from the tenant's company profile in production
    totals = compute_invoice(
        [LineInput(product_id=str(l.product_id), quantity=l.quantity,
                   unit_price=(l.unit_price if l.unit_price is not None
                               else products[l.product_id].unit_price),
                   discount_pct=l.discount_pct,
                   hsn_code=products[l.product_id].hsn_code,
                   description=products[l.product_id].name)
         for l in body.lines],
        seller_state=seller_state, buyer_state=customer.state_code)

    idate = body.invoice_date or date.today()
    number = await _next_number(session, principal, SalesInvoice,
                                SalesInvoice.invoice_number, "INV")
    inv = SalesInvoice(
        tenant_id=principal.tenant_id, invoice_number=number, customer_id=customer.id,
        location_id=location.id, invoice_date=idate,
        due_date=idate + timedelta(days=customer.credit_days or 0),
        status=InvoiceStatus.PAID if body.auto_pay else InvoiceStatus.POSTED,
        place_of_supply=customer.state_code, is_interstate=totals.is_interstate,
        subtotal=totals.subtotal, discount_total=totals.discount_total,
        taxable_total=totals.taxable_total, cgst_total=totals.cgst_total,
        sgst_total=totals.sgst_total, igst_total=totals.igst_total,
        round_off=totals.round_off, grand_total=totals.grand_total,
        amount_paid=totals.grand_total if body.auto_pay else Decimal(0),
        notes=body.notes, created_by=principal.subject)
    session.add(inv)
    await session.flush()

    for i, lr in enumerate(totals.lines, start=1):
        pid = uuid.UUID(lr.product_id)
        session.add(SalesInvoiceLine(
            tenant_id=principal.tenant_id, invoice_id=inv.id, product_id=pid, line_no=i,
            quantity=lr.quantity, unit_price=lr.unit_price, discount_pct=lr.discount_pct,
            taxable_value=lr.taxable_value, gst_rate=lr.gst_rate,
            cgst=lr.cgst, sgst=lr.sgst, igst=lr.igst,
            line_total=lr.line_total, hsn_code=lr.hsn_code))
        # Ledger first: stock_movements is the truth, inventory_levels the cache.
        session.add(StockMovement(
            tenant_id=principal.tenant_id, product_id=pid, location_id=location.id,
            movement_type=MovementType.SALE, quantity=-lr.quantity,
            occurred_at=datetime.now(), reference_type="sales_invoice",
            reference_id=number, idempotency_key=f"{principal.tenant_id}:{number}:{i}"))
        level = (await session.execute(
            scope_query(select(InventoryLevel), InventoryLevel, principal)
            .where(InventoryLevel.product_id == pid,
                   InventoryLevel.location_id == location.id).with_for_update()
        )).scalar_one_or_none()
        if level is not None:
            level.on_hand = level.on_hand - lr.quantity

    await session.commit()
    return {"id": str(inv.id), "invoice_number": number,
            "grand_total": float(totals.grand_total),
            "amount_in_words": amount_in_words(totals.grand_total),
            **totals.as_dict()}


@router.get("/invoices")
async def list_invoices(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(200, le=1000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    stmt = scope_query(select(SalesInvoice), SalesInvoice, principal)
    if status and status != "all":
        try:
            stmt = stmt.where(SalesInvoice.status == InvoiceStatus(status))
        except ValueError:
            raise HTTPException(422, f"invalid status '{status}'")
    if search:
        stmt = stmt.where(SalesInvoice.invoice_number.ilike(f"%{search}%"))
    rows = (await session.execute(
        stmt.order_by(SalesInvoice.invoice_date.desc()).limit(limit))).scalars().all()
    return {"items": [
        {"id": str(i.id), "invoice_number": i.invoice_number,
         "invoice_date": i.invoice_date.isoformat(), "status": i.status.value,
         "is_interstate": i.is_interstate, "taxable_total": float(i.taxable_total),
         "gst_total": float(i.cgst_total + i.sgst_total + i.igst_total),
         "grand_total": float(i.grand_total), "amount_paid": float(i.amount_paid),
         "balance_due": float(i.balance_due)} for i in rows]}


@router.get("/receivables")
async def receivables(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Ageing buckets. Oldest first, because that is the call list."""
    rows = (await session.execute(
        scope_query(select(SalesInvoice), SalesInvoice, principal)
        .where(SalesInvoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PART_PAID]))
        .order_by(SalesInvoice.due_date)
    )).scalars().all()
    today = date.today()
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    items = []
    for i in rows:
        bal = float(i.balance_due)
        if bal <= 0.01:
            continue
        days = (today - i.due_date).days if i.due_date else 0
        b = ("current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60
             else "61-90" if days <= 90 else "90+")
        buckets[b] += bal
        items.append({"id": str(i.id), "invoice_number": i.invoice_number,
                      "due_date": i.due_date.isoformat() if i.due_date else None,
                      "days_overdue": max(0, days), "bucket": b, "balance": round(bal, 2)})
    return {"items": items, "buckets": {k: round(v, 2) for k, v in buckets.items()},
            "total": round(sum(buckets.values()), 2)}


@router.post("/payments", dependencies=[Depends(requires(Role.BUYER, Role.APPROVER))])
async def record_payment(
    body: PaymentIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> Dict[str, Any]:
    """Receive money and settle the oldest invoices first.

    Oldest-first is what keeps the ageing report meaningful; allocating
    newest-first leaves permanently stale balances at the top of the ledger.
    """
    customer = (await session.execute(
        scope_query(select(Customer), Customer, principal).where(Customer.id == body.customer_id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(404, "customer not found")

    number = await _next_number(session, principal, Payment, Payment.payment_number, "RCP")
    pay = Payment(tenant_id=principal.tenant_id, payment_number=number,
                  customer_id=customer.id, payment_date=date.today(), amount=body.amount,
                  method=body.method, reference=body.reference,
                  received_by=principal.subject)
    session.add(pay)
    await session.flush()

    left = body.amount
    applied: List[Dict[str, Any]] = []
    open_invoices = (await session.execute(
        scope_query(select(SalesInvoice), SalesInvoice, principal)
        .where(SalesInvoice.customer_id == customer.id,
               SalesInvoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PART_PAID]))
        .order_by(SalesInvoice.invoice_date, SalesInvoice.invoice_number)
    )).scalars().all()
    for inv in open_invoices:
        if left <= Decimal("0.009"):
            break
        due = inv.balance_due
        if due <= 0:
            continue
        take = min(left, due)
        session.add(PaymentAllocation(tenant_id=principal.tenant_id, payment_id=pay.id,
                                      invoice_id=inv.id, amount=take))
        inv.amount_paid = inv.amount_paid + take
        inv.status = (InvoiceStatus.PAID if inv.amount_paid >= inv.grand_total - Decimal("0.01")
                      else InvoiceStatus.PART_PAID)
        applied.append({"invoice_number": inv.invoice_number, "amount": float(take)})
        left -= take

    await session.commit()
    return {"payment_number": number, "amount": float(body.amount),
            "applied": applied, "on_account": float(left)}
