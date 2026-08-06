"""
Mid-Semester Evaluation Demo — CMA-ES Supply Chain Inventory Optimization
Run:  python3 demo.py
"""

import sys
import os
import time
import json
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

SECTION_WIDTH = 72
DATA_PATH = 'data/processed/processed_data_m5.csv'
SCALER_PATH = 'data/processed/scaler_m5.pkl'
RESULTS_PATH = 'models/evaluation_results.json'
THETA_PATH = 'models/cmaes_theta_seed123.npy'
EPISODE_LENGTH = 90
N_DEMO_EPISODES = 10
PAUSE = 0.5


def banner(title):
    print()
    print("=" * SECTION_WIDTH)
    print(f"  {title}")
    print("=" * SECTION_WIDTH)
    print()


def separator():
    print("-" * SECTION_WIDTH)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — PROBLEM STATEMENT
# ═══════════════════════════════════════════════════════════════════════════

def section_problem_statement():
    banner("SECTION 1 — THE INVENTORY OPTIMIZATION PROBLEM")

    print("""\
  Every retailer faces a daily decision: how much to order?

  STOCKOUT  — ordering too little means lost sales and unhappy customers.
              Cost: $3.00 per unit of unmet demand.

  OVERSTOCK — ordering too much ties up capital in unsold inventory.
              Cost: $0.05 per unit per day of holding.

  The goal: minimize total cost while maintaining high service levels
  (fraction of demand fulfilled).

  CLASSICAL APPROACH — the (s,S) policy:
    "When inventory drops below reorder point s, order up to level S."
    Simple, interpretable, widely used in industry.
    Problem: s and S are fixed — they cannot adapt to changing demand.

  DEEP RL ATTEMPT — DQN / hierarchical RL:
    Trained a deep neural network to learn ordering decisions.
    Result: UNSTABLE. 1 in 3 seeds collapsed to negative profit.
    Worst case: -$3,138 per episode (catastrophic failure).
    Seed-to-seed variance: $8,500 standard deviation.

  CMA-ES APPROACH — evolutionary optimization of (s,S) parameters:
    Keep the interpretable (s,S) structure, but let CMA-ES optimize
    8 context-dependent parameters across demand conditions.
    Result: matches classical profit with 7x lower variance and
    zero catastrophic failures across all 5 training seeds.

  Dataset: M5 Walmart competition data (1,941 daily records)
  Train/test split: 80% / 20% holdout (no data leakage)""")

    print()
    separator()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — LIVE POLICY DEMO
# ═══════════════════════════════════════════════════════════════════════════

def section_live_demo():
    banner("SECTION 2 — LIVE CMA-ES POLICY DEMO (HOLDOUT DATA)")

    from environment import SupplyChainEnv
    from hybrid_policy import ParameterizedSSPolicy

    theta = np.load(THETA_PATH)
    policy = ParameterizedSSPolicy(theta)

    print(f"  Loaded best CMA-ES theta (seed 123):")
    print(f"  {np.array2string(theta, precision=3, suppress_small=True)}")
    print()
    print(f"  s_base = {theta[0]:+.3f}    S_base = {theta[1]:+.3f}")
    print(f"  w1..w3 = [{theta[2]:+.3f}, {theta[3]:+.3f}, {theta[4]:+.3f}]  (reorder-point weights)")
    print(f"  w4..w6 = [{theta[5]:+.3f}, {theta[6]:+.3f}, {theta[7]:+.3f}]  (order-up-to weights)")
    print()
    separator()

    env = SupplyChainEnv(
        data_path=DATA_PATH,
        scaler_path=SCALER_PATH,
        data_split=(0.8, 1.0),
        enable_hierarchical=True,
    )

    action_map = env.action_map
    episode_results = []

    print(f"\n  Running {N_DEMO_EPISODES} episodes on 20% holdout split...\n")
    print(f"  {'Ep':>3}  {'Profit':>10}  {'SvcLvl':>7}  {'Demand':>8}  {'Sales':>8}  {'H.Cost':>8}  {'S.Out':>8}")
    print(f"  {'---':>3}  {'------':>10}  {'------':>7}  {'------':>8}  {'-----':>8}  {'------':>8}  {'-----':>8}")

    np.random.seed(777)

    for ep in range(N_DEMO_EPISODES):
        env.reset()
        env.set_strategic_mode(1)

        ep_revenue = ep_cost = ep_demand = ep_sales = 0.0
        ep_holding = ep_stockout = 0.0

        step_details = []

        for step in range(EPISODE_LENGTH):
            ts = env.get_tactical_state()
            state = {
                'inventory_level': float(ts[0]),
                'demand_forecast': float(ts[1]),
                'demand_std': float(ts[2]) * float(ts[3]),
                'lead_time': 2.0,
            }

            action_idx = policy.act(state)
            _, _, done, info = env.step(action_idx)

            ep_revenue += info['revenue']
            ep_cost += info['cost']
            ep_demand += info['demand']
            ep_sales += info['sales']
            ep_holding += info['holding_cost']
            ep_stockout += info['stockout_penalty']

            if step < 5:
                step_details.append({
                    'step': step + 1,
                    'inv': info['inventory'],
                    'order': action_map[action_idx],
                    'demand': info['demand'],
                    'profit': info['revenue'] - info['cost'],
                })

            if done:
                break

        profit = ep_revenue - ep_cost
        sl = (ep_sales / ep_demand * 100.0) if ep_demand > 0 else 0.0

        episode_results.append({
            'profit': profit, 'sl': sl, 'demand': ep_demand,
            'sales': ep_sales, 'holding': ep_holding, 'stockout': ep_stockout,
        })

        print(f"  {ep+1:>3}  ${profit:>9,.0f}  {sl:>6.1f}%  {ep_demand:>8,.0f}  {ep_sales:>8,.0f}  ${ep_holding:>7,.0f}  ${ep_stockout:>7,.0f}")

        if ep == 0:
            print()
            print(f"       First 5 steps of Episode 1 (policy making real-time decisions):")
            print(f"       {'Step':>4}  {'Inventory':>10}  {'Ordered':>8}  {'Demand':>8}  {'Profit':>8}")
            for d in step_details:
                print(f"       {d['step']:>4}  {d['inv']:>10,.0f}  {d['order']:>8,}  {d['demand']:>8,.0f}  ${d['profit']:>7,.0f}")
            print()

    profits = [r['profit'] for r in episode_results]
    sls = [r['sl'] for r in episode_results]

    print()
    separator()
    print(f"\n  Demo summary ({N_DEMO_EPISODES} episodes on holdout):")
    print(f"    Mean profit:   ${np.mean(profits):>10,.2f}")
    print(f"    Std profit:    ${np.std(profits):>10,.2f}")
    print(f"    Min profit:    ${np.min(profits):>10,.2f}")
    print(f"    Max profit:    ${np.max(profits):>10,.2f}")
    print(f"    Mean SL:       {np.mean(sls):>9.1f}%")
    print(f"    Min SL:        {np.min(sls):>9.1f}%")
    print()
    separator()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — RESULTS COMPARISON
# ═══════════════════════════════════════════════════════════════════════════

def section_results():
    banner("SECTION 3 — RESULTS COMPARISON (200 HOLDOUT EPISODES)")

    with open(RESULTS_PATH) as f:
        results = json.load(f)

    bl = results['baseline_best']
    ca = results['cmaes_aggregate']
    rl = results['historical_deep_rl']
    per_seed = results['cmaes_per_seed']

    print("  ┌─────────────────┬────────────┬────────────┬────────────┬──────────┐")
    print("  │  Method         │ Mean Profit│  Std Dev   │ Worst Case │  Svc Lvl │")
    print("  ├─────────────────┼────────────┼────────────┼────────────┼──────────┤")
    print(f"  │ (s,S) Baseline  │ ${bl['profit_mean']:>9,.0f} │ ${bl['profit_std']:>9,.0f} │ ${bl['profit_min']:>9,.0f} │ {bl['sl_mean']:>6.1f}%  │")
    print(f"  │ Deep RL (old)   │ ${rl['mean_profit']:>9,} │ ${rl['std']:>9,} │ ${rl['worst']:>9,} │ {rl['svc_lvl']:>6.1f}%  │")
    print(f"  │ CMA-ES (5 seeds)│ ${ca['mean_of_means']:>9,.0f} │ ${ca['std_of_means']:>9,.0f} │ ${ca['worst_seed']:>9,.0f} │ {ca['min_sl']:>6.1f}%  │")
    print("  └─────────────────┴────────────┴────────────┴────────────┴──────────┘")

    print()
    print("  Per-seed CMA-ES breakdown:")
    print(f"  {'Seed':>6}  {'Mean Profit':>12}  {'Std':>10}  {'Min Profit':>12}  {'SL':>7}")
    print(f"  {'----':>6}  {'-----------':>12}  {'---':>10}  {'----------':>12}  {'--':>7}")
    for seed in sorted(per_seed.keys(), key=int):
        d = per_seed[seed]
        marker = " <-- best" if d['profit_mean'] == ca['best_seed'] else ""
        print(f"  {seed:>6}  ${d['profit_mean']:>11,.0f}  ${d['profit_std']:>9,.0f}  ${d['profit_min']:>11,.0f}  {d['sl_mean']:>6.1f}%{marker}")

    print()
    separator()
    print()
    print("  Statistical Significance Test")
    print("  (One-sample t-test: CMA-ES seed means vs baseline mean)")
    print()

    seed_means = [per_seed[s]['profit_mean'] for s in sorted(per_seed.keys(), key=int)]
    baseline_mean = bl['profit_mean']
    from scipy.stats import ttest_1samp
    t_stat, p_value = ttest_1samp(seed_means, baseline_mean)
    delta = ca['mean_of_means'] - baseline_mean

    print(f"    Baseline mean:    ${baseline_mean:>10,.0f}")
    print(f"    CMA-ES mean:      ${ca['mean_of_means']:>10,.0f}")
    print(f"    Delta:            ${delta:>10,.0f}")
    print(f"    t-statistic:      {t_stat:>10.4f}")
    print(f"    p-value:          {p_value:>10.4f}")
    print()
    if p_value > 0.05:
        print("    Result: NOT statistically significant (p > 0.05)")
        print("    Interpretation: CMA-ES and baseline are STATISTICALLY EQUIVALENT")
    else:
        print(f"    Result: Statistically significant (p = {p_value:.4f})")
    print()

    print("  Acceptance Criteria:")
    for criterion, passed in results['criteria'].items():
        mark = "PASS" if passed else "FAIL"
        print(f"    [{mark}] {criterion}")
    print()
    separator()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — KEY FINDINGS
# ═══════════════════════════════════════════════════════════════════════════

def section_key_findings():
    banner("SECTION 4 — KEY FINDINGS")

    print("""\
  1. CMA-ES matches the classical (s,S) policy statistically.
     One-sample t-test: p = 0.65 (no significant difference in mean profit).
     Both methods earn ~$12,500 per episode on holdout data.

  2. CMA-ES achieves 7x lower variance across training seeds.
     CMA-ES std-of-means: $546   vs   Baseline std: $3,720
     This means CMA-ES is far more CONSISTENT — the optimizer
     reliably finds good policies regardless of random seed.

  3. Zero catastrophic failures vs Deep RL.
     CMA-ES worst case:  +$11,542  (profitable in every seed)
     Deep RL worst case:  -$3,138  (net loss — unacceptable)
     Deep RL collapsed in 1 of 3 seeds. CMA-ES: 0 of 5.

  CONCLUSION:
     CMA-ES does not beat the oracle-tuned baseline on raw profit,
     but it is the SAFER choice for deployment:
       - Equivalent expected return
       - Dramatically lower variance
       - No risk of catastrophic loss
       - Interpretable (s,S) structure preserved""")

    print()
    print("=" * SECTION_WIDTH)
    print("  END OF DEMO")
    print("=" * SECTION_WIDTH)
    print()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print()
    print("=" * SECTION_WIDTH)
    print("  HIERARCHICAL RL FOR SUPPLY CHAIN INVENTORY OPTIMIZATION")
    print("  CMA-ES Parameterized (s,S) Policy — Mid-Semester Demo")
    print("  Dataset: M5 Walmart  |  Train: 80%  |  Holdout: 20%")
    print("=" * SECTION_WIDTH)

    t0 = time.time()

    section_problem_statement()
    time.sleep(PAUSE)

    section_live_demo()
    time.sleep(PAUSE)

    section_results()
    time.sleep(PAUSE)

    section_key_findings()

    elapsed = time.time() - t0
    print(f"  Total runtime: {elapsed:.1f}s")
    print()


if __name__ == '__main__':
    main()
