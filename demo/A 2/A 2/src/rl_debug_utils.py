"""
Numerically-stable utilities for RL training.
- WelfordNormalizer     : O(1), numerically stable online mean/std
- LagrangianDualAscent  : projected dual gradient ascent (no EMA smoothing)
- compute_epsilon_decay : closed-form epsilon schedule
- q_entropy_regularizer : bounded softmax-entropy diversity term
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F


# ────────────────────────────────────────────────────────────────────────────
# #13  Welford online normalizer — numerically stable
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class WelfordNormalizer:
    """Online mean/variance via Welford's algorithm.

    Works in float64 internally to avoid catastrophic cancellation.
    Batched normalization uses *current* statistics (not stale ones) —
    callers MUST normalize at sample time, not at storage time.
    """
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    eps: float = 1e-6
    clip: float = 10.0  # clip normalized output to [-clip, +clip]

    def update(self, x: float) -> None:
        self.count += 1
        delta = float(x) - self.mean
        self.mean += delta / self.count
        delta2 = float(x) - self.mean
        self.m2 += delta * delta2

    def update_batch(self, xs: np.ndarray) -> None:
        for x in np.asarray(xs, dtype=np.float64).ravel():
            self.update(float(x))

    @property
    def variance(self) -> float:
        if self.count < 2:
            return 1.0
        return self.m2 / (self.count - 1)

    @property
    def std(self) -> float:
        return math.sqrt(max(self.variance, self.eps))

    def normalize(self, x: float) -> float:
        if self.count < 2:
            return 0.0
        z = (float(x) - self.mean) / (self.std + self.eps)
        return float(np.clip(z, -self.clip, self.clip))

    def normalize_tensor(self, x: torch.Tensor) -> torch.Tensor:
        """Normalize a torch tensor using CURRENT stats (sample-time)."""
        if self.count < 2:
            return torch.zeros_like(x)
        z = (x - self.mean) / (self.std + self.eps)
        return z.clamp_(-self.clip, self.clip)

    def state_dict(self) -> dict:
        return {"count": self.count, "mean": self.mean,
                "m2": self.m2, "eps": self.eps, "clip": self.clip}

    def load_state_dict(self, state: dict) -> None:
        self.count = int(state["count"])
        self.mean = float(state["mean"])
        self.m2 = float(state["m2"])
        self.eps = float(state.get("eps", 1e-6))
        self.clip = float(state.get("clip", 10.0))


# Backward-compat alias for any existing import
RunningNormalizer = WelfordNormalizer


# ────────────────────────────────────────────────────────────────────────────
# #6   Lagrangian dual ascent with projection (NO smoothing)
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class LagrangianDualAscent:
    """Projected dual gradient ascent for constrained RL.

    Update rule:
        λ_{t+1} = max(λ_min, min(λ_max, λ_t + η * (τ - x_t)))

    Where τ is the target and x_t is the measurement.
    When x_t > τ (over-satisfied), λ decays immediately — unlike a
    smoothed EMA which has O(1/(1-α)) response time.

    Reference: Chow et al., "Risk-Constrained RL with Percentile
    Risk Criteria" (2018); Tessler et al., "Reward Constrained PG" (2019).
    """
    target: float = 0.75
    lr: float = 1.0                 # NOT 0.01 — dual variable is linear
    lambda_min: float = 0.0
    lambda_max: float = 20.0
    initial_lambda: float = 1.0
    value: float = field(init=False)

    def __post_init__(self) -> None:
        self.value = float(np.clip(
            self.initial_lambda, self.lambda_min, self.lambda_max
        ))
        self._history: list[tuple[float, float, float]] = []

    def update(self, measurement: float) -> float:
        # Positive when under target → lambda grows
        # Negative when over target → lambda shrinks (projected at 0)
        gap = self.target - float(measurement)
        raw = self.value + self.lr * gap
        self.value = float(np.clip(raw, self.lambda_min, self.lambda_max))
        self._history.append((float(measurement), gap, self.value))
        return self.value

    def penalty(self, rolling_measurement: float) -> float:
        """Per-step penalty for the RL reward."""
        gap = self.target - float(rolling_measurement)
        if gap <= 0.0:
            return 0.0
        return self.value * gap

    def state_dict(self) -> dict:
        return {"target": self.target, "lr": self.lr,
                "lambda_min": self.lambda_min, "lambda_max": self.lambda_max,
                "value": self.value, "history": self._history[-100:]}

    def load_state_dict(self, state: dict) -> None:
        self.target = float(state["target"])
        self.lr = float(state["lr"])
        self.lambda_min = float(state["lambda_min"])
        self.lambda_max = float(state["lambda_max"])
        self.value = float(state["value"])
        self._history = list(state.get("history", []))


# ────────────────────────────────────────────────────────────────────────────
# #12  Closed-form epsilon schedule
# ────────────────────────────────────────────────────────────────────────────
def compute_epsilon_decay(eps_start: float, eps_end: float,
                          num_episodes: int) -> float:
    """Return the per-episode decay rate that exactly reaches `eps_end`
    after `num_episodes` updates of the form ε_{t+1} = ε_t * decay."""
    if num_episodes <= 0:
        return 1.0
    eps_start = max(float(eps_start), 1e-8)
    eps_end = max(float(eps_end), 1e-8)
    if eps_end >= eps_start:
        return 1.0
    return (eps_end / eps_start) ** (1.0 / float(num_episodes))


class EpsilonSchedule:
    """Pre-computed epsilon-greedy schedule with hard floor.

    Call `step()` once per episode (NOT per gradient update).
    """
    def __init__(self, eps_start: float = 1.0, eps_end: float = 0.05,
                 num_episodes: int = 500, min_steps_at_floor: int = 20):
        self.eps_start = float(eps_start)
        self.eps_end = float(eps_end)
        self.num_episodes = int(num_episodes)
        self.decay = compute_epsilon_decay(
            eps_start, eps_end, num_episodes - min_steps_at_floor
        )
        self.value = self.eps_start
        self.t = 0

    def step(self) -> float:
        self.t += 1
        self.value = max(self.eps_end, self.value * self.decay)
        return self.value

    def reset(self) -> None:
        self.value = self.eps_start
        self.t = 0


# ────────────────────────────────────────────────────────────────────────────
# #11  Bounded Q-diversity regularizer (softmax entropy)
# ────────────────────────────────────────────────────────────────────────────
def q_entropy_regularizer(q_values: torch.Tensor,
                          temperature: float = 1.0) -> torch.Tensor:
    """Bounded diversity bonus based on the softmax entropy of Q-values.

    H(π) = -Σ π(a|s) log π(a|s),   π = softmax(Q / T)
    Bounded in [0, log(N_actions)]. Maximize H to diversify.

    Returns a scalar (mean over batch). ADD this with a NEGATIVE
    coefficient to the loss:
        loss = bellman_loss - entropy_coef * H   (entropy_coef > 0)
    Unlike var(Q), this cannot be minimized by driving Q_max → ∞.
    """
    if q_values.numel() == 0:
        return torch.zeros((), device=q_values.device)
    log_probs = F.log_softmax(q_values / max(temperature, 1e-6), dim=-1)
    probs = log_probs.exp()
    entropy = -(probs * log_probs).sum(dim=-1).mean()
    return entropy
