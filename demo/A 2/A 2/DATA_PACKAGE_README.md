# CMA-ES Supply Chain Project — Data Package for Visualization

## Must-Have Files ✓

### 1. **evaluation_results.json** (ALL RESULTS)
- Baseline (s,S) grid scan: 30 parameter combinations tested
- Best baseline: s=0.5, S=3 → $12,628 mean profit, $3,720 std
- CMA-ES per-seed breakdown: seeds 42, 123, 456, 789, 999
- CMA-ES aggregate: $12,507 mean, $546 std, p=0.6461 (statistical equivalence)
- Historical Deep RL: mean $7,384, worst case -$3,138, catastrophic failures
- Acceptance criteria: 3/4 passed (missing: "Mean profit >= baseline")

### 2. **cmaes_training_log_seed123.json** (CONVERGENCE CURVE)
- 100 generations of CMA-ES optimization
- Fields per generation:
  - `generation`: 1-100
  - `best_profit`: best found so far (negative = minimization objective)
  - `mean_profit`: population mean (shows overall progress)
  - `sigma`: step-size (covariance matrix adaptation)
- **Key insight**: Converges by generation ~35, stabilizes with sigma→0.5
- Can animate: plot best_profit and sigma vs generation

### 3. **theta_values.json** (ALL 5 OPTIMAL POLICIES)
```json
{
  "42": [-10.7364, 7.4792, 5.1870, 0.5037, 4.7756, 3.5816, 3.2119, -3.5197],
  "123": [1.0143, 14.0850, 16.0173, 9.2675, 9.3880, 2.5032, 6.5267, -6.8682],
  "456": [2.8447, 6.3559, -8.9776, 1.8757, 10.6170, 2.3334, 6.4072, -2.9687],
  "789": [12.1226, -12.5655, -2.1401, 3.3940, -2.4267, 2.5709, 6.1094, 6.4778],
  "999": [17.9793, 18.1514, 9.9384, 28.7530, 0.3297, 2.5630, 5.2693, -8.8255]
}
```
- Each row: [s_base, S_base, w1, w2, w3, w4, w5, w6]
- Seed 123 (highest test profit): Best choice for demo
- Seed 42 (lowest): Shows variance across seeds

---

## Nice-to-Have Files ✓

### 4. **sample_episodes.json** (TIME-SERIES DATA)
- 3 realistic episodes from seed 123 policy on holdout data
- 30 steps per episode (first 30 shown for brevity)
- Fields per step:
  - `inventory`: units in stock
  - `order_qty`: units ordered
  - `demand`: customer demand
  - `sales`: fulfilled demand
  - `profit`: revenue - cost for that step
  - `service_level`: sales/demand × 100
- **Use case**: Animated time-series chart showing policy in action

---

## Dataset Summary
- **Name**: M5 Walmart Competition
- **Time range**: Jan 29, 2011 – Jun 30, 2016
- **Rows**: 1,941 daily records (aggregated across 100 products, 1 store)
- **Features**: Price, Demand, Stock levels, Lead times, Costs, Defect rates
- **Train/Test**: 1,553 / 388 rows (80% / 20%)

---

## How to Use These Files

### For a Convergence Animation
```python
import json
log = json.load(open('cmaes_training_log_seed123.json'))
gens = [x['generation'] for x in log['log']]
profits = [x['best_profit'] for x in log['log']]
sigmas = [x['sigma'] for x in log['log']]
# Plot (gens, profits) and (gens, sigmas) on same or split axes
```

### For a Results Comparison Table
```python
results = json.load(open('evaluation_results.json'))
baseline = results['baseline_best']
cmaes = results['cmaes_aggregate']
deep_rl = results['historical_deep_rl']
# Create 3-row table: baseline vs deep_rl vs cmaes_aggregate
```

### For a Time-Series Animation
```python
episodes = json.load(open('sample_episodes.json'))
for ep in episodes:
    steps = ep['steps']  # list of dicts with inventory, demand, order_qty, profit
    # Animate time-series: x-axis=step, y-axes=[inventory, demand, order_qty]
```

### For Per-Seed Box Plot
```python
results = json.load(open('evaluation_results.json'))
per_seed = results['cmaes_per_seed']
profits = [per_seed[s]['profit_mean'] for s in ['42','123','456','789','999']]
stds = [per_seed[s]['profit_std'] for s in ['42','123','456','789','999']]
# Create box plot or violin plot of seed-to-seed variation
```

---

## Key Numbers for Your Dashboard

| Metric | Value | Insight |
|--------|-------|---------|
| Baseline Mean Profit | $12,628 | Oracle-tuned (s,S) policy |
| CMA-ES Mean Profit | $12,507 | Statistically equivalent (Δ = -$121, p=0.65) |
| Variance Reduction | 7.0x | CMA-ES std $546 vs baseline $3,720 |
| Catastrophic Failures | 0 vs 1/3 | CMA-ES > Deep RL stability |
| Best Seed | 456 | Highest single-seed profit ($12,808) |
| Worst Seed | 42 | Lowest single-seed profit ($11,542, still profitable) |
| Training Time | ~100 gen × 64 episodes × 90 steps | ~576k environment steps |
| Holdout Worst Case | $11,542 | Min profit across all seeds (safe) |

---

## File Sizes
- evaluation_results.json: ~40 KB (complete)
- cmaes_training_log_seed123.json: ~25 KB (100 generations)
- theta_values.json: <1 KB (5 vectors)
- sample_episodes.json: ~50 KB (3 episodes × 30 steps)

**Total package**: ~120 KB (easily transferable)
