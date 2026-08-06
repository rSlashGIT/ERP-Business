"""
CMA-ES (Covariance Matrix Adaptation Evolution Strategy) — pure NumPy.

Replaces the `cma` PyPI package. Written out because SmartStock ships into an
air-gapped ERP deployment where adding a transitive dependency to the
optimisation path is not acceptable, and because we need to control the
restart / budget semantics for a service that fits policies on a schedule.

Reference: Hansen (2016), "The CMA Evolution Strategy: A Tutorial", arXiv:1604.00772.
Implements: weighted intermediate recombination, rank-1 + rank-mu covariance
update, cumulative step-size adaptation (CSA), and eigen-decomposition lazily
refreshed every O(n/(10*lambda)) generations.

Complexity is O(n^2) per generation for sampling and O(n^3) per eigen refresh.
This is the reason SmartStock optimises SEGMENT-level parameters rather than
per-SKU parameters — see core/segmentation.py. With 12 segments x 10 params,
n = 120 and CMA-ES is comfortable. With per-SKU parameters and 10k SKUs,
n = 100_000 and CMA-ES is mathematically inapplicable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CMAESResult:
    x_best: np.ndarray
    f_best: float
    generations: int
    evaluations: int
    history: List[float] = field(default_factory=list)
    sigma_final: float = 0.0
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "x_best": self.x_best.tolist(),
            "f_best": float(self.f_best),
            "generations": int(self.generations),
            "evaluations": int(self.evaluations),
            "history": [float(v) for v in self.history],
            "sigma_final": float(self.sigma_final),
            "stop_reason": self.stop_reason,
        }


class CMAES:
    """Minimises f: R^n -> R.

    Parameters
    ----------
    x0 : initial mean.
    sigma0 : initial step size. Should be ~1/4 of the expected search range
        in each (normalised) coordinate. SmartStock normalises all policy
        params to roughly N(0,1) scale before optimising, so sigma0=0.5 is sane.
    popsize : lambda. Defaults to Hansen's 4 + floor(3*ln(n)).
    seed : RNG seed for reproducible fits. Production runs pin this so a
        replenishment run is auditable and re-derivable.
    bounds : optional (lo, hi) arrays applied by clipping at evaluation time
        only. The internal distribution stays unbounded, which preserves the
        CMA invariance properties; clipping is a projection at the objective
        boundary. For hard constraints prefer a penalty in the objective.
    """

    def __init__(
        self,
        x0: Sequence[float],
        sigma0: float = 0.5,
        popsize: Optional[int] = None,
        seed: Optional[int] = None,
        bounds: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    ) -> None:
        self.xmean = np.asarray(x0, dtype=np.float64).copy()
        self.n = int(self.xmean.size)
        if self.n == 0:
            raise ValueError("x0 must be non-empty")
        if not np.all(np.isfinite(self.xmean)):
            raise ValueError("x0 contains non-finite values")
        if sigma0 <= 0:
            raise ValueError("sigma0 must be > 0")

        self.sigma = float(sigma0)
        self.rng = np.random.default_rng(seed)
        self.bounds = bounds

        n = self.n
        self.lam = int(popsize) if popsize else 4 + int(math.floor(3 * math.log(n)))
        self.lam = max(self.lam, 4)
        self.mu = self.lam // 2

        # Recombination weights (log-decreasing), normalised to sum 1.
        w = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.weights = w / w.sum()
        self.mueff = float(1.0 / np.sum(self.weights ** 2))

        # Adaptation rates (Hansen 2016, eqs. 55-58).
        self.cc = (4.0 + self.mueff / n) / (n + 4.0 + 2.0 * self.mueff / n)
        self.cs = (self.mueff + 2.0) / (n + self.mueff + 5.0)
        self.c1 = 2.0 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(
            1.0 - self.c1,
            2.0 * (self.mueff - 2.0 + 1.0 / self.mueff) / ((n + 2.0) ** 2 + self.mueff),
        )
        self.damps = (
            1.0 + 2.0 * max(0.0, math.sqrt((self.mueff - 1.0) / (n + 1.0)) - 1.0) + self.cs
        )

        # Dynamic state.
        self.pc = np.zeros(n)
        self.ps = np.zeros(n)
        self.B = np.eye(n)
        self.D = np.ones(n)
        self.C = np.eye(n)
        self.invsqrtC = np.eye(n)
        self.eigeneval = 0
        self.counteval = 0
        self.generation = 0
        self.chiN = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))

        self.x_best: np.ndarray = self.xmean.copy()
        self.f_best: float = math.inf
        self._last_pop: Optional[np.ndarray] = None

    # ---------------- sampling ----------------

    def ask(self) -> np.ndarray:
        """Return (lam, n) candidate solutions."""
        z = self.rng.standard_normal((self.lam, self.n))
        y = z @ (self.B * self.D).T          # y ~ N(0, C)
        pop = self.xmean[None, :] + self.sigma * y
        if not np.all(np.isfinite(pop)):
            # Numerical blow-up: reset covariance and resample once.
            logger.warning("CMA-ES produced non-finite samples; resetting covariance")
            self._reset_covariance()
            z = self.rng.standard_normal((self.lam, self.n))
            y = z @ (self.B * self.D).T
            pop = self.xmean[None, :] + self.sigma * y
        self._last_pop = pop
        return self._clip(pop)

    def _clip(self, pop: np.ndarray) -> np.ndarray:
        if self.bounds is None:
            return pop
        lo, hi = self.bounds
        return np.clip(pop, lo, hi)

    def _reset_covariance(self) -> None:
        self.C = np.eye(self.n)
        self.B = np.eye(self.n)
        self.D = np.ones(self.n)
        self.invsqrtC = np.eye(self.n)
        self.pc[:] = 0.0
        self.ps[:] = 0.0
        self.sigma = max(self.sigma, 1e-3)

    # ---------------- update ----------------

    def tell(self, pop: np.ndarray, fitness: np.ndarray) -> None:
        """Update the distribution from evaluated candidates (lower is better)."""
        pop = np.asarray(pop, dtype=np.float64)
        fitness = np.asarray(fitness, dtype=np.float64)
        if pop.shape[0] != fitness.shape[0]:
            raise ValueError("pop and fitness length mismatch")

        # Non-finite fitness is treated as maximally bad rather than crashing the
        # run: a single pathological policy draw must not kill a nightly fit.
        bad = ~np.isfinite(fitness)
        if bad.any():
            finite = fitness[~bad]
            fill = (finite.max() if finite.size else 0.0) + 1e12
            fitness = np.where(bad, fill, fitness)

        self.counteval += pop.shape[0]
        self.generation += 1

        order = np.argsort(fitness)
        pop_sorted = pop[order]
        if fitness[order[0]] < self.f_best:
            self.f_best = float(fitness[order[0]])
            self.x_best = pop_sorted[0].copy()

        xold = self.xmean.copy()
        self.xmean = self.weights @ pop_sorted[: self.mu]

        # Step-size evolution path.
        y_w = (self.xmean - xold) / self.sigma
        self.ps = (1.0 - self.cs) * self.ps + math.sqrt(
            self.cs * (2.0 - self.cs) * self.mueff
        ) * (self.invsqrtC @ y_w)

        ps_norm = float(np.linalg.norm(self.ps))
        hsig = ps_norm / math.sqrt(
            max(1e-12, 1.0 - (1.0 - self.cs) ** (2.0 * self.generation))
        ) / self.chiN < (1.4 + 2.0 / (self.n + 1.0))

        # Rank-1 evolution path.
        self.pc = (1.0 - self.cc) * self.pc + (
            hsig * math.sqrt(self.cc * (2.0 - self.cc) * self.mueff)
        ) * y_w

        # Covariance: rank-1 + rank-mu.
        artmp = (pop_sorted[: self.mu] - xold[None, :]) / self.sigma
        delta_hsig = (1.0 - hsig) * self.cc * (2.0 - self.cc)
        self.C = (
            (1.0 - self.c1 - self.cmu) * self.C
            + self.c1 * (np.outer(self.pc, self.pc) + delta_hsig * self.C)
            + self.cmu * (artmp.T * self.weights) @ artmp
        )

        # CSA step-size control.
        self.sigma *= math.exp(min(1.0, (self.cs / self.damps) * (ps_norm / self.chiN - 1.0)))
        self.sigma = float(np.clip(self.sigma, 1e-12, 1e6))

        self._maybe_eigen()

    def _maybe_eigen(self) -> None:
        interval = self.lam / ((self.c1 + self.cmu) * self.n * 10.0)
        if self.counteval - self.eigeneval <= interval:
            return
        self.eigeneval = self.counteval
        self.C = np.triu(self.C) + np.triu(self.C, 1).T   # enforce symmetry
        try:
            D2, B = np.linalg.eigh(self.C)
        except np.linalg.LinAlgError:
            logger.warning("eigh failed; resetting covariance")
            self._reset_covariance()
            return
        # Guard against numerical negatives / degenerate axes.
        D2 = np.maximum(D2, 1e-20)
        cond = D2.max() / D2.min()
        if cond > 1e14:
            D2 = np.maximum(D2, D2.max() / 1e14)
        self.D = np.sqrt(D2)
        self.B = B
        self.invsqrtC = B @ np.diag(1.0 / self.D) @ B.T

    # ---------------- stopping ----------------

    def should_stop(self, tol_fun: float, history: List[float]) -> Optional[str]:
        if self.sigma * float(self.D.max()) < 1e-11:
            return "tolx: search distribution collapsed"
        if self.D.max() / max(self.D.min(), 1e-20) > 1e14:
            return "conditioncov: covariance ill-conditioned"
        if len(history) >= 20:
            window = history[-20:]
            if (max(window) - min(window)) < tol_fun * (abs(min(window)) + 1e-9):
                return "tolfun: no improvement over 20 generations"
        return None


def minimize(
    objective: Callable[[np.ndarray], np.ndarray],
    x0: Sequence[float],
    sigma0: float = 0.5,
    max_generations: int = 200,
    popsize: Optional[int] = None,
    seed: Optional[int] = None,
    bounds: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    tol_fun: float = 1e-9,
    callback: Optional[Callable[[int, float, np.ndarray], None]] = None,
) -> CMAESResult:
    """Run CMA-ES to a generation budget.

    `objective` receives the full (lam, n) population and MUST return a
    (lam,) array. Batch evaluation is required, not optional: the SmartStock
    simulator evaluates an entire population against all SKUs in one
    vectorised pass, which is where the throughput comes from. Calling it
    once per candidate would be ~lam times slower.
    """
    es = CMAES(x0, sigma0=sigma0, popsize=popsize, seed=seed, bounds=bounds)
    history: List[float] = []
    stop_reason = "max_generations reached"

    for gen in range(1, max_generations + 1):
        pop = es.ask()
        fitness = np.asarray(objective(pop), dtype=np.float64)
        if fitness.shape != (pop.shape[0],):
            raise ValueError(
                f"objective must return shape ({pop.shape[0]},), got {fitness.shape}"
            )
        es.tell(pop, fitness)
        history.append(es.f_best)
        if callback is not None:
            callback(gen, es.f_best, es.x_best)
        reason = es.should_stop(tol_fun, history)
        if reason:
            stop_reason = reason
            break

    return CMAESResult(
        x_best=es.x_best,
        f_best=es.f_best,
        generations=es.generation,
        evaluations=es.counteval,
        history=history,
        sigma_final=es.sigma,
        stop_reason=stop_reason,
    )
