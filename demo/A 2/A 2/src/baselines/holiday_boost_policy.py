"""
Holiday Boost Policy for Supply Chain Management.

This policy uses the holiday signal already present in the environment
to increase orders before holidays.
"""

import numpy as np
from collections import deque
from typing import Dict


class HolidayBoostPolicy:
    """
    Holiday-aware baseline that boosts orders when holidays are near.

    Strategy:
    - Maintain rolling average of demand (like moving average policy)
    - Check is_holiday_near feature from environment info
    - If holiday approaching: order = 1.5 * mean_demand
    - Else: order = mean_demand
    - Uses same holiday signal as RL agent (no hardcoded dates)
    """

    def __init__(self, window_size: int = 7, holiday_multiplier: float = 1.5):
        """
        Initialize Holiday Boost Policy.

        Args:
            window_size: Number of days to average (default: 7)
            holiday_multiplier: Multiplier for holiday periods (default: 1.5)
        """
        self.window_size = window_size
        self.holiday_multiplier = holiday_multiplier
        self.demand_history = deque(maxlen=window_size)

        self.action_map = {
            0: 0, 1: 50, 2: 150, 3: 300,
            4: 500, 5: 750, 6: 1000, 7: 1500,
        }

    def select_action(self, state: np.ndarray, info: Dict) -> int:
        """
        Select action based on holiday signal and rolling average.

        Args:
            state: Current environment state (not used directly)
            info: Dictionary containing:
                  - 'demand': current demand value
                  - 'is_holiday_near': float from 0.0-1.0 indicating holiday proximity

        Returns:
            Action index (0-7)
        """
        # Extract demand from info
        current_demand = info.get('demand', 0.0)

        # Add to history
        self.demand_history.append(current_demand)

        # Compute rolling average
        if len(self.demand_history) > 0:
            avg_demand = np.mean(self.demand_history)
        else:
            avg_demand = 0.0

        # Check holiday signal
        # is_holiday_near returns float from 0.0 (no holiday) to 1.0 (holiday today)
        # Use threshold of 0.5 to trigger boost
        is_holiday = info.get('is_holiday_near', 0.0) > 0.5

        # Apply holiday boost if needed
        if is_holiday:
            order_quantity = avg_demand * self.holiday_multiplier
        else:
            order_quantity = avg_demand

        # Ensure non-negative
        order_quantity = max(0.0, order_quantity)

        # Map to closest action
        action = self._map_quantity_to_action(order_quantity)

        return action

    def _map_quantity_to_action(self, quantity: float) -> int:
        """
        Map a desired order quantity to the closest valid action.

        Args:
            quantity: Desired order quantity

        Returns:
            Action index (0-7)
        """
        # Find closest action
        best_action = 0
        best_diff = float('inf')

        for action_idx, action_qty in self.action_map.items():
            diff = abs(quantity - action_qty)
            if diff < best_diff:
                best_diff = diff
                best_action = action_idx

        return best_action

    def reset(self):
        """Reset the policy state for a new episode."""
        self.demand_history.clear()
