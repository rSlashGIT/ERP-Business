"""
Moving Average Policy for Supply Chain Management.

This policy uses a simple 7-day rolling average of historical demand
to determine order quantities.
"""

import numpy as np
from collections import deque
from typing import Dict


class MovingAveragePolicy:
    """
    Simple baseline that orders based on rolling average demand.

    Strategy:
    - Maintain a 7-day rolling window of historical demand
    - Order quantity = mean(last 7 days demand)
    - Clip to valid action space
    """

    def __init__(self, window_size: int = 7):
        """
        Initialize Moving Average Policy.

        Args:
            window_size: Number of days to average (default: 7)
        """
        self.window_size = window_size
        self.demand_history = deque(maxlen=window_size)

        self.action_map = {
            0: 0, 1: 50, 2: 150, 3: 300,
            4: 500, 5: 750, 6: 1000, 7: 1500,
        }

    def select_action(self, state: np.ndarray, info: Dict) -> int:
        """
        Select action based on rolling average demand.

        Args:
            state: Current environment state (not used directly)
            info: Dictionary containing 'demand' key with current demand value

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

        # Map to closest action
        action = self._map_quantity_to_action(avg_demand)

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
