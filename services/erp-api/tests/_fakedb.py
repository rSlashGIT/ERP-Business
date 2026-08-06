"""
Minimal in-memory stand-ins for SQLAlchemy / FastAPI / Pydantic.

Purpose: import the REAL api/v1/inventory.py and api/v1/dashboard.py and
execute their route functions, so cross-tenant isolation is proven by running
the shipped code rather than by reading it. SQLAlchemy, FastAPI and a database
are not installable in this environment (see services/smartstock/FROZEN.md).

WHAT THIS PROVES: every query the route builds carries the tenant predicate,
and the rows that survive filtering belong to exactly one tenant.

WHAT IT DOES NOT PROVE: that SQLAlchemy emits correct SQL, or that Postgres
executes it as expected. Those need a real database.

The engine records, per executed query, exactly which seeded rows survived the
predicates. The test asserts that set never contains a foreign tenant's row --
which is the property under test, independent of aggregate arithmetic.
"""
from __future__ import annotations

import sys
import types
from typing import Any, Callable, Dict, List, Optional, Sequence

EXECUTED: List[Dict[str, Any]] = []   # audit trail of every query executed


# ───────────────────────── expressions ─────────────────────────

class Pred:
    def __init__(self, fn: Callable[[Any], bool], desc: str) -> None:
        self.fn, self.desc = fn, desc

    def __call__(self, row: Any) -> bool:
        try:
            return bool(self.fn(row))
        except Exception:
            return True          # unresolvable predicate must not fake isolation

    def __or__(self, other: "Pred") -> "Pred":
        """SQL OR. Must be a real disjunction: returning True unconditionally
        would let a search filter appear to pass isolation checks for the wrong
        reason."""
        return Pred(lambda r: self(r) or other(r), f"({self.desc} OR {other.desc})")

    def __and__(self, other: "Pred") -> "Pred":
        return Pred(lambda r: self(r) and other(r), f"({self.desc} AND {other.desc})")

    def __invert__(self) -> "Pred":
        return Pred(lambda r: not self(r), f"NOT({self.desc})")

    def __repr__(self) -> str:
        return self.desc


class Col:
    """A model column that builds evaluable predicates."""

    def __init__(self, model: str, name: str) -> None:
        self.model, self.name = model, name

    def _get(self, row: Any) -> Any:
        """Resolve against the row, falling back to a joined relationship.

        The routers filter InventoryLevel rows on Product.is_active. Real SQL
        evaluates that against the JOINed row; this engine holds one model's
        rows, so a Product column is resolved through row.product. Without
        this every joined predicate silently evaluated False and the query
        returned nothing -- which would have made the isolation assertions
        pass vacuously.
        """
        if hasattr(row, self.name) and type(row).__name__ == self.model:
            return getattr(row, self.name)
        rel = getattr(row, self.model.lower(), None)
        if rel is not None and hasattr(rel, self.name):
            return getattr(rel, self.name)
        return getattr(row, self.name, None)

    def __eq__(self, other: Any) -> Pred:          # type: ignore[override]
        return Pred(lambda r: self._get(r) == other, f"{self.model}.{self.name}=={other!r}")

    def __ne__(self, other: Any) -> Pred:          # type: ignore[override]
        return Pred(lambda r: self._get(r) != other, f"{self.model}.{self.name}!={other!r}")

    def __le__(self, other: Any) -> Pred:
        o = other
        return Pred(lambda r: (self._get(r) or 0) <= (o._get(r) if isinstance(o, Col) else o),
                    f"{self.model}.{self.name}<=...")

    def __lt__(self, other: Any) -> Pred:
        return Pred(lambda r: (self._get(r) or 0) < other, f"{self.model}.{self.name}<...")

    def __ge__(self, other: Any) -> Pred:
        return Pred(lambda r: (self._get(r) or 0) >= other, f"{self.model}.{self.name}>=...")

    def __add__(self, other: Any) -> "Expr":
        return Expr(lambda r: (self._get(r) or 0) + (other._get(r) if isinstance(other, Col) else other))

    def __mul__(self, other: Any) -> "Expr":
        return Expr(lambda r: (self._get(r) or 0) * (other._get(r) if isinstance(other, Col) else other))

    def is_(self, v: Any) -> Pred:
        return Pred(lambda r: self._get(r) is v or self._get(r) == v, f"{self.model}.{self.name} is {v}")

    def isnot(self, v: Any) -> Pred:
        return Pred(lambda r: not (self._get(r) is v or self._get(r) == v), f"{self.model}.{self.name} isnot {v}")

    def in_(self, vals: Sequence[Any]) -> Pred:
        return Pred(lambda r: self._get(r) in vals, f"{self.model}.{self.name} in {len(vals)}")

    def ilike(self, pat: str) -> Pred:
        needle = pat.strip("%").lower()
        return Pred(lambda r: needle in str(self._get(r) or "").lower(), "ilike")

    def desc(self) -> "Col": return self
    def asc(self) -> "Col": return self
    def __or__(self, other: Any) -> Pred:
        return Pred(lambda r: True, "or")
    def __hash__(self) -> int:
        return hash((self.model, self.name))


class Expr:
    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self.fn = fn

    def __le__(self, other: Any) -> Pred:
        o = other
        return Pred(lambda r: self.fn(r) <= (o._get(r) if isinstance(o, Col) else o), "expr<=")


class Func:
    def __init__(self, kind: str, arg: Any = None, default: Any = 0) -> None:
        self.kind, self.arg, self.default = kind, arg, default


class _FuncNS:
    def count(self, *a: Any) -> Func: return Func("count")
    def sum(self, arg: Any = None) -> Func: return Func("sum", arg)
    def coalesce(self, arg: Any, default: Any = 0) -> Func:
        if isinstance(arg, Func):
            arg.default = default
            return arg
        return Func("coalesce", arg, default)
    def now(self) -> Any: return None


func = _FuncNS()


# ───────────────────────── select / session ─────────────────────────

class Select:
    def __init__(self, *entities: Any) -> None:
        self.entities = list(entities)
        self.preds: List[Pred] = []
        self.primary: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset = 0
        for e in entities:
            if isinstance(e, type) and hasattr(e, "__fakemodel__"):
                self.primary = e.__fakemodel__
                break
            if isinstance(e, Col):
                self.primary = e.model
                break

    # chainable no-ops
    def join(self, *a: Any, **k: Any) -> "Select": return self
    def options(self, *a: Any) -> "Select": return self
    def order_by(self, *a: Any) -> "Select": return self
    def with_for_update(self, *a: Any, **k: Any) -> "Select": return self
    def distinct(self) -> "Select": return self
    def subquery(self) -> "Select": return self

    def select_from(self, model: Any) -> "Select":
        if isinstance(model, type) and hasattr(model, "__fakemodel__"):
            self.primary = model.__fakemodel__
        elif isinstance(model, Select):
            # COUNT over a subquery. Real SQLAlchemy carries the subquery's
            # WHERE clause; inherit it, otherwise the count is computed over
            # the unfiltered table and reports another tenant's totals.
            self.primary = model.primary
            self.preds = list(model.preds) + self.preds
        return self

    def where(self, *preds: Any) -> "Select":
        for p in preds:
            if isinstance(p, Pred):
                self.preds.append(p)
                if self.primary is None:
                    self.primary = p.desc.split(".")[0]
        return self

    def limit(self, n: Optional[int]) -> "Select":
        self._limit = n; return self

    def offset(self, n: int) -> "Select":
        self._offset = n or 0; return self


def select(*entities: Any) -> Select:
    return Select(*entities)


class Result:
    def __init__(self, rows: List[Any], entities: List[Any]) -> None:
        self.rows, self.entities = rows, entities

    def _agg(self) -> List[Any]:
        out = []
        for e in self.entities:
            if isinstance(e, Func):
                if e.kind == "count":
                    out.append(len(self.rows))
                elif e.kind == "sum":
                    tot = 0.0
                    for r in self.rows:
                        if isinstance(e.arg, Col):
                            tot += float(e.arg._get(r) or 0)
                        elif isinstance(e.arg, Expr):
                            try: tot += float(e.arg.fn(r))
                            except Exception: pass
                    out.append(tot if self.rows else e.default)
                else:
                    out.append(e.default)
            elif isinstance(e, Col):
                out.append([e._get(r) for r in self.rows])
            else:
                out.append(self.rows)
        return out

    def scalars(self) -> "Result": return self
    def all(self) -> List[Any]:
        if any(isinstance(e, Func) for e in self.entities):
            return [tuple(self._agg())]
        if len(self.entities) == 1 and isinstance(self.entities[0], Col):
            c = self.entities[0]
            return [c._get(r) for r in self.rows]
        return self.rows
    def unique(self) -> "Result": return self
    def scalar_one(self) -> Any:
        a = self._agg(); return a[0] if a else 0
    def scalar_one_or_none(self) -> Any:
        if any(isinstance(e, Func) for e in self.entities):
            a = self._agg(); return a[0] if a else None
        if len(self.entities) == 1 and isinstance(self.entities[0], Col):
            c = self.entities[0]
            return c._get(self.rows[0]) if self.rows else None
        return self.rows[0] if self.rows else None
    def one(self) -> Any: return tuple(self._agg())
    def __iter__(self): return iter(self.rows)


class FakeSession:
    """Holds seeded rows per model and evaluates Select against them."""

    def __init__(self, tables: Dict[str, List[Any]]) -> None:
        self.tables = tables
        self.added: List[Any] = []
        self.committed = False

    async def execute(self, stmt: Select) -> Result:
        rows = list(self.tables.get(stmt.primary or "", []))
        survivors = [r for r in rows if all(p(r) for p in stmt.preds)]
        if stmt._offset:
            survivors = survivors[stmt._offset:]
        if stmt._limit is not None:
            survivors = survivors[: stmt._limit]
        EXECUTED.append({
            "model": stmt.primary,
            "predicates": [p.desc for p in stmt.preds],
            "tenant_filtered": any(".tenant_id==" in p.desc for p in stmt.preds),
            "rows": survivors,
            "tenants_returned": sorted({getattr(r, "tenant_id", "?") for r in survivors}),
        })
        return Result(survivors, stmt.entities)

    def add(self, obj: Any) -> None: self.added.append(obj)
    async def commit(self) -> None: self.committed = True
    async def rollback(self) -> None: pass


# ───────────────────────── module stubs ─────────────────────────

def install() -> None:
    """Install stub modules so the real routers import cleanly."""
    sa = types.ModuleType("sqlalchemy")
    sa.select, sa.func = select, func
    for n in ("String", "Integer", "Boolean", "Text", "Date", "DateTime", "Numeric",
              "BigInteger", "ForeignKey", "Index", "UniqueConstraint", "CheckConstraint",
              "Enum", "text"):
        setattr(sa, n, lambda *a, **k: None)
    orm = types.ModuleType("sqlalchemy.orm")
    orm.selectinload = lambda *a, **k: None
    orm.Mapped = object
    orm.mapped_column = lambda *a, **k: None
    orm.relationship = lambda *a, **k: None
    orm.DeclarativeBase = object
    aio = types.ModuleType("sqlalchemy.ext.asyncio")
    aio.AsyncSession = object
    ext = types.ModuleType("sqlalchemy.ext")
    ext.asyncio = aio
    sa.orm, sa.ext = orm, ext

    fa = types.ModuleType("fastapi")

    class APIRouter:
        def __init__(self, **k: Any) -> None: pass
        def _deco(self, *a: Any, **k: Any):
            def d(fn): return fn
            return d
        get = post = put = delete = _deco

    fa.APIRouter = APIRouter
    fa.Depends = lambda x=None: x
    fa.Query = lambda default=None, **k: default
    fa.Header = lambda default=None, **k: default
    fa.Body = lambda default=None, **k: default

    class HTTPException(Exception):
        def __init__(self, status_code: int = 400, detail: str = "", headers: Any = None):
            super().__init__(f"{status_code}: {detail}")
            self.status_code, self.detail = status_code, detail

    fa.HTTPException = HTTPException
    st = types.ModuleType("fastapi.status")
    st.HTTP_401_UNAUTHORIZED, st.HTTP_403_FORBIDDEN = 401, 403
    fa.status = st

    pyd = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items(): setattr(self, k, v)
    pyd.BaseModel = BaseModel
    pyd.Field = lambda default=None, **k: default
    def field_validator(*a: Any, **k: Any):
        def d(fn): return fn
        return d
    pyd.field_validator = field_validator

    sys.modules.update({
        "sqlalchemy": sa, "sqlalchemy.orm": orm,
        "sqlalchemy.ext": ext, "sqlalchemy.ext.asyncio": aio,
        "fastapi": fa, "fastapi.status": st, "pydantic": pyd,
    })


class EnumVal(str):
    """A str that also answers `.value`, matching SQLAlchemy Enum columns.

    The routers do `run.status.value` / `po.status.value`, so a plain string
    seed raises AttributeError. Subclassing str keeps comparisons working
    while satisfying the .value access.
    """
    @property
    def value(self) -> str:
        return str(self)


def make_models() -> types.ModuleType:
    """Lightweight stand-ins for app.db.models with the columns the routes use."""
    m = types.ModuleType("app.db.models")

    def mk(name: str, cols: Sequence[str]) -> type:
        ns: Dict[str, Any] = {"__fakemodel__": name}
        for c in cols:
            ns[c] = Col(name, c)
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items(): setattr(self, k, v)
        ns["__init__"] = __init__
        return type(name, (), ns)

    common = ["id", "tenant_id"]
    m.InventoryLevel = mk("InventoryLevel", common + [
        "product_id", "location_id", "on_hand", "on_order", "reserved", "backorder",
        "reorder_point", "order_up_to", "safety_stock",
        # relationship attributes: selectinload() references them on the CLASS,
        # while the route reads them on the INSTANCE (i.product.sku). Seeded
        # rows overwrite these with real joined objects.
        "product", "location"])
    # `available` and `inventory_position` are @property on the real model.
    m.InventoryLevel.available = property(
        lambda self: (self.on_hand or 0) - (self.reserved or 0))
    m.InventoryLevel.inventory_position = property(
        lambda self: (self.on_hand or 0) + (self.on_order or 0) - (self.backorder or 0))
    m.Product = mk("Product", common + [
        "sku", "name", "unit_cost", "unit_price", "is_active", "deleted_at",
        "style_id", "size", "size_seq", "colour", "barcode", "style"])
    m.ProductStyle = mk("ProductStyle", common + [
        "style_code", "name", "brand", "category", "season", "hsn_code", "is_active"])
    m.Location = mk("Location", common + ["code", "name", "is_active"])
    m.StockMovement = mk("StockMovement", common + [
        "product_id", "location_id", "movement_type", "quantity", "occurred_at",
        "reference_type", "reference_id", "idempotency_key"])
    m.AuditLog = mk("AuditLog", common + [
        "entity_type", "entity_id", "action", "actor", "before", "after"])
    m.Recommendation = mk("Recommendation", common + [
        "run_id", "status", "urgency", "line_value", "recommended_qty", "final_qty",
        "decided_at", "product_id", "location_id", "supplier_id"])
    m.PurchaseOrder = mk("PurchaseOrder", common + ["status", "total_value", "po_number"])
    m.ReplenishmentRun = mk("ReplenishmentRun", common + [
        "run_date", "status", "policy_version", "lines_recommended", "duration_ms",
        "error", "created_at"])

    class _E:
        PENDING = EnumVal("pending")
    m.RecommendationStatus = _E
    class _P:
        APPROVED, SENT, PARTIALLY_RECEIVED = (
            EnumVal("approved"), EnumVal("sent"), EnumVal("partially_received"))
    m.POStatus = _P
    class _M:
        ADJUSTMENT = EnumVal("adjustment")
    m.MovementType = _M

    # Enums the routers import but do not exercise in these tests. Present so
    # the real module's import list resolves; values match db/models.py.
    class _RunStatus:
        QUEUED, RUNNING, SUCCEEDED, FAILED, PARTIAL = (
            EnumVal("queued"), EnumVal("running"), EnumVal("succeeded"),
            EnumVal("failed"), EnumVal("partial"))
    m.RunStatus = _RunStatus

    class _LocationType:
        DISTRIBUTION_CENTER, STORE, TRANSIT, SUPPLIER = (
            "distribution_center", "store", "transit", "supplier")
    m.LocationType = _LocationType

    m.PurchaseOrderLine = mk("PurchaseOrderLine", common + [
        "purchase_order_id", "product_id", "line_no", "ordered_qty", "unit_cost",
        "line_value", "ai_recommended_qty"])
    m.Supplier = mk("Supplier", common + ["code", "name"])
    m.SupplierProduct = mk("SupplierProduct", common + ["supplier_id", "product_id"])
    m.DemandHistory = mk("DemandHistory", common + [
        "product_id", "location_id", "bucket_date", "quantity", "was_stocked_out"])
    m.GoodsReceipt = mk("GoodsReceipt", common + ["purchase_order_line_id", "received_qty"])
    m.LeadTimeObservation = mk("LeadTimeObservation", common + [
        "supplier_id", "product_id", "lead_days"])
    m.PolicyParameter = mk("PolicyParameter", ["id", "policy_version", "segment"])
    m.EnumVal = EnumVal
    return m
