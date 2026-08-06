"""
Supply Chain Environment — M5 Walmart data.
"""

import warnings
import pandas as pd
import numpy as np
import random
import joblib
import os

warnings.filterwarnings('ignore', category=UserWarning, module='sklearn')
from collections import deque
from typing import Dict, List, Optional, Tuple

from holiday_calendar import get_holiday_multiplier, is_holiday_near


class _IdentityScaler:
    """Pass-through scaler for clean-data mode (real units, no z-scoring)."""
    def transform(self, x):
        return np.asarray(x, dtype=float)
    def inverse_transform(self, x):
        return np.asarray(x, dtype=float)


# Unit cost as a fraction of selling price for clean-mode synthetic features
CLEAN_UNIT_COST_FRACTION = 0.6


class SupplyChainEnv:
    def __init__(
        self,
        data_path='data/processed/m5_clean.csv',
        scaler_path='data/processed/scaler_m5.pkl',
        uncertainty_model=None,
        use_uncertainty=False,
        sequence_length=10,
        enable_hierarchical=False,
        operational_history_window=30,
        data_split=(0.0, 1.0),
        num_products=1,
    ):
        self.num_products = num_products
        self.max_warehouse_capacity = 5000

        if not os.path.exists(data_path):
            fallback = 'data/processed/processed_data.csv'
            fallback_scaler = 'data/processed/scaler.pkl'
            if os.path.exists(fallback):
                print(f"Warning: {data_path} not found, falling back to {fallback}")
                data_path = fallback
                scaler_path = fallback_scaler
            else:
                raise FileNotFoundError(f"No data found at {data_path} or {fallback}")

        raw_df = pd.read_csv(data_path)
        # Clean mode: real-unit M5 data with 'demand' + 'price' columns.
        # Legacy mode: z-scored 9-feature CSV that requires scaler_m5.pkl.
        self._clean_mode = ('demand' in raw_df.columns and 'price' in raw_df.columns)

        if self._clean_mode:
            price = raw_df['price'].astype(float)
            self.df = pd.DataFrame({
                'Price':                  price,
                'Number of products sold': raw_df['demand'].astype(float),
                'Stock levels':           0.0,
                'Lead times':             2.0,
                'Order quantities':       0.0,
                'Shipping costs':         0.0,
                'Manufacturing costs':    price * CLEAN_UNIT_COST_FRACTION,
                'Defect rates':           0.0,
                'Costs':                  0.0,
            })
        else:
            self.df = raw_df

        start_idx = int(len(self.df) * data_split[0])
        end_idx = int(len(self.df) * data_split[1])
        if end_idx - start_idx < sequence_length + 20:
            start_idx = 0
            end_idx = len(self.df)

        self.df = self.df.iloc[start_idx:end_idx].reset_index(drop=True)
        self.df = self.df.astype(float)
        self.columns = self.df.columns.tolist()

        if self._clean_mode:
            self.scaler = _IdentityScaler()
        else:
            if not os.path.exists(scaler_path):
                raise FileNotFoundError(f"Scaler not found at {scaler_path}.")
            self.scaler = joblib.load(scaler_path)

        self.action_map = {
            0: 0, 1: 50, 2: 150, 3: 300,
            4: 500, 5: 750, 6: 1000, 7: 1500,
        }

        self.allocation_map = {
            0: np.array([0.0, 0.0]),
            1: np.array([0.8, 0.2]),
            2: np.array([0.7, 0.3]),
            3: np.array([0.6, 0.4]),
            4: np.array([0.5, 0.5]),
            5: np.array([0.4, 0.6]),
            6: np.array([0.3, 0.7]),
            7: np.array([0.5, 0.5]),
        }

        self.IDX_PRICE = 0
        self.IDX_DEMAND = 1
        self.IDX_STOCK = 2
        self.IDX_MFG_COST = 6
        self.IDX_SHIP_COST = 5
        self.IDX_DEFECT = 7
        self.NUM_FEATURES_COUNT = 9
        self.HOLIDAY_FEATURE_SIZE = 1

        self.max_steps = 90
        self.current_step = 0
        self.current_idx = 0
        self.state = None

        self.internal_inventory = np.zeros(self.num_products)
        self.demand_volatility_window = deque(maxlen=10)
        self.stockout_history = deque(maxlen=5)

        if self._clean_mode:
            # Clean-mode economics for FOODS_3_090 @ CA_1
            # Selling price ~$1.38, unit cost ~$0.83 (60%), margin ~$0.55
            # Holding ~2% of unit cost/day; stockout ~1.5x price (margin loss + goodwill)
            self.HOLDING_COST_FACTOR = 0.02
            self.STOCKOUT_PENALTY_FACTOR = 2.0
            self.DEFECT_PENALTY_FACTOR = 0.0
        else:
            # Legacy z-scored economics
            self.HOLDING_COST_FACTOR = 0.05
            self.STOCKOUT_PENALTY_FACTOR = 3.0
            self.DEFECT_PENALTY_FACTOR = 0.1

        self.use_uncertainty = False
        self.sequence_length = sequence_length
        self.current_uncertainty = None
        self.demand_scale = 1.0

        self.enable_hierarchical = enable_hierarchical
        self.operational_history_window = operational_history_window
        self.strategic_mode = 0
        self.previous_strategic_mode = 0

        self.operational_demand_history = deque(maxlen=operational_history_window)
        self.operational_cost_history = deque(maxlen=operational_history_window)
        self.operational_inventory_history = deque(maxlen=operational_history_window)
        self.operational_sales_history = deque(maxlen=operational_history_window)
        self.operational_revenue_history = deque(maxlen=operational_history_window)

        self._init_strategic_effects()

        self.cumulative_sales = 0.0
        self.cumulative_demand = 0.0
        self.SL_TARGET = 0.95

    def _init_strategic_effects(self):
        # IDENTITY — strategic modes no longer distort cost signal.
        # Fair comparison: baseline and RL see the exact same env dynamics
        # regardless of which "mode" they are in.
        identity = {
            'reorder_multiplier': 1.0,
            'holding_cost_multiplier': 1.0,
            'stockout_penalty_multiplier': 1.0,
            'cost_multiplier': 1.0,
        }
        self.strategic_effects = {0: dict(identity), 1: dict(identity), 2: dict(identity)}

    def set_demand_scale(self, scale: float) -> None:
        self.demand_scale = scale

    def set_strategic_mode(self, mode: int) -> None:
        if mode not in self.strategic_effects:
            raise ValueError(f"Invalid strategic mode: {mode}. Must be 0-2.")
        self.strategic_mode = mode

    def get_strategic_mode(self) -> int:
        return self.strategic_mode

    def get_operational_history(self) -> Dict[str, List[float]]:
        return {
            'demand': list(self.operational_demand_history),
            'cost': list(self.operational_cost_history),
            'inventory': list(self.operational_inventory_history),
            'sales': list(self.operational_sales_history),
            'revenue': list(self.operational_revenue_history)
        }

    def get_tactical_state(self) -> np.ndarray:
        """7 REAL features — no fake quantiles.
        Shape preserved for checkpoint compatibility."""
        inventory_p0 = float(self.internal_inventory[0])
        scaled = self.base_state[:self.NUM_FEATURES_COUNT].reshape(1, -1)
        real = self.scaler.inverse_transform(scaled)[0]

        demand = float(real[self.IDX_DEMAND])
        cost = float(real[self.IDX_MFG_COST] + real[self.IDX_SHIP_COST])

        # Real rolling statistics (from demand_volatility_window, always tracked)
        window = list(self.demand_volatility_window)
        if len(window) >= 2:
            recent_mean = float(np.mean(window))
            recent_std = float(np.std(window))
            cv = recent_std / (recent_mean + 1e-6)
        else:
            recent_mean = demand
            cv = 0.0

        expected_demand = max(recent_mean, 1.0)
        days_of_cover = inventory_p0 / expected_demand
        safety_ratio = inventory_p0 / (recent_mean + 1e-6)
        stockout_rate = (float(np.mean(self.stockout_history))
                         if len(self.stockout_history) > 0 else 0.0)

        if self.num_products > 1:
            inventory_p1 = float(self.internal_inventory[1])
            total_inventory = inventory_p0 + inventory_p1 + 1e-6
            inventory_ratio_p0 = inventory_p0 / total_inventory
            doc_p1 = inventory_p1 / expected_demand
            return np.array([
                np.clip(inventory_p0 / 1500.0, 0.0, 3.0),
                np.clip(inventory_p1 / 1500.0, 0.0, 3.0),
                inventory_ratio_p0,
                np.clip(days_of_cover / 15.0, 0.0, 2.0),
                np.clip(doc_p1 / 15.0, 0.0, 2.0),
                np.clip(demand / 300.0, 0.0, 2.0),
                np.clip(recent_mean / 300.0, 0.0, 2.0),
                np.clip(cv, 0.0, 2.0),
                np.clip(cost / 10.0, 0.0, 2.0),
                np.clip(safety_ratio / 5.0, 0.0, 2.0),
                np.clip(stockout_rate, 0.0, 1.0),
            ], dtype=np.float32)

        return np.array([
            np.clip(inventory_p0 / 1500.0, 0.0, 3.0),     # 1. inventory
            np.clip(demand / 300.0, 0.0, 2.0),            # 2. today's demand
            np.clip(recent_mean / 300.0, 0.0, 2.0),       # 3. rolling demand mean
            np.clip(cv, 0.0, 2.0),                        # 4. demand volatility
            np.clip(cost / 10.0, 0.0, 2.0),               # 5. unit cost
            np.clip(days_of_cover / 15.0, 0.0, 2.0),      # 6. days of cover
            np.clip(stockout_rate, 0.0, 1.0),             # 7. recent stockout rate
        ], dtype=np.float32)

    def apply_strategic_effects(self, order_quantity, holding_cost, stockout_penalty,
                                 procurement_cost, strategic_mode=None):
        if strategic_mode is None:
            strategic_mode = self.strategic_mode
        effects = self.strategic_effects[strategic_mode]
        return (
            order_quantity * effects['reorder_multiplier'],
            holding_cost * effects['holding_cost_multiplier'],
            stockout_penalty * effects['stockout_penalty_multiplier'],
            procurement_cost * effects['cost_multiplier']
        )

    def _compute_service_level_penalty(self, rolling_sl: float) -> float:
        return 0.0


    def reset(self):
        max_start = max(0, len(self.df) - self.max_steps - 1)
        self.current_idx = random.randint(0, max_start)
        self.current_step = 0

        # FIX BUG 1: Separate base_state (9 features) from full state
        raw_row = self.df.iloc[self.current_idx].values.copy()
        self.base_state = raw_row[:self.NUM_FEATURES_COUNT].copy()

        real_start = self.scaler.inverse_transform(
            self.base_state.reshape(1, -1)
        )[0]
        current_inventory = real_start[self.IDX_STOCK]

        if self.enable_hierarchical and len(self.operational_demand_history) >= 5:
            recent_demand = np.mean(list(self.operational_demand_history)[-5:])
        else:
            recent_demand = real_start[self.IDX_DEMAND] * self.demand_scale

        # Build full state from base
        self.state = self._build_full_state(current_inventory, recent_demand)

        self.internal_inventory = np.full(self.num_products, current_inventory)
        self.demand_volatility_window.clear()
        self.stockout_history.clear()

        if self.enable_hierarchical:
            self.strategic_mode = 0
            self.previous_strategic_mode = 0
            self.operational_demand_history.clear()
            self.operational_cost_history.clear()
            self.operational_inventory_history.clear()
            self.operational_sales_history.clear()
            self.operational_revenue_history.clear()

        self.cumulative_sales = 0.0
        self.cumulative_demand = 0.0

        return self.state.copy()

    def _build_full_state(self, current_inventory=None, recent_demand=None):
        """FIX BUG 1: Always rebuild full state from base_state (9 features).
        Never append to existing state — prevents state growth."""
        parts = [self.base_state.copy()]

        # Holiday signal
        holiday_signal = np.array([is_holiday_near(self.current_idx)], dtype=np.float32)
        parts.append(holiday_signal)

        # Safety buffer ratio
        if current_inventory is None or recent_demand is None:
            real = self.scaler.inverse_transform(
                self.base_state.reshape(1, -1)
            )[0]
            if current_inventory is None:
                current_inventory = real[self.IDX_STOCK]
            if recent_demand is None:
                if self.enable_hierarchical and len(self.operational_demand_history) >= 5:
                    recent_demand = np.mean(list(self.operational_demand_history)[-5:])
                else:
                    recent_demand = real[self.IDX_DEMAND] * self.demand_scale

        safety_ratio = current_inventory / (recent_demand + 1e-6)
        parts.append(np.array([np.clip(safety_ratio / 2.0, 0.0, 1.0)], dtype=np.float32))

        # Demand volatility
        if self.enable_hierarchical and len(self.operational_demand_history) >= 5:
            recent_demands = np.array(list(self.operational_demand_history)[-10:])
            cv = np.std(recent_demands) / (np.mean(recent_demands) + 1e-6)
            vol = np.clip(cv / 0.5, 0.0, 1.0)
        else:
            vol = 0.5
        parts.append(np.array([vol], dtype=np.float32))

        # Stockout rate
        rate = np.mean(list(self.stockout_history)) if len(self.stockout_history) > 0 else 0.0
        parts.append(np.array([rate], dtype=np.float32))

        return np.concatenate(parts)

    def step(self, action_idx):
        if action_idx not in self.action_map:
            raise ValueError(f"Invalid action: {action_idx}. Valid: 0-{len(self.action_map)-1}")

        order_quantity_scalar = self.action_map[action_idx]

        if self.num_products > 1:
            allocation_ratio = self.allocation_map.get(
                action_idx, np.ones(self.num_products) / self.num_products
            )
        else:
            allocation_ratio = np.ones(self.num_products)

        # Read from base_state (always 9 features)
        real_numericals = self.scaler.inverse_transform(
            self.base_state.reshape(1, -1)
        )[0]

        base_demand = real_numericals[self.IDX_DEMAND] * self.demand_scale
        holiday_multiplier = get_holiday_multiplier(self.current_idx)
        base_demand = base_demand * holiday_multiplier

        if self.num_products == 2:
            demand = np.array([base_demand * 1.3, base_demand * 0.7])
        else:
            demand = np.array([base_demand])

        stock = self.internal_inventory.copy()
        price = np.full(self.num_products, real_numericals[self.IDX_PRICE])
        mfg_cost = np.full(self.num_products, real_numericals[self.IDX_MFG_COST])
        ship_cost = np.full(self.num_products, real_numericals[self.IDX_SHIP_COST])
        defect_rate = np.full(self.num_products, real_numericals[self.IDX_DEFECT])

        order_quantity = allocation_ratio * order_quantity_scalar

        if self.enable_hierarchical:
            effects = self.strategic_effects[self.strategic_mode]
            order_quantity = order_quantity * effects['reorder_multiplier']

        I_current = stock
        sales = np.minimum(np.maximum(0, demand), np.maximum(0, I_current))
        revenue = sales * price
        I_next = np.maximum(0, I_current - demand) + order_quantity
        self.internal_inventory = I_next

        procurement_cost = order_quantity * (mfg_cost + ship_cost)
        holding_cost = np.maximum(0, I_next) * self.HOLDING_COST_FACTOR
        unmet_demand = np.maximum(0, demand - sales)
        stockout_penalty = unmet_demand * self.STOCKOUT_PENALTY_FACTOR

        had_stockout = 1.0 if np.any(unmet_demand > 0) else 0.0
        self.stockout_history.append(had_stockout)

        defect_cost = order_quantity * np.maximum(0, defect_rate) * self.DEFECT_PENALTY_FACTOR

        if self.enable_hierarchical:
            _, holding_cost, stockout_penalty, procurement_cost = self.apply_strategic_effects(
                order_quantity=order_quantity,
                holding_cost=holding_cost,
                stockout_penalty=stockout_penalty,
                procurement_cost=procurement_cost
            )

        total_sales_val = np.sum(sales)
        total_demand_val = np.sum(demand)
        total_inventory = np.sum(I_next)
        total_revenue = np.sum(revenue)

        self.demand_volatility_window.append(total_demand_val)

        self.cumulative_sales += total_sales_val
        self.cumulative_demand += total_demand_val

        rolling_sl = self.cumulative_sales / self.cumulative_demand if self.cumulative_demand > 0 else 1.0
        constraint_penalty = self._compute_service_level_penalty(rolling_sl)

        total_cost_real = (np.sum(holding_cost) + np.sum(procurement_cost) +
                           np.sum(defect_cost) + np.sum(stockout_penalty))

        # Reward = raw profit. SL target is implicit in the cost ratio:
        # stockout ($3.00/unit) vs holding ($0.05/unit) → critical ratio 98.4%.
        reward = float(total_revenue - total_cost_real)

        info = {
            'demand': total_demand_val,
            'cost': total_cost_real,
            'inventory': total_inventory,
            'sales': total_sales_val,
            'revenue': total_revenue,
            'holding_cost': np.sum(holding_cost),
            'stockout_penalty': np.sum(stockout_penalty),
            'procurement_cost': np.sum(procurement_cost),
            'strategic_mode': self.strategic_mode,
            'constraint_penalty': constraint_penalty,
            'rolling_sl': rolling_sl,
            'p_demand': demand,
            'p_sales': sales,
            'p_inventory': I_next,
            'holiday_multiplier': holiday_multiplier,
            'is_holiday_near': is_holiday_near(self.current_idx)
        }

        if self.enable_hierarchical:
            self.operational_demand_history.append(total_demand_val)
            self.operational_cost_history.append(total_cost_real)
            self.operational_inventory_history.append(total_inventory)
            self.operational_sales_history.append(total_sales_val)
            self.operational_revenue_history.append(total_revenue)

        self.current_idx += 1
        self.current_step += 1

        done = False
        if self.current_idx >= len(self.df) or self.current_step >= self.max_steps:
            done = True
            # FIX BUG 1: Update base_state, then rebuild full state
            current_real = self.scaler.inverse_transform(
                self.base_state.reshape(1, -1)
            )[0]
            current_real[self.IDX_STOCK] = I_next[0]
            self.base_state = self.scaler.transform(current_real.reshape(1, -1))[0]
            self.state = self._build_full_state()
            return self.state.copy(), reward, done, info

        next_row = self.df.iloc[self.current_idx].values.copy()
        next_real = self.scaler.inverse_transform(
            next_row[:self.NUM_FEATURES_COUNT].reshape(1, -1)
        )[0]
        next_real[self.IDX_STOCK] = I_next[0]
        self.base_state = self.scaler.transform(next_real.reshape(1, -1))[0]
        self.state = self._build_full_state()

        return self.state.copy(), reward, done, info

    def get_info(self) -> Dict[str, float]:
        return {}


class SsPolicy:
    """(s, S) reorder-point policy — classical inventory management baseline."""

    def __init__(self, s: float = 200.0, S: float = 800.0, action_map: dict = None):
        self.s = s
        self.S = S
        self.action_map = action_map or {
            0: 0, 1: 50, 2: 150, 3: 300, 4: 500, 5: 750, 6: 1000, 7: 1500
        }

    def select_action(self, tactical_state: np.ndarray, **kwargs) -> int:
        inventory = tactical_state[0] * 1500.0
        if inventory <= self.s:
            order_needed = max(0, self.S - inventory)
            best_action = 0
            best_diff = float('inf')
            for idx, qty in self.action_map.items():
                diff = abs(qty - order_needed)
                if diff < best_diff:
                    best_diff = diff
                    best_action = idx
            return best_action
        else:
            return 0

    def reset_episode(self):
        pass
