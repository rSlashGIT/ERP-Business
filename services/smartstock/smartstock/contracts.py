"""
Wire contracts between the ERP backend and the SmartStock service.

Single source of truth. The ERP's Pydantic schemas mirror these field-for-field;
the JSON on the wire is what is documented in docs/HANDOFF.md section 5.

Pydantic v2 is used when available. A minimal shim is provided so the engine and
the zero-dependency demo runner can import this module in an environment without
pydantic installed — the field names and semantics are identical either way.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - exercised by whichever branch the env supports
    from pydantic import BaseModel, Field, field_validator

    PYDANTIC = True
except Exception:  # pragma: no cover
    PYDANTIC = False

    def Field(default=None, **kwargs):  # type: ignore
        return default

    def field_validator(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco

    import typing as _t

    def _coerce(value, annotation):
        """Recursively build nested models from plain dicts.

        Pydantic does this for free. Without it, ReplenishmentRequest(**json)
        leaves `items` as a list of dicts and every downstream `item.sku_id`
        raises AttributeError -- which is exactly the bug this shim caused
        the first time it was wired to the HTTP layer.
        """
        if annotation is None or value is None:
            return value
        origin = _t.get_origin(annotation)
        args = _t.get_args(annotation)
        if origin is _t.Union:                      # Optional[X] / Union[...]
            for arg in args:
                if arg is type(None):
                    continue
                try:
                    return _coerce(value, arg)
                except Exception:
                    continue
            return value
        if origin in (list, _t.List) and args:
            return [_coerce(v, args[0]) for v in value]
        if origin in (dict, _t.Dict):
            return value
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation(**value) if isinstance(value, dict) else value
        if isinstance(annotation, type) and issubclass(annotation, Enum) and not isinstance(value, Enum):
            try:
                return annotation(value)
            except ValueError:
                return value
        return value

    class BaseModel:  # type: ignore
        """Tiny stand-in supporting construction, nesting and dict export."""

        def __init__(self, **data: Any) -> None:
            ann: Dict[str, Any] = {}
            for klass in reversed(type(self).__mro__):
                ann.update(getattr(klass, "__annotations__", {}))
            # get_type_hints re-evaluates every annotation string on EVERY
            # construction. Profiling a 400-item request showed 35% of total
            # time in it. Resolve once per class and cache on the class.
            cls_ = type(self)
            hints = cls_.__dict__.get("_hint_cache")
            if hints is None:
                try:
                    hints = _t.get_type_hints(cls_)
                except Exception:
                    hints = {}
                try:
                    cls_._hint_cache = hints
                except Exception:
                    pass
            for key in ann:
                if key in data:
                    setattr(self, key, _coerce(data[key], hints.get(key)))
                elif hasattr(type(self), key):
                    default = getattr(type(self), key)
                    setattr(self, key, list(default) if isinstance(default, list) else default)
                else:
                    setattr(self, key, None)
            for key, val in data.items():
                if key not in ann:
                    setattr(self, key, val)

        def model_dump(self, **_: Any) -> Dict[str, Any]:
            def enc(v):
                if isinstance(v, BaseModel):
                    return v.model_dump()
                if isinstance(v, list):
                    return [enc(x) for x in v]
                if isinstance(v, dict):
                    return {k: enc(x) for k, x in v.items()}
                if isinstance(v, Enum):
                    return v.value
                if isinstance(v, (datetime, date)):
                    return v.isoformat()
                return v

            return {k: enc(v) for k, v in self.__dict__.items()}

        dict = model_dump


# ───────────────────────── enums ─────────────────────────

class Urgency(str, Enum):
    CRITICAL = "critical"   # projected stockout inside the lead time
    HIGH = "high"           # below reorder point
    MEDIUM = "medium"       # approaching reorder point
    LOW = "low"             # cycle replenishment
    NONE = "none"


class RecommendationAction(str, Enum):
    ORDER = "order"
    HOLD = "hold"
    REVIEW = "review"       # constraint conflict a human must resolve


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ───────────────────── ERP -> SmartStock ─────────────────────

class LeadTimeObservation(BaseModel):
    """One historical supplier delivery, in days from PO issue to receipt."""

    ordered_at: str
    received_at: str
    days: float


class SupplierRef(BaseModel):
    supplier_id: str
    name: Optional[str] = None
    contract_lead_days: Optional[float] = None
    contract_lead_cv: Optional[float] = None
    reliability_score: Optional[float] = None


class OrderPolicyConstraints(BaseModel):
    """Hard business constraints applied AFTER the continuous quantity."""

    moq: float = 0.0
    order_multiple: float = 1.0
    max_order_qty: Optional[float] = None
    max_inventory_position: Optional[float] = None
    shelf_life_days: Optional[int] = None


class SkuNodeState(BaseModel):
    """Everything SmartStock needs about one (SKU, location) at end of day."""

    sku_id: str
    node_id: str
    on_hand: float
    on_order: float = 0.0
    backorder: float = 0.0
    unit_cost: float = 0.0
    unit_price: float = 0.0

    # Provide EITHER demand_history (SmartStock derives mean/sigma) OR an
    # externally produced forecast. If both are present the forecast wins and
    # history is used only for segmentation.
    demand_history: Optional[List[float]] = None
    # Parallel to demand_history. True on days the SKU was out of stock, so the
    # observation is right-censored (sales < demand). Optional but strongly
    # recommended: without it the model trains on sales and learns to stay out
    # of stock.
    stocked_out_flags: Optional[List[bool]] = None
    forecast_mean: Optional[float] = None
    forecast_sigma: Optional[float] = None
    forecast_horizon_days: Optional[int] = None

    supplier: Optional[SupplierRef] = None
    lead_time_observations: Optional[List[float]] = None
    constraints: Optional[OrderPolicyConstraints] = None


class ReplenishmentRequest(BaseModel):
    """Nightly batch push from the ERP."""

    run_id: str
    as_of_date: str
    tenant_id: str = "default"
    items: List[SkuNodeState] = Field(default_factory=list)
    review_period_days: int = 1
    service_level_target: float = 0.95
    currency: str = "USD"
    policy_version: Optional[str] = None
    dry_run: bool = False


class OptimizeRequest(BaseModel):
    """Trigger a CMA-ES policy refit. Long-running; returns a job handle."""

    tenant_id: str = "default"
    items: List[SkuNodeState] = Field(default_factory=list)
    max_generations: int = 60
    seed: int = 42
    sku_batch_size: Optional[int] = None
    service_level_target: float = 0.95
    holding_rate_per_day: float = 0.0006
    stockout_multiple: float = 1.5
    order_fixed_cost: float = 25.0


# ───────────────────── SmartStock -> ERP ─────────────────────

class Rationale(BaseModel):
    """Everything the approval screen needs to justify the number to a human."""

    reorder_point: float
    order_up_to: float
    safety_stock: float
    cycle_stock: float
    inventory_position: float
    demand_over_leadtime: float
    sigma_demand_over_leadtime: float
    lead_time_mean_days: float
    lead_time_std_days: float
    lead_time_source: str
    implied_service_level: float
    days_of_cover_before: float
    days_of_cover_after: float
    projected_stockout_day: Optional[int] = None
    segment: str = ""
    binding_constraint: Optional[str] = None
    explanation: str = ""


class DraftPurchaseOrderLine(BaseModel):
    sku_id: str
    node_id: str
    recommended_qty: int                 # EXACT units — continuous action space
    unconstrained_qty: float             # pre-MOQ / pre-rounding, for transparency
    unit_cost: float
    line_value: float
    urgency: Urgency = Urgency.LOW
    action: RecommendationAction = RecommendationAction.ORDER
    confidence: float = 0.0
    rationale: Optional[Rationale] = None
    warnings: List[str] = Field(default_factory=list)


class DraftPurchaseOrder(BaseModel):
    draft_po_id: str
    supplier_id: str
    supplier_name: Optional[str] = None
    node_id: str
    expected_delivery_date: Optional[str] = None
    currency: str = "USD"
    total_value: float = 0.0
    line_count: int = 0
    lines: List[DraftPurchaseOrderLine] = Field(default_factory=list)


class ReplenishmentResponse(BaseModel):
    run_id: str
    as_of_date: str
    policy_version: str
    generated_at: str
    engine_version: str
    draft_purchase_orders: List[DraftPurchaseOrder] = Field(default_factory=list)
    skipped: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


class JobHandle(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.PENDING
    submitted_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress: float = 0.0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    engine_version: str
    policy_version: Optional[str] = None
    policy_fitted_at: Optional[str] = None
    n_segments: int = 0
    uptime_seconds: float = 0.0


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    run_id: Optional[str] = None
