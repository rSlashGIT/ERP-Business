"""
Recommendation orchestration: ReplenishmentRequest -> DraftPurchaseOrder[].

This is the synchronous path the ERP calls nightly. It does NOT run CMA-ES —
fitting is a separate, slower job (training/fit.py) whose output is a small
parameter matrix. Serving is: look up segment, evaluate closed-form (s,S),
project constraints, explain. That is microseconds per SKU, so a 50k-line
request is bounded by JSON parsing, not by the policy.

Every returned line carries a full Rationale so the human approving it can see
exactly why the number is what it is. This is a hard requirement: an
unexplained AI quantity does not get approved by a procurement manager twice.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..contracts import (
    DraftPurchaseOrder,
    DraftPurchaseOrderLine,
    OrderPolicyConstraints,
    Rationale,
    RecommendationAction,
    ReplenishmentRequest,
    ReplenishmentResponse,
    SkuNodeState,
    Urgency,
)
from . import policy as P
from .leadtime import fit_profile
from .forecast import decensor, forecast_for
from .segmentation import GLOBAL_SEGMENT, DemandStats, build_stats, classify_demand

logger = logging.getLogger(__name__)

ENGINE_VERSION = "2.2.1"
MIN_HISTORY_FOR_SEGMENTATION = 14


class PolicyStore:
    """Holds fitted segment parameters. Thread-safe for read-mostly access.

    Falls back to the classical (s,S) default for any segment it has not seen,
    so a cold service still produces defensible orders on its first request
    instead of erroring or emitting zeros.
    """

    def __init__(self) -> None:
        self._params: Dict[str, np.ndarray] = {}
        self.policy_version: str = "default-classical"
        self.fitted_at: Optional[str] = None

    def load(self, params: Dict[str, Sequence[float]], version: Optional[str] = None) -> None:
        clean: Dict[str, np.ndarray] = {}
        for seg, raw in params.items():
            arr = np.asarray(raw, dtype=np.float64).reshape(-1)
            if arr.size != P.N_PARAMS:
                raise ValueError(
                    f"segment {seg}: expected {P.N_PARAMS} params, got {arr.size}"
                )
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"segment {seg}: non-finite parameters")
            clean[seg] = arr
        self._params = clean
        self.fitted_at = datetime.now(timezone.utc).isoformat()
        self.policy_version = version or self._hash(clean)

    @staticmethod
    def _hash(params: Dict[str, np.ndarray]) -> str:
        h = hashlib.sha256()
        for seg in sorted(params):
            h.update(seg.encode())
            h.update(params[seg].tobytes())
        return "fit-" + h.hexdigest()[:12]

    def get(self, segment: str) -> np.ndarray:
        if segment in self._params:
            return self._params[segment]
        parent = segment.split(":")[0] + ":*"
        if parent in self._params:
            return self._params[parent]
        if GLOBAL_SEGMENT in self._params:
            return self._params[GLOBAL_SEGMENT]
        return P.DEFAULT_RAW

    @property
    def segments(self) -> List[str]:
        return sorted(self._params)

    def describe(self) -> Dict[str, Dict[str, float]]:
        return {seg: P.describe(raw) for seg, raw in self._params.items()}


def _derive_demand(item: SkuNodeState) -> Tuple[float, float, float, str]:
    """Return (mean, sigma, intermittency, segment) for one item.

    Precedence: explicit forecast > derived from history > zero.
    Zero-demand SKUs are legitimate (discontinued lines still sitting in the
    master) and must produce action=HOLD, not a division-by-zero.
    """
    hist = item.demand_history or []
    if len(hist) >= MIN_HISTORY_FOR_SEGMENTATION:
        arr = np.asarray(hist, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        # De-censor BEFORE anything else: on stockout days recorded sales are a
        # lower bound on demand, and fitting to them teaches the model to stay
        # out of stock.
        arr = decensor(arr, getattr(item, "stocked_out_flags", None))
        cls, adi, cv2 = classify_demand(arr)
        derived_mean, derived_sigma, model_name = forecast_for(arr, horizon=1)
        intermittency = float(1.0 - (arr > 0).mean()) if arr.size else 1.0
        segment = f"{cls.value}:mid"
    else:
        derived_mean = derived_sigma = 0.0
        intermittency = 0.0
        segment = GLOBAL_SEGMENT
        model_name = "insufficient_history"

    mean = item.forecast_mean if item.forecast_mean is not None else derived_mean
    sigma = item.forecast_sigma if item.forecast_sigma is not None else derived_sigma
    mean = max(0.0, float(mean or 0.0))
    # A forecast with no stated uncertainty is a forecast you cannot size safety
    # stock from. Assume Poisson-ish sigma = sqrt(mean) rather than zero, which
    # would drive safety stock to 0 and guarantee stockouts.
    if sigma is None or not math.isfinite(sigma) or sigma <= 0:
        sigma = math.sqrt(mean) if mean > 0 else 0.0
    return mean, float(sigma), intermittency, segment


def _urgency(ip: float, s: float, d_hat: float, lt_mean: float) -> Tuple[Urgency, Optional[int]]:
    """Classify urgency and project the stockout day."""
    if d_hat <= 1e-9:
        return Urgency.NONE, None
    days_cover = ip / d_hat if d_hat > 0 else math.inf
    stockout_day = int(math.floor(days_cover)) if math.isfinite(days_cover) else None
    if days_cover < lt_mean:
        return Urgency.CRITICAL, stockout_day
    if ip <= s:
        return Urgency.HIGH, stockout_day
    if ip <= s * 1.25:
        return Urgency.MEDIUM, stockout_day
    return Urgency.LOW, stockout_day


def _confidence(n_obs: int, n_hist: int, sigma: float, mean: float) -> float:
    """Heuristic confidence in [0,1].

    Three independent signals, multiplied because they are all necessary:
      * lead-time evidence   (how many receipts back the LT distribution)
      * demand evidence      (how much history backs the forecast)
      * demand stability     (CV; a CV of 3 means any point forecast is weak)
    Deliberately conservative. A confident-looking wrong number is worse than
    an honestly uncertain one on a screen a human is about to sign.
    """
    lt_conf = min(1.0, n_obs / 12.0) * 0.6 + 0.4 if n_obs > 0 else 0.35
    hist_conf = min(1.0, n_hist / 90.0) * 0.7 + 0.3
    cv = (sigma / mean) if mean > 1e-9 else 3.0
    stab = 1.0 / (1.0 + max(0.0, cv - 0.3))
    return round(float(np.clip(lt_conf * hist_conf * stab, 0.0, 1.0)), 3)


def _binding_constraint(
    raw_q: float, final_q: float, c: OrderPolicyConstraints, headroom: float
) -> Optional[str]:
    if final_q <= 0 and raw_q > 0:
        return "moq_not_met" if raw_q < c.moq else "capacity_full"
    if c.max_order_qty is not None and abs(final_q - c.max_order_qty) < 1e-6:
        return "max_order_qty"
    if abs(final_q - headroom) < 1e-6 and math.isfinite(headroom):
        return "max_inventory_position"
    if c.moq > 0 and abs(final_q - c.moq) < 1e-6 and raw_q < c.moq:
        return "moq"
    if c.order_multiple > 1 and final_q > raw_q + 1e-9:
        return "order_multiple"
    return None


def generate(
    request: ReplenishmentRequest,
    store: PolicyStore,
    default_lead_days: float = 7.0,
    default_lead_cv: float = 0.35,
) -> ReplenishmentResponse:
    """Produce draft POs for a whole replenishment request."""
    now = datetime.now(timezone.utc)
    as_of = request.as_of_date or now.date().isoformat()
    lines_by_supplier: Dict[Tuple[str, str], List[DraftPurchaseOrderLine]] = defaultdict(list)
    supplier_meta: Dict[str, Dict[str, object]] = {}
    skipped: List[Dict[str, object]] = []

    n_order = n_hold = 0
    total_value = 0.0
    review = max(1, int(request.review_period_days or 1))

    for item in request.items or []:
        try:
            if not item.sku_id or not item.node_id:
                skipped.append({"sku_id": item.sku_id, "reason": "missing sku_id or node_id"})
                continue

            on_hand = float(item.on_hand or 0.0)
            on_order = float(item.on_order or 0.0)
            backorder = float(item.backorder or 0.0)
            if not all(math.isfinite(v) for v in (on_hand, on_order, backorder)):
                skipped.append({"sku_id": item.sku_id, "reason": "non-finite stock values"})
                continue
            if on_hand < 0:
                # Negative on-hand is a data integrity problem upstream. Treat as
                # zero for the policy but surface it, do not silently absorb it.
                logger.warning("negative on_hand for %s@%s: %s", item.sku_id, item.node_id, on_hand)

            d_hat, sigma_d, interm, segment = _derive_demand(item)

            sup = item.supplier
            supplier_id = sup.supplier_id if sup else "UNASSIGNED"
            contract = sup.contract_lead_days if sup else None
            contract_cv = (sup.contract_lead_cv if sup else None) or default_lead_cv
            obs = item.lead_time_observations or []
            profile = fit_profile(
                supplier_id=supplier_id,
                sku_id=item.sku_id,
                node_id=item.node_id,
                observations=obs,
                contract_days=contract if contract is not None else default_lead_days,
                contract_cv=contract_cv,
            )

            raw = store.get(segment)
            params = P.unpack(raw)

            s_arr, S_arr, safety_arr = P.target_levels(
                params[None, :],
                np.array([d_hat]),
                np.array([sigma_d]),
                np.array([profile.mean_days]),
                np.array([profile.std_days]),
                review_period=review,
                intermittency=np.array([interm]),
            )
            s = float(s_arr[0]); S = float(S_arr[0]); safety = float(safety_arr[0])

            c = item.constraints or OrderPolicyConstraints()
            max_order = c.max_order_qty if c.max_order_qty is not None else math.inf
            max_pos = c.max_inventory_position if c.max_inventory_position is not None else math.inf
            # Shelf life caps how much you can hold without spoilage.
            if c.shelf_life_days and d_hat > 0:
                max_pos = min(max_pos, d_hat * c.shelf_life_days)

            cons = P.OrderConstraints(
                moq=np.array([c.moq or 0.0]),
                order_multiple=np.array([c.order_multiple or 1.0]),
                max_order=np.array([max_order]),
                max_position=np.array([max_pos]),
            )
            ip = max(on_hand, 0.0) + on_order - backorder
            raw_q = float(P.order_quantity(np.array([ip]), s_arr, S_arr, None, integer=False)[0])
            qty = int(P.order_quantity(np.array([ip]), s_arr, S_arr, cons, integer=True)[0])

            urgency, stockout_day = _urgency(ip, s, d_hat, profile.mean_days)
            headroom = max_pos - ip
            binding = _binding_constraint(raw_q, float(qty), c, headroom)

            action = RecommendationAction.ORDER if qty > 0 else RecommendationAction.HOLD
            warnings: List[str] = []
            if urgency == Urgency.CRITICAL and qty <= 0:
                # Wants stock, cannot order: a human must break the tie.
                action = RecommendationAction.REVIEW
                warnings.append(
                    f"projected stockout in {stockout_day}d but order blocked by {binding or 'constraint'}"
                )
            if profile.n_observations == 0:
                warnings.append("no lead-time history; using contract value with wide prior")
            if d_hat <= 0:
                warnings.append("zero forecast demand; SKU may be discontinued")
            if on_hand < 0:
                warnings.append(f"negative on-hand ({on_hand:g}) reported by ERP")

            if action == RecommendationAction.HOLD and qty <= 0:
                n_hold += 1
                continue

            unit_cost = float(item.unit_cost or 0.0)
            z_eff = safety / max(1e-9, math.sqrt(
                max(profile.mean_days, 1e-9) * sigma_d ** 2 + d_hat ** 2 * profile.std_days ** 2
            )) if safety > 0 else 0.0

            rationale = Rationale(
                reorder_point=round(s, 2),
                order_up_to=round(S, 2),
                safety_stock=round(safety, 2),
                cycle_stock=round(max(0.0, S - s), 2),
                inventory_position=round(ip, 2),
                demand_over_leadtime=round(d_hat * profile.mean_days, 2),
                sigma_demand_over_leadtime=round(
                    math.sqrt(max(profile.mean_days * sigma_d ** 2
                                  + d_hat ** 2 * profile.std_days ** 2, 0.0)), 2),
                lead_time_mean_days=round(profile.mean_days, 2),
                lead_time_std_days=round(profile.std_days, 2),
                lead_time_source=profile.source,
                implied_service_level=round(float(P.implied_service_level(np.array([z_eff]))[0]), 4),
                days_of_cover_before=round(ip / d_hat, 1) if d_hat > 0 else -1.0,
                days_of_cover_after=round((ip + qty) / d_hat, 1) if d_hat > 0 else -1.0,
                projected_stockout_day=stockout_day,
                segment=segment,
                binding_constraint=binding,
                explanation=(
                    f"Demand {d_hat:.1f}/day (sigma {sigma_d:.1f}); supplier lead "
                    f"{profile.mean_days:.1f}+/-{profile.std_days:.1f}d from {profile.source} "
                    f"({profile.n_observations} receipts). Reorder point {s:.0f} "
                    f"= {d_hat * profile.mean_days:.0f} lead-time demand + {safety:.0f} safety. "
                    f"Position {ip:.0f} is {'below' if ip <= s else 'above'} it; "
                    f"order-up-to {S:.0f} implies {raw_q:.0f} units"
                    + (f", adjusted to {qty} by {binding}." if binding else ".")
                ),
            )

            line = DraftPurchaseOrderLine(
                sku_id=item.sku_id,
                node_id=item.node_id,
                recommended_qty=qty,
                unconstrained_qty=round(raw_q, 2),
                unit_cost=unit_cost,
                line_value=round(qty * unit_cost, 2),
                urgency=urgency,
                action=action,
                confidence=_confidence(
                    profile.n_observations, len(item.demand_history or []), sigma_d, d_hat
                ),
                rationale=rationale,
                warnings=warnings,
            )
            lines_by_supplier[(supplier_id, item.node_id)].append(line)
            supplier_meta[supplier_id] = {
                "name": (sup.name if sup else None),
                "lead": profile.mean_days,
            }
            n_order += 1
            total_value += line.line_value

        except Exception as exc:  # one bad SKU must not fail a 50k-line run
            logger.exception("recommendation failed for %s", getattr(item, "sku_id", "?"))
            skipped.append({"sku_id": getattr(item, "sku_id", None), "reason": str(exc)})

    drafts: List[DraftPurchaseOrder] = []
    for (supplier_id, node_id), lines in sorted(lines_by_supplier.items()):
        lines.sort(key=lambda l: (
            ["critical", "high", "medium", "low", "none"].index(
                l.urgency.value if hasattr(l.urgency, "value") else str(l.urgency)
            ),
            -l.line_value,
        ))
        meta = supplier_meta.get(supplier_id, {})
        lead = float(meta.get("lead") or default_lead_days)
        drafts.append(
            DraftPurchaseOrder(
                draft_po_id=f"DPO-{request.run_id}-{supplier_id}-{node_id}",
                supplier_id=supplier_id,
                supplier_name=meta.get("name"),
                node_id=node_id,
                expected_delivery_date=(now + timedelta(days=lead)).date().isoformat(),
                currency=request.currency,
                total_value=round(sum(l.line_value for l in lines), 2),
                line_count=len(lines),
                lines=lines,
            )
        )
    drafts.sort(key=lambda d: -d.total_value)

    return ReplenishmentResponse(
        run_id=request.run_id,
        as_of_date=as_of,
        policy_version=store.policy_version,
        generated_at=now.isoformat(),
        engine_version=ENGINE_VERSION,
        draft_purchase_orders=drafts,
        skipped=skipped,
        stats={
            "items_received": len(request.items or []),
            "lines_recommended": n_order,
            "items_held": n_hold,
            "items_skipped": len(skipped),
            "draft_po_count": len(drafts),
            "total_value": round(total_value, 2),
            "critical_lines": sum(
                1 for d in drafts for l in d.lines
                if (l.urgency.value if hasattr(l.urgency, "value") else l.urgency) == "critical"
            ),
        },
    )
