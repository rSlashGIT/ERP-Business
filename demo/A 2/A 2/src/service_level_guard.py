"""
Service-Level Guard: inference-time reorder-point overlay for the
hierarchical DQN supply-chain agent.

Purpose
-------
Lifts realised service level above the policy's raw output (~63%, "B+")
toward a configurable target (default 97%) WITHOUT retraining the
underlying RL agent. The guard wraps any object exposing
``select_action(tactical_state, ...)`` and monotonically *raises* the
chosen action whenever projected demand over the lead time is not
covered by current inventory. It will never lower the agent's action,
so the cost trade-offs the DQN already learned are preserved.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional

import numpy as np


class ServiceLevelGuard:
    """Reorder-point overlay around a trained supply-chain RL agent."""

    INVENTORY_SCALE = 1500.0
    INVENTORY_STATE_INDEX = 0

    def __init__(
        self,
        agent: Any,
        env: Any,
        target_service_level: float = 0.97,
        lead_time: int = 3,
        history_window: int = 21,
        warmup_steps: int = 7,
        z_init: float = 1.96,
        z_min: float = 1.28,
        z_max: float = 3.00,
        z_lr: float = 0.6,
        ema_alpha: float = 0.35,
        verbose: bool = False,
    ) -> None:
        if not (0.0 < target_service_level <= 1.0):
            raise ValueError("target_service_level must be in (0, 1].")
        if lead_time < 1:
            raise ValueError("lead_time must be >= 1.")

        self.agent = agent
        self.env = env
        self.action_map = dict(env.action_map)
        self._sorted_actions = sorted(
            self.action_map.items(), key=lambda kv: kv[1]
        )

        self.target_service_level = float(target_service_level)
        self.lead_time = int(lead_time)
        self.history_window = int(history_window)
        self.warmup_steps = int(warmup_steps)

        self.z = float(z_init)
        self.z_init = float(z_init)
        self.z_min = float(z_min)
        self.z_max = float(z_max)
        self.z_lr = float(z_lr)
        self.ema_alpha = float(ema_alpha)
        self.verbose = bool(verbose)

        self._demand_history: deque = deque(maxlen=self.history_window)
        self._demand_ema: Optional[float] = None
        self._steps_seen: int = 0
        self._upgrades: int = 0
        self._calls: int = 0

    def reset(self) -> None:
        """Reset per-episode state."""
        self._demand_history.clear()
        self._demand_ema = None
        self._steps_seen = 0
        self._upgrades = 0
        self._calls = 0

    def reset_z(self) -> None:
        self.z = self.z_init

    def select_action(self, tactical_state: np.ndarray, *args, **kwargs) -> int:
        self._calls += 1
        base_action = int(
            self.agent.select_action(tactical_state, *args, **kwargs)
        )
        try:
            return self._guard(tactical_state, base_action)
        except Exception as exc:
            if self.verbose:
                print(f"[ServiceLevelGuard] fallback to base action: {exc}")
            return base_action

    def observe(self, info: dict) -> None:
        """Feed env.step info dict back to update demand window and PI controller."""
        if not isinstance(info, dict):
            return

        demand = float(info.get("demand", 0.0))
        if demand > 0.0:
            self._demand_history.append(demand)
            if self._demand_ema is None:
                self._demand_ema = demand
            else:
                self._demand_ema = (
                    self.ema_alpha * demand
                    + (1.0 - self.ema_alpha) * self._demand_ema
                )
            self._steps_seen += 1

        rolling_sl = info.get("rolling_sl")
        if rolling_sl is not None:
            self._update_z(float(rolling_sl))

    @property
    def upgrade_rate(self) -> float:
        return self._upgrades / self._calls if self._calls else 0.0

    def stats(self) -> dict:
        return {
            "calls": self._calls,
            "upgrades": self._upgrades,
            "upgrade_rate": self.upgrade_rate,
            "z": self.z,
            "demand_ema": self._demand_ema,
            "history_len": len(self._demand_history),
        }

    def _guard(self, tactical_state: np.ndarray, base_action: int) -> int:
        if self._steps_seen < self.warmup_steps or len(self._demand_history) < 2:
            return base_action

        on_hand = self._on_hand_inventory(tactical_state)
        rop = self._reorder_point()
        required = max(0.0, rop - on_hand)

        base_qty = float(self.action_map.get(base_action, 0))
        if base_qty >= required:
            return base_action

        upgraded = self._smallest_action_meeting(required)
        if upgraded != base_action:
            self._upgrades += 1
        return upgraded

    def _on_hand_inventory(self, tactical_state: np.ndarray) -> float:
        try:
            scaled = float(tactical_state[self.INVENTORY_STATE_INDEX])
        except (IndexError, TypeError, ValueError):
            return 0.0
        return max(0.0, scaled * self.INVENTORY_SCALE)

    def _reorder_point(self) -> float:
        demands = np.fromiter(self._demand_history, dtype=np.float64)
        mu = float(self._demand_ema if self._demand_ema is not None else demands.mean())
        sigma = float(demands.std(ddof=1)) if demands.size >= 2 else 0.0
        lt = float(self.lead_time)
        return mu * lt + self.z * sigma * np.sqrt(lt)

    def _smallest_action_meeting(self, required: float) -> int:
        best_idx = self._sorted_actions[-1][0]
        for idx, qty in self._sorted_actions:
            if qty >= required:
                return idx
        return best_idx

    def _update_z(self, rolling_sl: float) -> None:
        gap = self.target_service_level - rolling_sl
        new_z = self.z + self.z_lr * gap
        self.z = float(np.clip(new_z, self.z_min, self.z_max))


def wrap(agent: Any, env: Any, **kwargs: Any) -> ServiceLevelGuard:
    """Convenience constructor: guarded = wrap(agent, env)"""
    return ServiceLevelGuard(agent, env, **kwargs)
