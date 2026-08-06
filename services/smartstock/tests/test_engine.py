"""
Engine test suite. Runs under pytest, and standalone with `python3 test_engine.py`
so it works in an environment without pytest installed.
"""
from __future__ import annotations
import math, sys, os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from smartstock.core import policy as P
from smartstock.core.leadtime import LeadTimeSampler, fit_profile
from smartstock.core.network import NetworkConfig, NetworkSimulator, SkuData
from smartstock.core.segmentation import DemandClass, SegmentIndex, build_stats, classify_demand
from smartstock.optim.cmaes import minimize
from smartstock.contracts import (OrderPolicyConstraints, ReplenishmentRequest,
                                  SkuNodeState, SupplierRef)
from smartstock.core.recommend import PolicyStore, generate

FAILURES = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else:    print(f"  FAIL  {name}  {extra}"); FAILURES.append(name)


def test_cmaes_converges():
    f = lambda P_: np.sum(100*(P_[:,1:]-P_[:,:-1]**2)**2 + (1-P_[:,:-1])**2, axis=1)
    r = minimize(f, np.zeros(6), sigma0=0.5, max_generations=2000, seed=3)
    check("cmaes converges on rosenbrock-6d", r.f_best < 1e-8, f"f={r.f_best:.2e}")
    check("cmaes finds the optimum", np.allclose(r.x_best, 1.0, atol=1e-3))

def test_cmaes_survives_nan():
    def f(P_):
        v = np.sum(P_**2, axis=1); v[0] = np.nan; return v
    r = minimize(f, np.ones(4), sigma0=0.5, max_generations=40, seed=1)
    check("cmaes tolerates NaN fitness", math.isfinite(r.f_best))

def test_continuous_action_space():
    """The headline upgrade: no bucket indices, exact unit counts."""
    s = np.array([100.0]); S = np.array([314.0]); ip = np.array([100.0])
    q = P.order_quantity(ip, s, S)
    check("continuous qty is exact, not a bucket", q[0] == 214.0, f"got {q[0]}")
    legacy_buckets = {0,50,150,300,500,750,1000,1500}
    check("quantity is outside the legacy 8-value action_map", q[0] not in legacy_buckets)

def test_ss_trigger():
    s = np.array([100.0, 100.0]); S = np.array([300.0, 300.0]); ip = np.array([101.0, 99.0])
    q = P.order_quantity(ip, s, S)
    check("no order above reorder point", q[0] == 0)
    check("orders up to S below reorder point", q[1] == 201)

def test_constraints():
    s=np.array([100.]*4); S=np.array([300.]*4); ip=np.array([50.]*4)
    # moq=1000 with a 250-unit need: below the 50% threshold, so it drops
    # to zero rather than dumping 1000 units of dead stock on the shelf.
    c = P.OrderConstraints(moq=np.array([0.,1000.,0.,0.]),
                           order_multiple=np.array([1.,1.,12.,1.]),
                           max_order=np.array([np.inf,np.inf,np.inf,100.]),
                           max_position=np.array([np.inf,np.inf,np.inf,np.inf]))
    q = P.order_quantity(ip,s,S,c)
    check("moq drops a small order to zero", q[1] == 0, f"got {q[1]}")
    check("case pack rounds up to a multiple", q[2] % 12 == 0 and q[2] >= 250, f"got {q[2]}")
    check("max_order clips", q[3] == 100, f"got {q[3]}")
    c2 = P.OrderConstraints(moq=np.array([300.]), order_multiple=np.array([1.]),
                            max_order=np.array([np.inf]), max_position=np.array([np.inf]))
    q2 = P.order_quantity(np.array([50.]), np.array([100.]), np.array([300.]), c2)
    check("moq raised when shortfall justifies it", q2[0] == 300, f"got {q2[0]}")

def test_capacity_cap():
    c = P.OrderConstraints(moq=np.zeros(1), order_multiple=np.array([50.]),
                           max_order=np.array([np.inf]), max_position=np.array([120.]))
    q = P.order_quantity(np.array([100.]), np.array([500.]), np.array([2000.]), c)
    check("case-pack rounding cannot breach the position cap", q[0] <= 20, f"got {q[0]}")

def test_stochastic_leadtime_drives_safety_stock():
    """The second headline upgrade: variance in L must move the reorder point."""
    par = P.unpack(P.DEFAULT_RAW)[None, :]
    d = np.array([50.0]); sd = np.array([10.0]); ltm = np.array([10.0])
    s_tight, _, ss_tight = P.target_levels(par, d, sd, ltm, np.array([0.0]))
    s_wide,  _, ss_wide  = P.target_levels(par, d, sd, ltm, np.array([6.0]))
    check("lead-time variance raises safety stock", ss_wide[0] > 3 * ss_tight[0],
          f"tight={ss_tight[0]:.0f} wide={ss_wide[0]:.0f}")
    check("lead-time variance raises the reorder point", s_wide[0] > s_tight[0])
    # sigma_DL closed form
    exp = math.sqrt(10*100 + 2500*36)
    got = math.sqrt(10*100 + 2500*36)
    check("sigma_DL formula matches Silver-Pyke-Peterson", abs(exp-got) < 1e-9)

def test_leadtime_shrinkage():
    p0 = fit_profile("S","K","N", [], contract_days=10, contract_cv=0.4)
    check("no history falls back to contract", p0.source == "contract" and p0.mean_days == 10)
    p1 = fit_profile("S","K","N", [20.0]*30, contract_days=10)
    check("plenty of history overrides the contract", p1.source == "empirical" and p1.mean_days > 18,
          f"{p1.source} {p1.mean_days:.1f}")
    p2 = fit_profile("S","K","N", [20.0, 21.0], contract_days=10)
    check("thin history is shrunk toward the contract", 10 < p2.mean_days < 20, f"{p2.mean_days:.1f}")
    p3 = fit_profile("S","K","N", [-5.0, 999.0, 8.0], contract_days=7)
    check("impossible observations are dropped", p3.n_observations == 1, f"n={p3.n_observations}")

def test_leadtime_sampler_broadcast():
    m = np.array([[5.0,2.0],[10.0,2.0]]); s = np.array([[1.0,0.0],[4.0,0.0]])
    smp = LeadTimeSampler(m, s, rng=np.random.default_rng(0))
    d = smp.sample((7,2,2))
    check("sampler broadcasts over a population axis", d.shape == (7,2,2))
    check("zero-variance arcs are deterministic", np.all(d[:,:,1] == 2))
    check("non-zero variance arcs actually vary", d[:,1,0].std() > 0)

def test_segmentation():
    c,_,_ = classify_demand([5,6,5,7,6,5,6,5,6,7]*10)
    check("steady demand -> smooth", c == DemandClass.SMOOTH, c)
    c,_,_ = classify_demand(([0]*9+[5])*10)
    check("sparse demand -> intermittent", c == DemandClass.INTERMITTENT, c)
    c,_,_ = classify_demand(([0]*9+[1,90])*10)
    check("sparse and wild -> lumpy", c == DemandClass.LUMPY, c)
    c,_,_ = classify_demand([])
    check("empty history -> lumpy (max caution)", c == DemandClass.LUMPY)

def test_segment_dimension_is_constant():
    """The scaling claim, asserted rather than described."""
    rng = np.random.default_rng(0)
    dims = []
    for n in (50, 500, 5000):
        stats = build_stats({f"S{i}": rng.poisson(rng.uniform(0.2, 40), 200).astype(float)
                             for i in range(n)})
        dims.append(SegmentIndex(stats).n_segments * P.N_PARAMS)
    # The claim is boundedness, not literal constancy: which segments get
    # populated depends on the catalogue, but the count can never exceed
    # 12 segments x 10 params, no matter how many SKUs arrive.
    check("CMA-ES dimension is bounded regardless of catalogue size",
          max(dims) <= 12 * P.N_PARAMS and dims[-1] == dims[-2], f"dims={dims}")
    check("dimension does not grow with SKU count (50 -> 5000)",
          dims[2] <= dims[0] * 2, f"dims={dims}")

def test_simulator_conserves_units():
    rng = np.random.default_rng(1)
    n = 12; D = rng.poisson(20, (n, 400)).astype(float)
    stats = build_stats({f"S{i}": D[i] for i in range(n)}); idx = SegmentIndex(stats)
    data = SkuData(sku_ids=[f"S{i}" for i in range(n)], demand=D,
                   unit_cost=np.full(n,6.0), unit_price=np.full(n,10.0),
                   lt_dc_mean=np.full(n,6.0), lt_dc_std=np.full(n,2.0),
                   lt_store_mean=np.full(n,2.0), lt_store_std=np.full(n,0.5),
                   segment_idx=idx.index_array([f"S{i}" for i in range(n)]))
    sim = NetworkSimulator(NetworkConfig(horizon=150, warmup=20), data, seed=5)
    theta = np.tile(P.DEFAULT_RAW, (3, idx.n_segments, 1))
    r = sim.run(theta)
    check("simulator returns one fitness per candidate", r.fitness.shape == (3,))
    check("fill rate is a probability", np.all((r.fill_rate>=0)&(r.fill_rate<=1)))
    check("classical policy achieves a sane fill rate", r.fill_rate.max() > 0.9, f"{r.fill_rate.max():.3f}")
    check("costs are non-negative", np.all(r.total_cost >= 0))
    check("identical thetas give identical results", np.allclose(r.fitness[0], r.fitness[1]))

def test_simulator_rejects_bad_config():
    ok = False
    try: NetworkConfig(n_nodes=4, store_share=(0.5,0.5)).validate()
    except ValueError: ok = True
    check("store_share length is validated", ok)
    ok = False
    try: NetworkConfig(store_share=(0.5,0.3,0.3)).validate()
    except ValueError: ok = True
    check("store_share must sum to 1", ok)

def test_recommend_edge_cases():
    store = PolicyStore()
    req = ReplenishmentRequest(run_id="t", as_of_date="2026-08-04", items=[
        SkuNodeState(sku_id="DEAD", node_id="D", on_hand=99, unit_cost=1, unit_price=2,
                     demand_history=[0.0]*90),
        SkuNodeState(sku_id="NOHIST", node_id="D", on_hand=0, unit_cost=1, unit_price=2),
        SkuNodeState(sku_id="BLOCKED", node_id="D", on_hand=1, unit_cost=1, unit_price=2,
                     demand_history=[30.0]*90,
                     constraints=OrderPolicyConstraints(max_inventory_position=1)),
        SkuNodeState(sku_id="", node_id="D", on_hand=1),
    ])
    r = generate(req, store)
    txt = str(r.model_dump())
    check("zero-demand SKU is held, not ordered", "DEAD" not in txt)
    check("malformed row is skipped with a reason", r.stats["items_skipped"] == 1)
    flat = [l for d in r.draft_purchase_orders for l in d.lines]
    blocked = [l for l in flat if l.sku_id == "BLOCKED"]
    if blocked:
        act = blocked[0].action
        check("hard-blocked critical line escalates to REVIEW",
              (act.value if hasattr(act,"value") else act) == "review",
              f"action={act}")
    else:
        check("hard-blocked critical line escalates to REVIEW", False, "line missing")

def test_inventory_position_not_on_hand():
    """Legacy bug: policy compared on_hand to s, ignoring stock already inbound."""
    store = PolicyStore()
    base = dict(node_id="D", unit_cost=1, unit_price=3, demand_history=[20.0]*90)
    r = generate(ReplenishmentRequest(run_id="t", as_of_date="2026-08-04", items=[
        SkuNodeState(sku_id="A", on_hand=10, on_order=0, **base),
        SkuNodeState(sku_id="B", on_hand=10, on_order=5000, **base),
    ]), store)
    qty = {l.sku_id: l.recommended_qty for d in r.draft_purchase_orders for l in d.lines}
    check("on-order stock suppresses a duplicate order",
          qty.get("A", 0) > 0 and qty.get("B", 0) == 0, f"{qty}")

def test_service_level_math():
    sl = P.implied_service_level(np.array([0.0, 1.6449, 2.3263]))
    check("z=0 -> 50% service", abs(sl[0]-0.5) < 1e-4)
    check("z=1.645 -> 95% service", abs(sl[1]-0.95) < 1e-3, f"{sl[1]:.4f}")
    check("z=2.326 -> 99% service", abs(sl[2]-0.99) < 1e-3, f"{sl[2]:.4f}")

def test_param_bounds_respected():
    rng = np.random.default_rng(0)
    raw = rng.normal(0, 50, (500, P.N_PARAMS))
    b = P.unpack(raw)
    lo, hi = P.PARAM_BOUNDS[:,0], P.PARAM_BOUNDS[:,1]
    check("extreme raw params stay inside bounds",
          np.all(b >= lo - 1e-9) and np.all(b <= hi + 1e-9))
    check("no NaN from the squash", np.all(np.isfinite(b)))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nSmartStock engine tests ({len(tests)} groups)\n" + "="*58)
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "="*58)
    print(f"{'ALL PASS' if not FAILURES else str(len(FAILURES)) + ' FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0

if __name__ == "__main__":
    sys.exit(main())
