"""
Continuous (s, S) replenishment policy.

UPGRADE 3 OF 4: CONTINUOUS ACTION SPACE
---------------------------------------
Legacy behaviour (hybrid_policy.py):

    return int(np.clip(round(qty), 0, 7))        # <- an ACTION INDEX

...which environment.py then looked up in

    action_map = {0:0, 1:50, 2:150, 3:300, 4:500, 5:750, 6:1000, 7:1500}

So the model could never say "order 214". It could say "order bucket 3",
meaning 300. Two consequences, both bad:
  1. Quantisation error of up to +/-250 units on every single order. On a
     SKU selling 30/day that is eight days of demand of pure noise.
  2. The gradient signal seen by CMA-ES was a step function. Small parameter
     changes produced zero fitness change until a bucket boundary was
     crossed, which is precisely the landscape evolution strategies handle
     worst.

Now the policy emits a real-valued quantity and applies genuine business
constraints (MOQ, case pack, max order, shelf-life cap) as a post-processing
projection. The output is an exact unit count.

PARAMETERISATION
----------------
10 parameters PER SEGMENT (not per SKU — see core/segmentation.py). CMA-ES
searches unbounded R^10; `unpack` maps to interpretable bounded values via a
smooth squash so the optimiser never has to deal with hard walls.

    idx name              range          meaning
    --- ----------------- -------------- ------------------------------------
     0  z_safety          [0.0,  4.0]    safety factor on sigma_DL (~service level)
     1  lt_bias           [0.5,  2.0]    multiplier on E[L]; learns systematic
                                         supplier optimism in the contract data
     2  w_sigma_lt        [0.0,  2.0]    extra safety per unit of lead-time CV
     3  w_trend           [-1.0, 2.0]    responsiveness to demand trend
     4  cover_days        [1.0, 45.0]    cycle stock, in days of demand
     5  w_cover_cv        [-1.0, 2.0]    cover adjustment by demand CV
     6  w_intermittency   [-1.0, 2.0]    adjustment for zero-heavy demand
     7  max_cover_days    [7.0,120.0]    hard cap on inventory position (days)
     8  w_holding         [0.0,  2.0]    leanness response to holding/stockout ratio
     9  review_bias       [-0.5, 1.5]    coverage of the review period R

Every one of these is human-readable, which is what makes the "Why this
quantity?" panel in the ERP approval screen possible without a post-hoc
explainer model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

N_PARAMS = 10

PARAM_NAMES: Tuple[str, ...] = (
    "z_safety", "lt_bias", "w_sigma_lt", "w_trend", "cover_days",
    "w_cover_cv", "w_intermittency", "max_cover_days", "w_holding", "review_bias",
)

PARAM_BOUNDS: np.ndarray = np.array(
    [
        [0.0, 4.0],
        [0.5, 2.0],
        [0.0, 2.0],
        [-1.0, 2.0],
        [1.0, 45.0],
        [-1.0, 2.0],
        [-1.0, 2.0],
        [7.0, 120.0],
        [0.0, 2.0],
        [-0.5, 1.5],
    ],
    dtype=np.float64,
)

# theta_0: a sane classical (s,S). z=1.64 is ~95% cycle service level,
# 14 days of cycle stock, no learned adjustments. CMA-ES starts here so a
# failed fit degrades to textbook behaviour rather than to nonsense.
DEFAULT_RAW = np.array([-0.3588, -0.6931, -3.6636, -0.6931, -0.869, -0.6931, -0.6931, -0.1241, -3.6636, -1.0986])


def _squash(raw: np.ndarray) -> np.ndarray:
    """Map unbounded R -> [0,1] with a logistic. Smooth, monotone, no walls."""
    return 1.0 / (1.0 + np.exp(-np.clip(raw, -30.0, 30.0)))


def unpack(raw_theta: np.ndarray) -> np.ndarray:
    """(..., N_PARAMS) unbounded -> (..., N_PARAMS) bounded interpretable params."""
    raw = np.asarray(raw_theta, dtype=np.float64)
    if raw.shape[-1] != N_PARAMS:
        raise ValueError(f"expected last dim {N_PARAMS}, got {raw.shape[-1]}")
    u = _squash(raw)
    lo = PARAM_BOUNDS[:, 0]
    hi = PARAM_BOUNDS[:, 1]
    return lo + u * (hi - lo)


def describe(raw_theta: np.ndarray) -> Dict[str, float]:
    """Human-readable parameter dict for one segment. Used by the API and UI."""
    p = unpack(np.asarray(raw_theta, dtype=np.float64).reshape(-1))
    return {name: round(float(v), 4) for name, v in zip(PARAM_NAMES, p)}


@dataclass
class OrderConstraints:
    """Per-SKU business constraints applied after the continuous quantity.

    All arrays broadcast against the (..., n_sku) quantity array.
      moq            minimum order quantity; an order below this is either
                     raised to MOQ or dropped to zero, whichever is cheaper
                     against the shortfall — see `apply`.
      order_multiple case / pallet pack size. Quantity rounds UP to a multiple.
      max_order      supplier or budget cap. Hard clip.
      max_position   capacity or shelf-life cap on on-hand + on-order.
    """

    moq: np.ndarray
    order_multiple: np.ndarray
    max_order: np.ndarray
    max_position: np.ndarray

    @staticmethod
    def none(n: int) -> "OrderConstraints":
        return OrderConstraints(
            moq=np.zeros(n),
            order_multiple=np.ones(n),
            max_order=np.full(n, np.inf),
            max_position=np.full(n, np.inf),
        )


def target_levels(
    params: np.ndarray,        # (..., n_sku, N_PARAMS) bounded
    d_hat: np.ndarray,         # (..., n_sku) forecast mean demand / day
    sigma_d: np.ndarray,       # (..., n_sku) forecast sigma / day
    lt_mean: np.ndarray,       # (..., n_sku) E[lead time] days
    lt_std: np.ndarray,        # (..., n_sku) sd[lead time] days
    review_period: float = 1.0,
    trend: Optional[np.ndarray] = None,
    intermittency: Optional[np.ndarray] = None,
    holding_ratio: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute (s, S, safety_stock). Fully vectorised, no Python loops.

    Returns arrays broadcast to the shape of d_hat.
    """
    z = params[..., 0]
    lt_bias = params[..., 1]
    w_sig_lt = params[..., 2]
    w_trend = params[..., 3]
    cover = params[..., 4]
    w_cov_cv = params[..., 5]
    w_interm = params[..., 6]
    max_cover = params[..., 7]
    w_hold = params[..., 8]
    review_bias = params[..., 9]

    eps = 1e-9
    d_hat = np.maximum(d_hat, 0.0)
    sigma_d = np.maximum(sigma_d, 0.0)
    lt_mean = np.maximum(lt_mean, 0.0)
    lt_std = np.maximum(lt_std, 0.0)

    # Effective protection window: lead time (bias-corrected) + review period.
    L_eff = lt_bias * lt_mean + review_bias * review_period
    L_eff = np.maximum(L_eff, 0.0)

    # sigma of demand over the protection window (Silver-Pyke-Peterson).
    # THIS is where stochastic lead time enters the policy.
    sigma_DL = np.sqrt(
        np.maximum(L_eff * np.square(sigma_d) + np.square(d_hat) * np.square(lt_std), 0.0)
    )

    lt_cv = lt_std / np.maximum(lt_mean, eps)
    interm = intermittency if intermittency is not None else np.zeros_like(d_hat)
    z_eff = z + w_sig_lt * lt_cv + w_interm * interm
    z_eff = np.maximum(z_eff, 0.0)

    safety = z_eff * sigma_DL

    trend_adj = (w_trend * trend) if trend is not None else 0.0
    s = L_eff * d_hat * (1.0 + np.clip(trend_adj, -0.5, 1.0)) + safety

    d_cv = sigma_d / np.maximum(d_hat, eps)
    cover_eff = cover * (1.0 + w_cov_cv * np.clip(d_cv, 0.0, 3.0))
    if holding_ratio is not None:
        # Expensive-to-hold items get leaner cycle stock.
        cover_eff = cover_eff / (1.0 + w_hold * np.clip(holding_ratio, 0.0, 5.0))
    cover_eff = np.maximum(cover_eff, 0.0)

    S = s + cover_eff * d_hat
    S = np.minimum(S, np.maximum(max_cover * d_hat, s))   # never cap below s

    # DEGENERATE-CAP GUARD. When the reorder point itself exceeds the
    # max_cover_days ceiling -- which happens on slow movers behind an
    # unreliable supplier, where safety stock dominates (M5 SKU FOODS_3_808:
    # d=0.35/day, LT 17.2+/-5.8 -> s=142.7 against a 60-day cap of 21) -- the
    # clamp above collapses S onto s. An (s,S) policy with S == s orders exactly
    # up to the trigger point, so the very next review re-triggers it: a stream
    # of tiny orders, each paying the fixed ordering cost. Guarantee at least
    # one review period of cycle stock so the policy cannot chatter.
    min_cycle = np.maximum(d_hat * np.maximum(review_period, 1.0), 1.0)
    S = np.maximum(S, s + min_cycle)

    # ZERO-DEMAND GUARD. Safety stock exists to absorb variability in demand
    # that EXISTS. With d_hat == 0 there is nothing to protect, so both targets
    # collapse to zero and the (s,S) rule holds rather than orders.
    #
    # This guard is load-bearing because forecast_for floors sigma at the
    # long-run spread when a short window goes flat (see core/forecast.py).
    # Without the guard, a discontinued SKU gets d_hat = 0 but sigma > 0, which
    # yields s = z * sigma_DL > 0 and orders stock for a dead line -- measured
    # at 16 units on a 300-day-then-dead series before this was added. When the
    # SKU genuinely resumes, d_hat rises above zero and the floored sigma then
    # supplies proper safety stock on the first real order.
    alive = d_hat > 1e-9
    s = np.where(alive, s, 0.0)
    S = np.where(alive, S, 0.0)
    safety = np.where(alive, safety, 0.0)
    return s, S, safety


def order_quantity(
    inventory_position: np.ndarray,   # on_hand + on_order - backorder
    s: np.ndarray,
    S: np.ndarray,
    constraints: Optional[OrderConstraints] = None,
    integer: bool = True,
) -> np.ndarray:
    """CONTINUOUS order quantity with real-world constraint projection.

    Returns exact unit counts (e.g. 214), not bucket indices.

    Constraint order matters and is deliberate:
      1. raw = max(0, S - IP) when IP <= s, else 0        (the (s,S) rule)
      2. position cap: never exceed max_position
      3. case pack: round UP to the next multiple
      4. MOQ: if 0 < q < moq, raise to moq ONLY if the shortfall is more than
         half the MOQ; otherwise drop to 0. Blindly raising to MOQ on a
         1-unit shortfall is how systems accumulate dead stock.
      5. max_order: hard clip (supplier/budget ceiling)
      6. integer rounding
    """
    ip = np.asarray(inventory_position, dtype=np.float64)
    s = np.asarray(s, dtype=np.float64)
    S = np.asarray(S, dtype=np.float64)

    trigger = ip <= s
    raw = np.where(trigger, np.maximum(S - ip, 0.0), 0.0)

    if constraints is None:
        return np.rint(raw) if integer else raw

    headroom = np.maximum(constraints.max_position - ip, 0.0)
    q = np.minimum(raw, headroom)

    mult = np.maximum(constraints.order_multiple, 1e-9)
    finite_mult = np.isfinite(mult) & (mult > 1.0 + 1e-12)
    q = np.where(finite_mult, np.ceil(q / mult) * mult, q)
    q = np.minimum(q, headroom)   # rounding up must not breach the cap

    moq = np.maximum(constraints.moq, 0.0)
    below = (q > 0) & (q < moq)
    worth_it = q >= 0.5 * moq
    q = np.where(below & worth_it, moq, q)
    q = np.where(below & ~worth_it, 0.0, q)

    q = np.minimum(q, np.maximum(constraints.max_order, 0.0))
    q = np.maximum(q, 0.0)
    q = np.nan_to_num(q, nan=0.0, posinf=0.0, neginf=0.0)
    return np.rint(q) if integer else q


def implied_service_level(z: np.ndarray) -> np.ndarray:
    """Cycle service level implied by a safety factor, via the normal CDF.

    Abramowitz-Stegun 7.1.26 rational approximation for erf — avoids a scipy
    dependency in the hot path. Max abs error 1.5e-7, far below what matters
    for a service-level readout.
    """
    z = np.asarray(z, dtype=np.float64)
    x = z / np.sqrt(2.0)
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-ax * ax)
    erf = sign * y
    return np.clip(0.5 * (1.0 + erf), 0.0, 1.0)
