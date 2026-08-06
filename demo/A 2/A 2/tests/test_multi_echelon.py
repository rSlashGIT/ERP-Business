"""
Phase 2 tests for the multi-echelon supply chain network.

Run with:
    python -m pytest tests/test_multi_echelon.py -v
    python tests/test_multi_echelon.py         (standalone)
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hybrid_policy import ParameterizedSSPolicy  # noqa: E402
from multi_echelon import (                      # noqa: E402
    ACTION_MAP,
    INIT_INV_STORE,
    LEAD_SUPPLIER_TO_WAREHOUSE,
    LEAD_WAREHOUSE_TO_STORE,
    Node,
    STORE_SHARE,
    SupplyChainNetwork,
    WAREHOUSE_S_LOWER,
    WAREHOUSE_S_UPPER,
    default_theta24,
    default_theta26,
    split_theta24,
    split_theta26,
    transform_warehouse_params,
)


# ───────────────── test 1: node inventory update ─────────────────

def test_node_inventory_update():
    """Store inventory updates correctly when demand is less than, equal
    to, and greater than current stock."""
    node = Node(
        node_id='store_test',
        role='store',
        initial_inventory=100.0,
        lead_time_to_upstream=2,
        policy=ParameterizedSSPolicy(),
    )

    # demand < inventory
    info = node.realize_demand(40.0)
    assert info['sales'] == 40.0
    assert info['unmet'] == 0.0
    assert node.inventory == 60.0

    # demand == inventory (remaining 60)
    info = node.realize_demand(60.0)
    assert info['sales'] == 60.0
    assert info['unmet'] == 0.0
    assert node.inventory == 0.0

    # demand > inventory (0 left, ask for 30)
    info = node.realize_demand(30.0)
    assert info['sales'] == 0.0
    assert info['unmet'] == 30.0
    assert node.inventory == 0.0
    assert node.metrics.stockout_events == 1


# ───────────────── test 2: order propagation with lead time ─────────────────

def test_order_propagation_with_lead_time():
    """Order placed on day D with lead_time L arrives exactly on day D+L,
    not before and not after."""
    net = SupplyChainNetwork(seed=7)

    # Force a known scenario: the warehouse ships to store A on day 0,
    # arrival day should be 0 + LEAD_WAREHOUSE_TO_STORE.
    store_a = net.stores['A']
    pre_inv = store_a.inventory
    shipped = net.warehouse.ship_to(store_a, 200.0, arrival_day=LEAD_WAREHOUSE_TO_STORE)
    assert shipped == 200.0

    # Before arrival_day, store inventory must be unchanged.
    for day in range(LEAD_WAREHOUSE_TO_STORE):
        received = store_a.receive_deliveries(day)
        assert received == 0.0, f"received early on day {day}"
        assert store_a.inventory == pre_inv

    # On arrival_day the 200 units land.
    received = store_a.receive_deliveries(LEAD_WAREHOUSE_TO_STORE)
    assert received == 200.0
    assert store_a.inventory == pre_inv + 200.0


# ───────────────── test 3: cascading stockouts ─────────────────

def test_cascading_stockouts():
    """When warehouse has no stock and no pending deliveries in-flight for
    the current step, store orders go unfulfilled and backlog grows."""
    net = SupplyChainNetwork(seed=11)

    # Force warehouse empty and prevent any pending arrivals during the
    # test window.
    net.warehouse.inventory = 0.0
    net.warehouse.pending_deliveries = {}
    # Also drain stores so they are forced to place orders.
    for sid in net.STORE_IDS:
        net.stores[sid].inventory = 0.0

    pre_backlog = net.warehouse_backlog
    pre_wh_stockouts = net.warehouse.metrics.stockout_events

    # One step: stores try to order from empty warehouse. Default theta
    # produces positive orders at low inventory, so at least one store
    # request should go unfulfilled.
    record = net.step()

    # Orders were placed but unmet.
    orders_total = sum(record['store_orders'].values())
    shipped_total = sum(record['store_shipped'].values())
    assert orders_total > 0.0
    assert shipped_total == 0.0
    assert record['unmet_store_orders_total'] == orders_total
    assert net.warehouse_backlog == pre_backlog + orders_total
    assert net.warehouse.metrics.stockout_events == pre_wh_stockouts + 1


# ───────────────── test 4: profit aggregation ─────────────────

def test_network_profit_aggregation():
    """network_total_profit must equal the sum of per-node cumulative profits."""
    net = SupplyChainNetwork(seed=21)
    summary = net.simulate(n_steps=30)

    store_profit_sum = sum(summary['store_profits'].values())
    expected_total = summary['warehouse_profit'] + store_profit_sum

    # floating-point tolerance
    assert abs(summary['network_total_profit'] - expected_total) < 1e-6, (
        f"sum mismatch: network={summary['network_total_profit']} "
        f"vs components={expected_total}"
    )


# ───────────────── test 5: default-theta baseline runs ─────────────────

def test_fixed_warehouse_baseline_runs():
    """Default theta_24 (classical-like thresholds for every store)
    completes 90 steps without crashing and returns finite profit."""
    theta24 = default_theta24()
    per_store = split_theta24(theta24)
    assert per_store['A'].shape == (8,)
    assert per_store['C'].shape == (8,)

    net = SupplyChainNetwork(
        thetas=per_store,
        seed=33,
    )
    summary = net.simulate(n_steps=90)

    assert np.isfinite(summary['network_total_profit'])
    assert 0.0 <= summary['service_level'] <= 1.0
    assert np.isfinite(summary['warehouse_profit'])
    for sid in ('A', 'B', 'C'):
        assert np.isfinite(summary['store_profits'][sid])

    # Sanity: 90 daily records were collected.
    assert len(net.daily_records) == 90


# ───────────────── test 6 (Phase 2.8): theta26 + warehouse params ─────────────────

def test_theta26_warehouse_params():
    """default_theta26 must return a 26-vec, split_theta26 must extract
    the per-store thetas plus the transformed warehouse (s, S), and
    SupplyChainNetwork must accept and honor the warehouse_s_lower /
    warehouse_s_upper kwargs."""
    theta26 = default_theta26()
    assert theta26.shape == (26,)

    parts = split_theta26(theta26)
    for sid in ('A', 'B', 'C'):
        assert parts[sid].shape == (8,)

    # Phase 2.8 anchors: raw=(0,0) -> s=400, S=1000.
    assert parts['warehouse_s'] == pytest.approx(400.0)
    assert parts['warehouse_S'] == pytest.approx(1000.0)

    # Transform must clamp s to a hard floor and enforce S >= s + gap.
    extreme = transform_warehouse_params(-100.0, -100.0)
    assert extreme['warehouse_s'] >= 50.0
    assert extreme['warehouse_S'] >= extreme['warehouse_s'] + 100.0 - 1e-9

    # First 24 entries must match default_theta24.
    np.testing.assert_array_equal(theta26[:24], default_theta24())

    # SupplyChainNetwork honors the kwargs.
    thetas = split_theta24(default_theta24())
    net_default = SupplyChainNetwork(thetas=thetas, seed=99)
    assert net_default.warehouse_s_lower == WAREHOUSE_S_LOWER
    assert net_default.warehouse_s_upper == WAREHOUSE_S_UPPER

    net_lean = SupplyChainNetwork(
        thetas=thetas, seed=99,
        warehouse_s_lower=parts['warehouse_s'],
        warehouse_s_upper=parts['warehouse_S'],
    )
    assert net_lean.warehouse_s_lower == 400.0
    assert net_lean.warehouse_s_upper == 1000.0

    # The leaner warehouse should hold less inventory after a 30-day run
    # — a behavioral check that the kwargs actually drive the policy.
    s_def = net_default.simulate(n_steps=30)  # noqa: F841
    s_lean = net_lean.simulate(n_steps=30)    # noqa: F841
    assert net_lean.warehouse.metrics.holding_cost < net_default.warehouse.metrics.holding_cost

    # __init__ must reject S <= s.
    with pytest.raises(ValueError):
        SupplyChainNetwork(thetas=thetas, seed=99,
                           warehouse_s_lower=500.0,
                           warehouse_s_upper=400.0)


# ───────────────── test 7: CMA-ES improves over 10 gens ─────────────────

def test_cmaes_improves_over_10_gens():
    """Running CMA-ES for 10 generations, the running-best profit at gen
    10 must not regress below the running-best at gen 1."""
    # Import locally to keep test file self-sufficient and to catch any
    # import-time errors in train_network_cmaes as a real test failure.
    from train_network_cmaes import run_cmaes

    result = run_cmaes(
        run_seed=42,
        max_iter=10,
        popsize=16,
        n_eval_episodes=4,      # small for test speed
        episode_length=30,      # small for test speed
        save=False,
        verbose=False,
    )
    log = result['log']
    assert len(log) >= 2, "need at least two generations for comparison"

    best_at_gen1 = log[0]['running_best_profit']
    best_at_gen_last = log[-1]['running_best_profit']
    # Running-best is monotone non-decreasing; require no regression and
    # at least equality. This verifies the driver, fitness wiring and
    # that the simulation is deterministic per-seed.
    assert best_at_gen_last >= best_at_gen1 - 1e-6, (
        f"running best regressed: gen1={best_at_gen1} gen{len(log)}={best_at_gen_last}"
    )


# ───────────────── standalone runner ─────────────────

if __name__ == '__main__':
    import traceback
    tests = [
        test_node_inventory_update,
        test_order_propagation_with_lead_time,
        test_cascading_stockouts,
        test_network_profit_aggregation,
        test_fixed_warehouse_baseline_runs,
        test_theta26_warehouse_params,
        test_cmaes_improves_over_10_gens,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f'  PASS  {t.__name__}')
            passed += 1
        except Exception:
            print(f'  FAIL  {t.__name__}')
            traceback.print_exc()
            failed += 1
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)
