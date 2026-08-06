"""
Safety Stock Policy for Supply Chain Management.

This policy uses safety stock formula to account for demand variability.
Implements the classic inventory management approach:
Order = mean_demand + z * std_demand
"""

import numpy as np
from collections import deque
from typing import Dict


class SafetyStockPolicy:
    """
    Safety stock baseline using statistical demand forecasting.

    Strategy:
    - Compute rolling mean and standard deviation of demand
    - Apply safety stock formula: order = mean + 1.65 * std (95% service level)
    - Clip to valid action space
    """

    def __init__(self, window_size: int = 7, z_score: float = 1.65):
        """
        Initialize Safety Stock Policy.

        Args:
            window_size: Number of days for rolling statistics (default: 7)
            z_score: Safety factor (default: 1.65 for ~95% service level)
        """
        self.window_size = window_size
        self.z_score = z_score
        self.demand_history = deque(maxlen=window_size)

        self.action_map = {
            0: 0, 1: 50, 2: 150, 3: 300,
            4: 500, 5: 750, 6: 1000, 7: 1500,
        }

    def select_action(self, state: np.ndarray, info: Dict) -> int:
        """
        Select action based on safety stock formula.

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

        # Compute rolling statistics
        if len(self.demand_history) >= 2:
            mean_demand = np.mean(self.demand_history)
            std_demand = np.std(self.demand_history)
        else:
            # Not enough data, use current demand
            mean_demand = current_demand
            std_demand = 0.0

        # Safety stock formula
        order_quantity = mean_demand + self.z_score * std_demand

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
