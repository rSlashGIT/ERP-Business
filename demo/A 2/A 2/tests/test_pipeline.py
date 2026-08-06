"""
End-to-end pipeline tests for the CMA-ES supply chain system.

Run with:  python -m pytest tests/ -v
           python tests/test_pipeline.py   (standalone)
"""

import sys
import os
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ─── Environment ────────────────────────────────────────────────────────────

class TestSupplyChainEnv:
    DATA_PATH   = 'data/processed/processed_data_m5.csv'
    SCALER_PATH = 'data/processed/scaler_m5.pkl'

    def _make_env(self, **kwargs):
        from environment import SupplyChainEnv
        return SupplyChainEnv(
            data_path=self.DATA_PATH,
            scaler_path=self.SCALER_PATH,
            **kwargs
        )

    def test_reset_returns_state(self):
        env = self._make_env()
        state = env.reset()
        assert state is not None
        assert np.all(np.isfinite(state))

    def test_state_size_is_stable(self):
        """State vector must not grow across steps (BUG 1 regression)."""
        env = self._make_env()
        state = env.reset()
        size = len(state)
        for _ in range(10):
            _, _, done, _ = env.step(3)
            if done:
                state = env.reset()
            assert len(env.state) == size, "State size changed during episode"

    def test_action_space(self):
        env = self._make_env()
        env.reset()
        for action in range(8):
            env.reset()
            s, r, done, info = env.step(action)
            assert np.isfinite(r), f"Non-finite reward for action {action}"
            assert 'profit' not in info or np.isfinite(info.get('revenue', 0))

    def test_invalid_action_raises(self):
        env = self._make_env()
        env.reset()
        with pytest.raises(ValueError):
            env.step(99)

    def test_service_level_tracked(self):
        env = self._make_env()
        env.reset()
        for _ in range(30):
            _, _, done, info = env.step(4)
            assert 0.0 <= info['rolling_sl'] <= 1.0
            if done:
                break

    def test_tactical_state_shape(self):
        env = self._make_env()
        env.reset()
        ts = env.get_tactical_state()
        assert ts.shape == (7,)
        assert np.all(np.isfinite(ts))

    def test_data_split_no_overlap(self):
        """Train split and holdout split must not share rows."""
        from environment import SupplyChainEnv
        train_env = SupplyChainEnv(
            data_path=self.DATA_PATH, scaler_path=self.SCALER_PATH,
            data_split=(0.0, 0.8)
        )
        test_env = SupplyChainEnv(
            data_path=self.DATA_PATH, scaler_path=self.SCALER_PATH,
            data_split=(0.8, 1.0)
        )
        assert len(train_env.df) > len(test_env.df)
        # Check no row-count overlap (simple sanity)
        assert len(train_env.df) + len(test_env.df) <= 1942


# ─── ParameterizedSSPolicy ───────────────────────────────────────────────────

class TestParameterizedSSPolicy:
    def _make_policy(self, theta=None):
        from hybrid_policy import ParameterizedSSPolicy
        return ParameterizedSSPolicy(theta)

    def test_default_theta_shape(self):
        p = self._make_policy()
        assert p.get_params().shape == (8,)

    def test_act_returns_valid_action(self):
        p = self._make_policy()
        state = {'inventory_level': 1.0, 'demand_forecast': 0.5,
                 'demand_std': 0.2, 'lead_time': 2.0}
        action = p.act(state)
        assert isinstance(action, int)
        assert 0 <= action <= 7

    def test_set_get_params_roundtrip(self):
        p = self._make_policy()
        theta = np.array([1.0, 4.0, 0.1, 0.2, 0.0, 0.3, 0.1, 0.0])
        p.set_params(theta)
        np.testing.assert_array_almost_equal(p.get_params(), theta)

    def test_high_inventory_orders_nothing(self):
        """When inventory >> reorder point, action should be 0 (no order)."""
        p = self._make_policy()  # default: s_base=1.5, S_base=5.0, all w=0
        # inventory_level=2.5 > s_base=1.5 → no order
        state = {'inventory_level': 2.5, 'demand_forecast': 0.3,
                 'demand_std': 0.1, 'lead_time': 2.0}
        action = p.act(state)
        assert action == 0

    def test_zero_inventory_orders(self):
        """When inventory = 0, policy must order something."""
        p = self._make_policy()
        state = {'inventory_level': 0.0, 'demand_forecast': 0.5,
                 'demand_std': 0.2, 'lead_time': 2.0}
        action = p.act(state)
        assert action > 0


# ─── SsPolicy (classical baseline) ──────────────────────────────────────────

class TestSsPolicy:
    def test_orders_when_below_s(self):
        from environment import SsPolicy
        p = SsPolicy(s=200.0, S=800.0)
        # tactical_state[0] = inventory/1500; 100/1500 < 200/1500
        state = np.zeros(7)
        state[0] = 100.0 / 1500.0
        action = p.select_action(state)
        assert action > 0

    def test_no_order_when_above_s(self):
        from environment import SsPolicy
        p = SsPolicy(s=200.0, S=800.0)
        state = np.zeros(7)
        state[0] = 500.0 / 1500.0   # 500 > 200 → no order
        action = p.select_action(state)
        assert action == 0


# ─── WelfordNormalizer ───────────────────────────────────────────────────────

class TestWelfordNormalizer:
    def test_zero_before_two_samples(self):
        from rl_debug_utils import WelfordNormalizer
        n = WelfordNormalizer()
        n.update(5.0)
        assert n.normalize(5.0) == 0.0  # count < 2

    def test_normalizes_mean_to_zero(self):
        import torch
        from rl_debug_utils import WelfordNormalizer
        n = WelfordNormalizer()
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        for x in data:
            n.update(x)
        mean_norm = n.normalize(float(np.mean(data)))
        assert abs(mean_norm) < 0.2   # approx 0 at the mean

    def test_state_dict_roundtrip(self):
        from rl_debug_utils import WelfordNormalizer
        n = WelfordNormalizer()
        for x in range(10):
            n.update(float(x))
        sd = n.state_dict()
        n2 = WelfordNormalizer()
        n2.load_state_dict(sd)
        assert n2.count == n.count
        assert abs(n2.mean - n.mean) < 1e-9


# ─── EpsilonSchedule ─────────────────────────────────────────────────────────

class TestEpsilonSchedule:
    def test_starts_at_eps_start(self):
        from rl_debug_utils import EpsilonSchedule
        sch = EpsilonSchedule(eps_start=1.0, eps_end=0.05, num_episodes=100)
        assert sch.value == 1.0

    def test_reaches_floor(self):
        from rl_debug_utils import EpsilonSchedule
        sch = EpsilonSchedule(eps_start=1.0, eps_end=0.05, num_episodes=100)
        for _ in range(200):
            sch.step()
        assert sch.value <= 0.05 + 1e-9

    def test_monotone_decreasing(self):
        from rl_debug_utils import EpsilonSchedule
        sch = EpsilonSchedule(eps_start=1.0, eps_end=0.05, num_episodes=50)
        prev = sch.value
        for _ in range(60):
            sch.step()
            assert sch.value <= prev + 1e-9
            prev = sch.value


# ─── Holiday calendar ────────────────────────────────────────────────────────

class TestHolidayCalendar:
    def test_christmas_day_is_holiday(self):
        from holiday_calendar import get_holiday_multiplier
        # index 358 → Dec 25 relative to 2024-01-01
        mult = get_holiday_multiplier(358)
        assert mult == 2.0

    def test_regular_day_is_1x(self):
        from holiday_calendar import get_holiday_multiplier
        # index 100 → ~Apr 10 (no holidays)
        mult = get_holiday_multiplier(100)
        assert mult == 1.0

    def test_is_holiday_near_range(self):
        from holiday_calendar import is_holiday_near
        for idx in range(365):
            val = is_holiday_near(idx)
            assert 0.0 <= val <= 1.0, f"Out-of-range at index {idx}: {val}"

    def test_holiday_signal_is_1_on_holiday(self):
        from holiday_calendar import is_holiday_near
        # Jan 1 = index 0
        assert is_holiday_near(0) == 1.0


# ─── ServiceLevelGuard ───────────────────────────────────────────────────────

class TestServiceLevelGuard:
    DATA_PATH   = 'data/processed/processed_data_m5.csv'
    SCALER_PATH = 'data/processed/scaler_m5.pkl'

    def test_guard_never_lowers_action(self):
        from environment import SupplyChainEnv, SsPolicy
        from service_level_guard import ServiceLevelGuard

        env = SupplyChainEnv(
            data_path=self.DATA_PATH, scaler_path=self.SCALER_PATH
        )
        base_policy = SsPolicy(s=200.0, S=800.0)
        guard = ServiceLevelGuard(agent=base_policy, env=env,
                                  target_service_level=0.97, warmup_steps=0)

        env.reset()
        for _ in range(30):
            ts = env.get_tactical_state()
            base_action = base_policy.select_action(ts)
            guarded_action = guard.select_action(ts)
            qty_base    = env.action_map[base_action]
            qty_guarded = env.action_map[guarded_action]
            assert qty_guarded >= qty_base, \
                f"Guard lowered action: {qty_guarded} < {qty_base}"
            _, _, done, info = env.step(guarded_action)
            guard.observe(info)
            if done:
                break


# ─── Standalone runner ───────────────────────────────────────────────────────

if __name__ == '__main__':
    import traceback

    suites = [
        TestSupplyChainEnv,
        TestParameterizedSSPolicy,
        TestSsPolicy,
        TestWelfordNormalizer,
        TestEpsilonSchedule,
        TestHolidayCalendar,
        TestServiceLevelGuard,
    ]

    passed = failed = 0
    for suite_cls in suites:
        suite = suite_cls()
        for name in [m for m in dir(suite_cls) if m.startswith('test_')]:
            try:
                getattr(suite, name)()
                print(f'  PASS  {suite_cls.__name__}.{name}')
                passed += 1
            except Exception as e:
                print(f'  FAIL  {suite_cls.__name__}.{name}')
                traceback.print_exc()
                failed += 1

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)
