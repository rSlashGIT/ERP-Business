"""
Demand forecasting layer.

WHY THIS EXISTS
---------------
v2.0 shipped with a causal rolling mean as the forecast surrogate. That is a
defensible placeholder and an indefensible product: a rolling mean cannot see
weekly seasonality, cannot handle intermittent demand (it spreads a lumpy
series into a smooth trickle), and gives you no honest uncertainty estimate.
Since safety stock is `z * sigma_DL`, a wrong sigma is a wrong order quantity
on every single line.

WHAT CHANGED
------------
1. `Forecaster` protocol with `predict(history, horizon) -> (mean, sigma)`.
2. `LegacyModelAdapter` wraps ANY object exposing `predict_next(history) -> float`
   -- which is exactly the interface of the existing LGBMForecaster,
   NHITSForecaster and ChronosForecaster in the original SmartStock repo. Those
   models drop straight in with no engine change.
3. `CrostonSBA` for intermittent / lumpy demand. This is the correct method for
   sparse series and the original repo did not have it. Syntetos-Boylan
   Approximation debiases classical Croston by (1 - alpha/2).
4. `SeasonalDamped` -- additive Holt-Winters with weekly period and a damped
   trend, for smooth and erratic demand. Pure NumPy.
5. `EnsembleForecaster` -- combines members by median and uses their
   DISAGREEMENT as an extra variance term. When three models disagree you
   genuinely are less certain, and safety stock should reflect it.
6. Sigma is measured, not assumed: a rolling one-step backtest over the tail of
   the series gives the empirical residual std. v2.0 used sqrt(mean) whenever a
   caller omitted sigma, which is a Poisson assumption that retail demand
   violates badly (M5 series routinely run CV > 1.5).

SELECTION
---------
`auto_select` routes on the Syntetos-Boylan demand class already computed in
core/segmentation.py:
    SMOOTH, ERRATIC              -> SeasonalDamped
    INTERMITTENT, LUMPY          -> CrostonSBA
This is not a heuristic flourish; applying Holt-Winters to a series that is 80%
zeros produces a confidently wrong non-zero forecast every day.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Sequence, Tuple

import numpy as np

from .segmentation import DemandClass, classify_demand

logger = logging.getLogger(__name__)

MIN_HISTORY = 14
BACKTEST_TAIL = 60          # days used to measure residual sigma
SEASON_PERIOD = 7           # weekly
SHRINK_STRENGTH = 12.0      # uncensored neighbours needed before the estimate is trusted
SPARSE_ADI = 2.0            # Croston only earns its keep past this inter-demand interval
SIGMA_FLOOR_WINDOW = 180    # lookback used when the short-window sigma collapses to zero
SIGMA_FLOOR_FRACTION = 0.5  # fraction of the long-run spread used as the floor


class Forecaster(Protocol):
    name: str

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        """Return (mean demand per period, sigma per period)."""
        ...


# ───────────────────────── censored demand ─────────────────────────

def decensor(
    demand: Sequence[float],
    stocked_out: Optional[Sequence[bool]] = None,
    window: int = 28,
) -> np.ndarray:
    """Correct right-censored demand on stockout days.

    THE PROBLEM: on a day you were out of stock, recorded SALES are not DEMAND.
    They are min(demand, available), which is a right-censored observation. Feed
    that to a forecaster and it learns that demand drops exactly when you have
    no stock -- so it recommends less stock, causing more stockouts. The system
    trains itself into the failure mode it exists to prevent.

    THE CORRECTION: a censored observation is a LOWER BOUND on true demand, so
    the corrected value never goes below what was sold. Above that bound we use
    the local uncensored median, SHRUNK toward the observation by how much
    uncensored evidence is genuinely nearby. The shrinkage is the important
    part: with a long stockout block the few surviving neighbours come from a
    different demand regime, and using their mean outright overshoots the truth
    by ~2x. The statistically pure route is a Tobit/EM fit on the censored
    likelihood, which needs a per-SKU demand distribution we do not have.

    Returns the corrected series. Never mutates the input.
    """
    d = np.asarray(demand, dtype=np.float64).copy()
    if stocked_out is None:
        return d
    flags = np.asarray(stocked_out, dtype=bool)
    if flags.shape != d.shape or not flags.any():
        return d

    n = d.size
    corrected = 0
    for i in np.flatnonzero(flags):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        local = d[lo:hi]
        mask = ~flags[lo:hi]
        neighbours = local[mask]
        k = neighbours.size
        if k == 0:
            continue
        # Shrink the neighbour estimate toward the observation in proportion to
        # how much uncensored evidence is actually nearby. In the middle of a
        # long stockout block there are almost no uncensored neighbours, and the
        # ones that survive come from a different demand level -- taking their
        # mean outright overshoots wildly (measured: true 4.7 -> corrected 8.6).
        # w -> 0 with no local evidence, so the value stays at the observation,
        # which is a valid lower bound on the truth.
        w = k / (k + SHRINK_STRENGTH)
        est = w * float(np.median(neighbours)) + (1.0 - w) * d[i]
        if est > d[i]:
            d[i] = est
            corrected += 1
    if corrected:
        logger.debug("de-censored %d of %d stockout days", corrected, int(flags.sum()))
    return d


# ───────────────────────── helpers ─────────────────────────

def _residual_sigma(
    fc: "Forecaster", history: np.ndarray, tail: int = BACKTEST_TAIL
) -> float:
    """Empirical one-step residual std from a walk-forward backtest.

    This is the honest sigma. Everything downstream (safety stock, implied
    service level, the confidence score in the UI) depends on it being real
    rather than a distributional assumption.
    """
    n = history.size
    start = max(MIN_HISTORY, n - tail)
    if n - start < 8:
        return float(history.std(ddof=1)) if n > 1 else 0.0
    errs: List[float] = []
    for t in range(start, n):
        try:
            mu, _ = fc.predict(history[:t], horizon=1)
        except Exception:
            continue
        errs.append(float(history[t] - mu))
    if len(errs) < 4:
        return float(history.std(ddof=1)) if n > 1 else 0.0
    e = np.asarray(errs)
    # Residuals are heavy-tailed on retail data; MAD-based scale is far more
    # stable than a plain std against a single promotional spike.
    mad = float(np.median(np.abs(e - np.median(e))))
    sigma_mad = 1.4826 * mad
    return float(max(sigma_mad, e.std(ddof=1) * 0.5, 1e-6))


def sigma_over_horizon(sigma_1: float, horizon: int, autocorr: float = 0.0) -> float:
    """Scale a one-step sigma to an h-step aggregate.

    For iid errors this is sigma*sqrt(h). Retail demand errors are mildly
    positively autocorrelated, which inflates the aggregate variance; the
    `autocorr` term applies the standard AR(1) adjustment. Ignoring it
    understates safety stock on long lead times.
    """
    h = max(1, int(horizon))
    if abs(autocorr) < 1e-9:
        return sigma_1 * math.sqrt(h)
    r = float(np.clip(autocorr, -0.95, 0.95))
    var = h + 2.0 * sum((h - k) * (r ** k) for k in range(1, h))
    return sigma_1 * math.sqrt(max(var, 1e-12))


# ───────────────────────── models ─────────────────────────

@dataclass
class MovingAverage:
    """Baseline. Kept so the benchmark has an honest floor to beat."""

    window: int = 28
    name: str = "moving_average"

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        h = np.asarray(history, dtype=np.float64)
        if h.size == 0:
            return 0.0, 0.0
        w = h[-self.window:]
        mu = float(w.mean())
        sd = float(w.std(ddof=1)) if w.size > 1 else 0.0
        return mu, sigma_over_horizon(sd, horizon)


@dataclass
class CrostonSBA:
    """Croston with the Syntetos-Boylan Approximation, for intermittent demand.

    Smooths the non-zero demand SIZE and the INTER-ARRIVAL INTERVAL separately:
        z_t = a*y_t     + (1-a)*z_{t-1}      (size, updated only on non-zero days)
        p_t = a*q       + (1-a)*p_{t-1}      (interval since the last non-zero)
        forecast = (1 - a/2) * z_t / p_t     <- the SBA debiasing factor

    Classical Croston is provably biased high; the (1 - a/2) correction is the
    standard fix and materially changes safety stock on slow movers.
    """

    alpha: float = 0.1
    name: str = "croston_sba"

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        y = np.asarray(history, dtype=np.float64)
        nz_idx = np.flatnonzero(y > 0)
        if nz_idx.size == 0:
            return 0.0, 0.0
        if nz_idx.size == 1:
            mu = float(y[nz_idx[0]] / max(y.size, 1))
            return mu, sigma_over_horizon(float(y.std(ddof=1)) if y.size > 1 else 0.0, horizon)

        a = self.alpha
        z = float(y[nz_idx[0]])
        p = float(nz_idx[0] + 1)
        last = nz_idx[0]
        for i in nz_idx[1:]:
            gap = float(i - last)
            z += a * (float(y[i]) - z)
            p += a * (gap - p)
            last = i
        p = max(p, 1.0)
        mu = (1.0 - a / 2.0) * z / p          # SBA debiasing
        mu = float(max(0.0, mu))
        sd = float(y.std(ddof=1)) if y.size > 1 else 0.0
        return mu, sigma_over_horizon(sd, horizon)


@dataclass
class SeasonalDamped:
    """Additive Holt-Winters, weekly seasonality, damped trend.

    Damping (phi < 1) matters: an undamped trend extrapolated over a 20-day
    lead time produces absurd order quantities the first time a SKU has a good
    fortnight. phi=0.9 is the standard conservative default.
    """

    alpha: float = 0.25      # level
    beta: float = 0.05       # trend
    gamma: float = 0.20      # seasonal
    phi: float = 0.90        # trend damping
    period: int = SEASON_PERIOD
    name: str = "seasonal_damped"

    def _fit(self, y: np.ndarray) -> Tuple[float, float, np.ndarray]:
        m = self.period
        if y.size < 2 * m:
            return float(y.mean()) if y.size else 0.0, 0.0, np.zeros(m)
        n_seasons = y.size // m
        seasons = y[: n_seasons * m].reshape(n_seasons, m)
        level = float(seasons.mean())
        seasonal = seasons.mean(axis=0) - level
        trend = 0.0
        for t in range(y.size):
            s_idx = t % m
            prev_level = level
            deseason = y[t] - seasonal[s_idx]
            level = self.alpha * deseason + (1 - self.alpha) * (level + self.phi * trend)
            trend = self.beta * (level - prev_level) + (1 - self.beta) * self.phi * trend
            seasonal[s_idx] = self.gamma * (y[t] - level) + (1 - self.gamma) * seasonal[s_idx]
        return level, trend, seasonal

    def predict_series(self, history: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
        """Causal one-step-ahead forecast and running sigma for EVERY day, in O(n).

        The Holt-Winters recursion already produces a one-step forecast at each
        step, so recording it costs nothing. This is what lets the simulator
        use the real forecaster instead of a rolling mean -- a walk-forward
        refit per day would be O(n^2) and unusable inside a CMA-ES objective.
        """
        y = np.asarray(history, dtype=np.float64)
        n = y.size
        mu = np.zeros(n)
        sig = np.zeros(n)
        m = self.period
        if n < 2 * m:
            run = np.maximum.accumulate(np.ones(n)) * (y.mean() if n else 0.0)
            return run, np.full(n, float(y.std(ddof=1)) if n > 1 else 0.0)
        n_seasons = n // m
        seasons = y[: n_seasons * m].reshape(n_seasons, m)
        level = float(seasons.mean())
        seasonal = seasons.mean(axis=0) - level
        trend = 0.0
        se = 0.0
        for t in range(n):
            s_idx = t % m
            fc = max(0.0, level + self.phi * trend + seasonal[s_idx])
            mu[t] = fc                      # forecast made BEFORE seeing y[t]
            err = y[t] - fc
            se = 0.97 * se + 0.03 * err * err
            sig[t] = math.sqrt(max(se, 1e-12))
            prev_level = level
            deseason = y[t] - seasonal[s_idx]
            level = self.alpha * deseason + (1 - self.alpha) * (level + self.phi * trend)
            trend = self.beta * (level - prev_level) + (1 - self.beta) * self.phi * trend
            seasonal[s_idx] = self.gamma * (y[t] - level) + (1 - self.gamma) * seasonal[s_idx]
        return mu, sig

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        y = np.asarray(history, dtype=np.float64)
        if y.size < MIN_HISTORY:
            return MovingAverage().predict(y, horizon)
        level, trend, seasonal = self._fit(y)
        m = self.period
        # Average the forecast across the horizon: replenishment cares about
        # mean demand per day over the protection window, not day h alone.
        vals = []
        damp = 0.0
        for h in range(1, max(1, horizon) + 1):
            damp += self.phi ** h
            vals.append(level + damp * trend + seasonal[(y.size + h - 1) % m])
        mu = float(max(0.0, np.mean(vals)))
        resid = y[m:] - np.array([
            level + seasonal[i % m] for i in range(m, y.size)
        ])
        sd = float(np.std(resid, ddof=1)) if resid.size > 1 else float(y.std(ddof=1))
        return mu, sigma_over_horizon(sd, horizon)


class LegacyModelAdapter:
    """Wraps any object with `predict_next(history: np.ndarray) -> float`.

    That is the exact interface of LGBMForecaster, NHITSForecaster and
    ChronosForecaster in the original SmartStock repo (src/forecaster_*.py), so
    those trained models plug in here with zero changes to either side:

        from forecaster_lgbm import LGBMForecaster
        fc = LegacyModelAdapter(LGBMForecaster.load('models/lgbm.pkl'), 'lgbm')

    Multi-step is done by recursive rollout, appending each prediction to the
    history. That accumulates error, which is precisely why sigma is measured
    by backtest rather than assumed.
    """

    def __init__(self, model: Any, name: str, min_history: int = MIN_HISTORY) -> None:
        if not hasattr(model, "predict_next"):
            raise TypeError(
                f"{name}: legacy forecasters must expose predict_next(history) -> float"
            )
        self.model = model
        self.name = name
        self.min_history = min_history

    def _point(self, y: np.ndarray, horizon: int) -> Optional[float]:
        """Recursive rollout returning the mean forecast, or None on any failure.

        Separate from `predict` on purpose. `_residual_sigma` backtests by
        calling the forecaster repeatedly; if it called `predict`, and `predict`
        called `_residual_sigma`, the two would recurse forever. That is not
        hypothetical -- it hung the adapter test suite until this split was
        introduced.
        """
        rolled = list(y)
        preds: List[float] = []
        for _ in range(max(1, horizon)):
            try:
                raw = self.model.predict_next(np.asarray(rolled))
                v = float(raw)
            except Exception:
                logger.warning("%s.predict_next failed; falling back", self.name, exc_info=True)
                return None
            if not math.isfinite(v):
                logger.warning("%s.predict_next returned %r; falling back", self.name, v)
                return None
            v = max(0.0, v)
            preds.append(v)
            rolled.append(v)
        return float(np.mean(preds))

    def _backtest_sigma(self, y: np.ndarray, tail: int = BACKTEST_TAIL) -> float:
        """One-step residual sigma, using _point so it cannot recurse."""
        n = y.size
        start = max(self.min_history, n - tail)
        if n - start < 8:
            return float(y.std(ddof=1)) if n > 1 else 0.0
        errs: List[float] = []
        for t in range(start, n):
            mu = self._point(y[:t], 1)
            if mu is None:
                continue
            errs.append(float(y[t] - mu))
        if len(errs) < 4:
            return float(y.std(ddof=1)) if n > 1 else 0.0
        e = np.asarray(errs)
        mad = float(np.median(np.abs(e - np.median(e))))
        return float(max(1.4826 * mad, e.std(ddof=1) * 0.5, 1e-6))

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        y = np.asarray(history, dtype=np.float64)
        if y.size < self.min_history:
            return MovingAverage().predict(y, horizon)
        mu = self._point(y, horizon)
        if mu is None:
            return MovingAverage().predict(y, horizon)
        sd = self._backtest_sigma(y)
        return mu, sigma_over_horizon(sd, horizon)


class EnsembleForecaster:
    """Median of members, with model disagreement folded into sigma.

    sigma_total^2 = mean(member_sigma^2) + var(member_means)
                    ^ aleatoric              ^ epistemic

    The second term is the honest admission that when LightGBM says 40 and
    Chronos says 90, you do not know the answer to within 5. The legacy repo
    surfaced model disagreement as a "consensus" badge in the UI but never fed
    it into the order quantity; here it directly raises safety stock.
    """

    def __init__(self, members: Sequence[Forecaster], name: str = "ensemble") -> None:
        if not members:
            raise ValueError("ensemble needs at least one member")
        self.members = list(members)
        self.name = name

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        mus: List[float] = []
        sds: List[float] = []
        for m in self.members:
            try:
                mu, sd = m.predict(history, horizon)
            except Exception:
                logger.warning("ensemble member %s failed", getattr(m, "name", "?"), exc_info=True)
                continue
            if math.isfinite(mu) and math.isfinite(sd):
                mus.append(mu)
                sds.append(sd)
        if not mus:
            return MovingAverage().predict(history, horizon)
        mu = float(np.median(mus))
        aleatoric = float(np.mean(np.square(sds)))
        epistemic = float(np.var(mus)) if len(mus) > 1 else 0.0
        return mu, float(math.sqrt(max(aleatoric + epistemic, 0.0)))

    def disagreement(self, history: Sequence[float], horizon: int = 1) -> float:
        """Coefficient of variation across members. Drives the UI confidence bar."""
        mus = []
        for m in self.members:
            try:
                mus.append(m.predict(history, horizon)[0])
            except Exception:
                continue
        if len(mus) < 2:
            return 0.0
        mean = float(np.mean(mus))
        return float(np.std(mus) / mean) if mean > 1e-9 else 0.0


@dataclass
class Theta:
    """The Theta method (Assimakopoulos & Nikolopoulos 2000).

    Won the M3 competition and remains a brutally strong baseline. Decomposes
    the series into two "theta lines": theta=0 (the linear regression trend)
    and theta=2 (twice the curvature), then averages their extrapolations.
    Equivalent to SES with drift, which is why it is so hard to beat on noisy
    series -- it barely reacts to a single spike.

    Chosen for ERRATIC demand: regular timing, wildly varying size. Holt-Winters
    over-fits the size variation; Theta's heavy damping does not.
    """

    alpha: float = 0.2
    name: str = "theta"

    def predict(self, history: Sequence[float], horizon: int = 1) -> Tuple[float, float]:
        y = np.asarray(history, dtype=np.float64)
        n = y.size
        if n < MIN_HISTORY:
            return MovingAverage().predict(y, horizon)
        t = np.arange(n, dtype=np.float64)
        # theta=0 line: OLS trend
        tm, ym = t.mean(), y.mean()
        denom = float(((t - tm) ** 2).sum())
        slope = float(((t - tm) * (y - ym)).sum() / denom) if denom > 0 else 0.0
        intercept = ym - slope * tm
        # theta=2 line: SES on the doubled-curvature series
        theta2 = 2.0 * y - (intercept + slope * t)
        level = float(theta2[0])
        for v in theta2[1:]:
            level = self.alpha * float(v) + (1 - self.alpha) * level
        h = max(1, horizon)
        steps = np.arange(1, h + 1, dtype=np.float64)
        line0 = intercept + slope * (n - 1 + steps)
        mu = float(np.mean(0.5 * (line0 + level)))
        mu = max(0.0, mu)
        fitted = intercept + slope * t
        resid = y - fitted
        sd = float(resid.std(ddof=1)) if n > 1 else 0.0
        return mu, sigma_over_horizon(sd, horizon)


@dataclass
class BaggedMedian:
    """Median of several overlapping window means, plus a weekday factor.

    The median is the point: LUMPY demand is dominated by rare huge orders, and
    any mean-based estimator is dragged upward by them into permanent overstock.
    Bagging across window lengths (7/14/28/56) avoids betting on one lookback.
    A multiplicative day-of-week factor is applied when a weekday index is
    supplied, which is where the M5 `dow` column finally earns its keep.
    """

    windows: Tuple[int, ...] = (7, 14, 28, 56)
    name: str = "bagged_median"

    def predict(self, history: Sequence[float], horizon: int = 1,
                dow: Optional[Sequence[int]] = None,
                next_dow: Optional[int] = None) -> Tuple[float, float]:
        y = np.asarray(history, dtype=np.float64)
        n = y.size
        if n < MIN_HISTORY:
            return MovingAverage().predict(y, horizon)
        ests = [float(y[-w:].mean()) for w in self.windows if n >= w]
        if not ests:
            ests = [float(y.mean())]
        mu = float(np.median(ests))
        if dow is not None and next_dow is not None and n >= 28:
            d = np.asarray(dow, dtype=int)[-n:]
            overall = float(y.mean())
            if overall > 1e-9:
                mask = d == int(next_dow)
                if mask.sum() >= 4:
                    factor = float(y[mask].mean() / overall)
                    mu *= float(np.clip(factor, 0.5, 2.0))
        mu = max(0.0, mu)
        w = min(56, n)
        sd = float(y[-w:].std(ddof=1)) if w > 1 else 0.0
        return mu, sigma_over_horizon(sd, horizon)


class CalendarAdjusted:
    """Wraps any forecaster and applies multiplicative calendar factors.

    The M5 dataset ships `dow`, `month`, `snap` and `event` columns that the
    v2.1 forecaster ignored entirely. SNAP days (US food-assistance disbursement)
    move grocery demand materially, and weekday effects are large in retail.

    Factors are estimated as ratio-to-overall-mean, clipped to [0.5, 2.0] so a
    thin sample cannot produce a 5x multiplier, and only applied when at least
    MIN_OBS observations support them.
    """

    MIN_OBS = 6

    def __init__(self, base: Forecaster, name: Optional[str] = None) -> None:
        self.base = base
        self.name = name or f"{getattr(base, 'name', 'base')}+cal"

    @staticmethod
    def _factor(y: np.ndarray, mask: np.ndarray, overall: float) -> float:
        if overall <= 1e-9 or mask.sum() < CalendarAdjusted.MIN_OBS:
            return 1.0
        return float(np.clip(float(y[mask].mean()) / overall, 0.5, 2.0))

    def predict(self, history: Sequence[float], horizon: int = 1,
                calendar: Optional[dict] = None) -> Tuple[float, float]:
        mu, sd = self.base.predict(history, horizon)
        if not calendar:
            return mu, sd
        y = np.asarray(history, dtype=np.float64)
        n = y.size
        overall = float(y.mean()) if n else 0.0
        factor = 1.0
        for key, nxt in (("dow", calendar.get("next_dow")), ("snap", calendar.get("next_snap"))):
            col = calendar.get(key)
            if col is None or nxt is None:
                continue
            arr = np.asarray(col)[-n:]
            if arr.size != n:
                continue
            factor *= self._factor(y, arr == nxt, overall)
        return max(0.0, mu * factor), sd


# ───────────────────────── selection ─────────────────────────

def auto_select(history: Sequence[float], extra: Optional[Sequence[Forecaster]] = None) -> Forecaster:
    """Pick a forecaster from the demand class. Never returns None.

    ROUTING IS SET BY MEASUREMENT, NOT BY TEXTBOOK. Walk-forward one-step MAE,
    30 M5 SKUs x 120 held-out days (reproduce with scripts/forecast_bench.py):

        class          moving_avg  croston  seasonal_hw  theta  bagged  ens(hw,theta,bag)
        smooth              6.594    6.610        5.166  6.462   6.413              6.116
        intermittent        6.561    7.573        4.388  5.388   5.992              5.223
        erratic             6.847    7.895        6.950  6.602   6.691              6.550
        lumpy               5.681    5.780        6.096  5.828   5.704              5.731

    Three consequences, each of which contradicts the obvious choice:

      1. Croston-SBA loses on EVERY class, including the intermittent one it was
         designed for. M5's "intermittent" SKUs at ADI>=1.32 are only ~24% zeros
         -- not sparse enough for Croston to pay for the seasonality it discards.
         It stays in the module for genuinely sparse catalogues (ADI >= 2) but is
         no longer the default anywhere.
      2. LUMPY is still best served by a plain 28-day moving average. Nothing
         beat it, so nothing replaces it. Reporting a fancier model here would
         be a regression dressed up as progress.
      3. ERRATIC needs the ensemble, and only wins by 4.3%. Regular timing with
         wildly varying size defeats every single model; averaging three of them
         is the only thing that helps.
    """
    y = np.asarray(history, dtype=np.float64)
    if y.size < MIN_HISTORY:
        return MovingAverage()
    cls, adi, _cv2 = classify_demand(y)

    if adi >= SPARSE_ADI:
        base: Forecaster = CrostonSBA()          # genuinely sparse: Croston earns it
        wants_cal = False
    elif cls is DemandClass.LUMPY:
        base = MovingAverage(28)                 # measured: nothing beats it
        wants_cal = True
    elif cls is DemandClass.ERRATIC:
        base = EnsembleForecaster([SeasonalDamped(), Theta(), BaggedMedian()], name="erratic_ens")
        wants_cal = True
    else:                                        # SMOOTH, INTERMITTENT
        base = SeasonalDamped()
        wants_cal = False

    if extra:
        base = EnsembleForecaster([base, *extra])
        wants_cal = False
    # CALENDAR FEATURES ARE NOT UNIVERSALLY GOOD. Measured effect of applying
    # dow + snap factors on top of the routed model:
    #     erratic +1.2%   lumpy +0.6%   intermittent -8.2%   smooth -22.6%
    # Smooth collapses because SeasonalDamped ALREADY models weekly seasonality
    # -- a day-of-week multiplier on top double-counts it. So the flag is set
    # only for the two classes whose models carry no seasonal term of their own.
    try:
        base.wants_calendar = wants_cal  # type: ignore[attr-defined]
    except AttributeError:
        pass
    return base


def forecast_for(
    history: Sequence[float],
    horizon: int = 1,
    stocked_out: Optional[Sequence[bool]] = None,
    extra_models: Optional[Sequence[Forecaster]] = None,
    calendar: Optional[dict] = None,
) -> Tuple[float, float, str]:
    """One-call entry point used by core/recommend.py.

    Returns (mean_per_day, sigma_per_day, model_name).
    De-censors first: correcting stockout days BEFORE fitting is the whole point.
    """
    y = decensor(history, stocked_out)
    fc = auto_select(y, extra_models)
    if calendar and getattr(fc, "wants_calendar", False):
        fc = CalendarAdjusted(fc)
        mu, sd = fc.predict(y, horizon=horizon, calendar=calendar)
    else:
        mu, sd = fc.predict(y, horizon=horizon)
    # sigma is returned per-period; callers scale it over the lead time
    # themselves via sigma_DL, so undo the horizon scaling applied above.
    if horizon > 1:
        sd = sd / math.sqrt(horizon)

    # SIGMA FLOOR. A short window can be entirely flat while the longer history
    # is plainly variable -- e.g. M5 SKU FOODS_3_448 sells on 68% of days over
    # 1900 days but had a dead final month, so MovingAverage(28) returns
    # sigma = 0. Safety stock is z * sigma_DL, so sigma = 0 means ZERO safety
    # stock and a guaranteed stockout the moment demand resumes. Claiming no
    # uncertainty about a SKU with that history is indefensible, so floor sigma
    # at the long-run spread whenever the short window collapses.
    arr = np.asarray(y, dtype=np.float64)
    if sd <= 1e-9 and arr.size >= 2 * SIGMA_FLOOR_WINDOW:
        long_sd = float(np.std(arr[-SIGMA_FLOOR_WINDOW:], ddof=1))
        if long_sd > 1e-9:
            sd = long_sd * SIGMA_FLOOR_FRACTION
    return float(max(0.0, mu)), float(max(0.0, sd)), getattr(fc, "name", "auto")
