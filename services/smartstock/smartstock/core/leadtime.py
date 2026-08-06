"""
Stochastic lead-time estimation.

UPGRADE 2 OF 4: the legacy engine hard-coded lead times —
    environment.py           : 'Lead times': 2.0
    multi_echelon.py         : LEAD_SUPPLIER_TO_WAREHOUSE = 5
                               LEAD_WAREHOUSE_TO_STORE    = 2

Those constants are now gone. Lead time is a *distribution* per
(supplier, sku, destination_node), estimated from the ERP's own goods-receipt
history, and it feeds the policy through both its mean AND its variance.

WHY VARIANCE MATTERS
--------------------
Safety stock must cover demand over the lead time. If both demand and lead
time are random and independent, the standard deviation of demand over lead
time is

    sigma_DL = sqrt( E[L] * sigma_d^2  +  E[d]^2 * sigma_L^2 )

The second term is the one the legacy code threw away by fixing L. For a
supplier whose lead time swings between 3 and 21 days that term dominates,
and a policy blind to it will stock out roughly every time the supplier is
late. Reference: Silver, Pyke & Peterson, "Inventory Management and
Production Planning and Scheduling", ch. 7.

ESTIMATION AND SHRINKAGE
------------------------
Real ERP data gives you very few observations per (supplier, SKU) pair. A
raw sample std over 2 receipts is noise. We shrink the empirical estimate
toward the supplier's contractual lead time using a James-Stein style
weight:

    w      = n / (n + PRIOR_STRENGTH)
    mu_hat = w * mu_emp + (1 - w) * mu_contract
    var_hat= w * var_emp + (1 - w) * var_prior

with var_prior derived from the contract value (CV of 0.35 is a reasonable
default for unmonitored suppliers, and is configurable per supplier tier).

SAMPLING
--------
Lead times are sampled from a discretised gamma fitted by moment matching,
which is the standard choice for a positive, right-skewed duration. Gamma
degenerates gracefully: if sigma -> 0 we return the mean deterministically.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

PRIOR_STRENGTH = 5.0        # observations needed before empirical dominates
DEFAULT_CONTRACT_CV = 0.35  # assumed CV when a supplier has no history
MIN_LEAD_DAYS = 0.0
MAX_LEAD_DAYS = 365.0
MAX_PLAUSIBLE_LEAD = 180.0  # receipts beyond this are treated as data errors


@dataclass(frozen=True)
class LeadTimeProfile:
    """Fitted lead-time distribution for one (supplier, sku, node) triple."""

    supplier_id: str
    sku_id: str
    node_id: str
    mean_days: float
    std_days: float
    p95_days: float
    n_observations: int
    source: str                # "empirical" | "shrunk" | "contract" | "default"
    contract_days: Optional[float] = None

    @property
    def cv(self) -> float:
        return self.std_days / self.mean_days if self.mean_days > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "supplier_id": self.supplier_id,
            "sku_id": self.sku_id,
            "node_id": self.node_id,
            "mean_days": round(self.mean_days, 3),
            "std_days": round(self.std_days, 3),
            "p95_days": round(self.p95_days, 3),
            "cv": round(self.cv, 3),
            "n_observations": self.n_observations,
            "source": self.source,
            "contract_days": self.contract_days,
        }


def _clean(observations: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(observations), dtype=np.float64)
    if arr.size == 0:
        return arr
    arr = arr[np.isfinite(arr)]
    # Negative lead time means the receipt predates the PO — a data-entry
    # error, not a fast supplier. Drop rather than clamp so it does not
    # silently bias the mean down.
    arr = arr[(arr >= MIN_LEAD_DAYS) & (arr <= MAX_PLAUSIBLE_LEAD)]
    return arr


def fit_profile(
    supplier_id: str,
    sku_id: str,
    node_id: str,
    observations: Sequence[float],
    contract_days: Optional[float] = None,
    contract_cv: float = DEFAULT_CONTRACT_CV,
) -> LeadTimeProfile:
    """Fit one lead-time profile with shrinkage toward the contract value."""
    obs = _clean(observations)
    n = int(obs.size)

    if contract_days is None or not math.isfinite(contract_days) or contract_days < 0:
        contract_days = float(obs.mean()) if n else 7.0
    contract_days = float(np.clip(contract_days, MIN_LEAD_DAYS, MAX_LEAD_DAYS))
    prior_std = max(0.5, contract_cv * contract_days)

    if n == 0:
        mean, std, source = contract_days, prior_std, "contract"
    else:
        emp_mean = float(obs.mean())
        emp_var = float(obs.var(ddof=1)) if n > 1 else prior_std ** 2
        w = n / (n + PRIOR_STRENGTH)
        mean = w * emp_mean + (1.0 - w) * contract_days
        var = w * emp_var + (1.0 - w) * (prior_std ** 2)
        std = math.sqrt(max(var, 0.0))
        source = "empirical" if w > 0.8 else "shrunk"

    mean = float(np.clip(mean, MIN_LEAD_DAYS, MAX_LEAD_DAYS))
    std = float(np.clip(std, 0.0, MAX_LEAD_DAYS))
    # p95 of a gamma with these moments; falls back to normal approx when
    # the shape parameter is huge (gamma -> normal anyway).
    p95 = _gamma_quantile(mean, std, 0.95)

    return LeadTimeProfile(
        supplier_id=supplier_id,
        sku_id=sku_id,
        node_id=node_id,
        mean_days=mean,
        std_days=std,
        p95_days=p95,
        n_observations=n,
        source=source,
        contract_days=contract_days,
    )


def _gamma_quantile(mean: float, std: float, q: float) -> float:
    """Approximate quantile of a moment-matched gamma. No scipy dependency.

    Uses the Wilson-Hilferty cube-root transform, which is accurate to well
    within a day for the shape range we care about (k >= 1).
    """
    if mean <= 0:
        return 0.0
    if std <= 1e-9:
        return mean
    k = (mean / std) ** 2                      # shape
    theta = (std ** 2) / mean                  # scale
    if k < 1e-6:
        return mean
    # z for q=0.95 hard-coded; extend via a table if other quantiles are needed.
    z = {0.90: 1.2815515655, 0.95: 1.6448536270, 0.99: 2.3263478740}.get(q, 1.6448536270)
    wh = k * (1.0 - 1.0 / (9.0 * k) + z * math.sqrt(1.0 / (9.0 * k))) ** 3
    return float(max(0.0, wh * theta))


class LeadTimeSampler:
    """Vectorised sampler over a whole (n_sku, n_node) grid.

    Built once per simulation run. `sample()` draws integer day counts for
    every cell at once — this is what lets the simulator step thousands of
    SKUs without a Python loop over SKUs.
    """

    def __init__(
        self,
        mean_days: np.ndarray,
        std_days: np.ndarray,
        rng: Optional[np.random.Generator] = None,
        max_lead: int = 60,
    ) -> None:
        if mean_days.shape != std_days.shape:
            raise ValueError("mean_days and std_days must share shape")
        self.mean = np.clip(np.nan_to_num(mean_days, nan=1.0), 0.0, float(max_lead))
        self.std = np.clip(np.nan_to_num(std_days, nan=0.0), 0.0, float(max_lead))
        self.rng = rng or np.random.default_rng()
        self.max_lead = int(max_lead)

        # Moment-matched gamma parameters, guarded against sigma == 0.
        with np.errstate(divide="ignore", invalid="ignore"):
            self.shape = np.where(self.std > 1e-9, (self.mean / np.maximum(self.std, 1e-9)) ** 2, 0.0)
            self.scale = np.where(self.std > 1e-9, (self.std ** 2) / np.maximum(self.mean, 1e-9), 0.0)
        self.deterministic = self.std <= 1e-9

    def sample(self, shape: Optional[tuple] = None) -> np.ndarray:
        """Draw integer lead times, clipped to [0, max_lead].

        `shape` may prepend leading axes (e.g. a population axis) to the
        stored (n_sku, n_node) grid; parameters broadcast across them so one
        call draws lead times for every candidate policy at once.
        """
        target = tuple(shape) if shape is not None else self.mean.shape
        try:
            mean = np.broadcast_to(self.mean, target)
            det = np.broadcast_to(self.deterministic, target)
            shp = np.broadcast_to(self.shape, target)
            scl = np.broadcast_to(self.scale, target)
        except ValueError as exc:
            raise ValueError(
                f"cannot broadcast lead-time grid {self.mean.shape} to {target}"
            ) from exc

        draws = np.empty(target, dtype=np.float64)
        stochastic = ~det
        if stochastic.any():
            draws[stochastic] = self.rng.gamma(
                shape=np.maximum(shp[stochastic], 1e-6),
                scale=np.maximum(scl[stochastic], 1e-9),
            )
        draws[det] = mean[det]
        out = np.rint(draws).astype(np.int64)
        return np.clip(out, 0, self.max_lead)

    def sigma_dl(self, d_hat: np.ndarray, sigma_d: np.ndarray) -> np.ndarray:
        """sigma of demand over lead time: sqrt(L*sigma_d^2 + d^2*sigma_L^2).

        This is the single most important formula in the upgrade. It is what
        makes the policy react to an unreliable supplier instead of only to
        volatile demand.
        """
        term_demand = self.mean * np.square(sigma_d)
        term_lead = np.square(d_hat) * np.square(self.std)
        return np.sqrt(np.maximum(term_demand + term_lead, 0.0))


def profiles_to_grid(
    profiles: Dict[str, LeadTimeProfile],
    sku_order: Sequence[str],
    default_mean: float = 7.0,
    default_std: float = 2.0,
) -> tuple:
    """Project a {sku_id: profile} dict onto arrays aligned with sku_order.

    Missing SKUs get the default profile and are logged once — a silent
    default here would mean an unmonitored supplier looks perfectly reliable,
    which is the exact failure mode this module exists to prevent.
    """
    n = len(sku_order)
    mean = np.full(n, default_mean, dtype=np.float64)
    std = np.full(n, default_std, dtype=np.float64)
    missing: List[str] = []
    for i, sid in enumerate(sku_order):
        p = profiles.get(sid)
        if p is None:
            missing.append(sid)
            continue
        mean[i] = p.mean_days
        std[i] = p.std_days
    if missing:
        logger.warning(
            "no lead-time profile for %d SKU(s); using default mean=%.1f std=%.1f (first: %s)",
            len(missing), default_mean, default_std, ", ".join(missing[:5]),
        )
    return mean, std
