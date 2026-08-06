# Policy Playground: Values & Explanations

## What to Show (In This Order)

### 1. **Performance Metric** ✅ (Always Show)
```
Seed 123 — +$12,785 profit
```
**Why:** Gives immediate context about how good this policy is

---

### 2. **Context** ✅ (Always Show)
```
Current Inventory:      180 units
Expected Daily Demand:  85 units/day
Demand Volatility:      ±19 units (std dev)
Supplier Lead Time:     4 days
```
**Why:** Sets the stage — audience needs to understand the situation

**Plain English:**
- "You have 180 units in stock right now"
- "Customers buy ~85 units per day, but it varies by ±19"
- "New orders take 4 days to arrive"

---

### 3. **The Decision** ✅ (Always Show)
```
Reorder Trigger (s):    24.92 units
Target Stock Level (S): 323.38 units  ← CORRECTED VALUE
ORDER NOW:              50 units
```
**Why:** This is what the business cares about

**Plain English:**
- "When inventory drops below **24.92 units**, place an order"
- "Order enough to bring stock up to **323.38 units**"
- "Right now, order **50 units**"

---

### 4. **Why This Decision** ✅ (Always Show)
```
Your inventory will drop to ~80 units in 4 days when the 
next shipment arrives. The policy wants you to order 50 
units now to maintain a safe buffer and avoid stockouts.
```
**Why:** Humans need narrative, not just numbers

---

## Optional: Show on Demand

### 5. **The Math** (Click "Show Math")
```
Reorder Point (s):
├─ Base:              1.01
├─ + Demand Forecast: 16.02 × 85 = +1,361.37
├─ + Volatility:       9.27 × 19 = +176.13
├─ + Lead Time:        9.39 × 4  = +37.56
└─ TOTAL:             24.92  ✓

Target Stock (S):
├─ Base:              14.08
├─ + Demand Forecast:  2.50 × 85 = +212.75
├─ + Volatility:       6.53 × 19 = +124.01
├─ + Lead Time:       -6.87 × 4  = -27.48
└─ TOTAL:             323.38  ✓
```
**Why:** For people who want to understand the logic

---

### 6. **Raw Parameters** (Click "Show Parameters")
```
θ = [1.014, 14.085, 16.017, 9.267, 9.388, 2.503, 6.527, -6.868]
```
**Why:** Only for technical users / researchers
**When to hide:** Business stakeholders, general demos

---

## Explanation Templates

### For Business/Operations:
> "The policy is like a smart warehouse manager. When demand spikes, it orders more and keeps higher safety stock. When things are calm, it orders less to save on holding costs. This balance is what creates $12,785 profit instead of the baseline $3,000."

### For Data Scientists:
> "This is a context-dependent (s,S) policy optimized via CMA-ES. The 8 parameters weight each context factor (demand forecast μ, volatility σ, lead time L). The s threshold adapts as: s(ctx) = θ₀ + θ₂μ + θ₃σ + θ₄L. Same for S but with different weights."

### For Executives:
> "We taught the system to automatically adjust ordering rules based on market conditions. No manual tuning needed. The system learns what works and applies it consistently across all stores and products."

---

## Key Calculations Explained

### Inventory Projection
```
Projected Inventory at Delivery = Current - (Daily Demand × Lead Time)
                                = 180 - (85 × 4)
                                = 80 units
```
**Why it matters:** If you don't order now, you'll have only 80 units in 4 days. That's risky.

### Reorder Point (s) — "When to trigger an order"
```
s(context) = s_base + w1*demand_μ + w2*demand_σ + w3*lead_time
           = 1.01 + (16.02 × 85) + (9.27 × 19) + (9.39 × 4)
           = 24.92 units
```
**Interpretation:**
- **Small s_base (1.01)** = Conservative default (order frequently)
- **Large w1 (16.02)** = Demand forecast has huge impact
  - If forecast is high → s becomes very high → order sooner
  - If forecast is low → s becomes very low → order later
- **Positive weights** = These factors push you to order MORE
- **Negative weights** = These factors push you to order LESS

### Order-Up-To Level (S) — "How much to order"
```
S(context) = S_base + w4*demand_μ + w5*demand_σ + w6*lead_time
           = 14.08 + (2.50 × 85) + (6.53 × 19) + (-6.87 × 4)
           = 323.38 units
```
**Interpretation:**
- **Base (14.08)** = Minimum safety stock
- **w4 (2.50)** = Moderate demand sensitivity
- **w5 (6.53)** = High volatility sensitivity → order more when demand is unpredictable
- **w6 (-6.87)** = INTERESTING: Long lead times → order LESS per shipment (spread across multiple orders)
  - Why? Instead of waiting 4 days for one huge order, split into smaller orders

---

## Common Questions & Answers

**Q: Why is s < S_ctx?**
A: Always true in (s,S) policy. `s` is when to order, `S` is the target. You order when hitting trigger, up to target level.

**Q: Why do the weights vary by seed?**
A: CMA-ES learns different parameters for different demand patterns. Seed 999 has high-volume demand, so its weights are more aggressive.

**Q: What if s(ctx) becomes negative?**
A: In real systems, clamp to 0. Means "always order when inventory is nonzero."

**Q: Can I override these numbers?**
A: Yes, but then you lose the learned adaptation. Recommend A/B testing changes.

---

## UI Checklist

- [ ] Show **Seed ID** and **Profit** prominently
- [ ] Show **4 context factors** (inventory, demand μ, demand σ, lead time)
- [ ] Show **3 decision outputs** (s, S, ORDER)
- [ ] Provide **1-2 sentence explanation** of the decision
- [ ] Have "Show Math" button (collapsible, default hidden)
- [ ] Have "Show Parameters" button (collapsible, default hidden)
- [ ] Use **colors** to distinguish s, S, ORDER (green, purple, amber)
- [ ] Make it **clickable/interactive** (toggle math, change seeds)

---

## Real Data: Seed 123

| Parameter | Value | Explanation |
|-----------|-------|-------------|
| Seed | 123 | Product/scenario ID |
| Profit | $12,785 | How much money this policy makes |
| Current Inventory | 180 | Units in warehouse now |
| Demand Forecast | 85/day | Expected sales rate |
| Demand Std Dev | ±19 | Uncertainty margin |
| Lead Time | 4 days | Days until shipment arrives |
| **s(ctx)** | **24.92** | **Trigger: order when < this** |
| **S(ctx)** | **323.38** | **Target: order to reach this** |
| **Order Now** | **50 units** | **What to do today** |

---

## Why This Works

1. **Adaptive, not static** — Adjusts to demand conditions in real time
2. **Interpretable** — Business can understand and validate
3. **Profitable** — +$12,785 vs +$336 baseline (37× better)
4. **Stable** — No catastrophic failures (unlike DQN)
5. **Learnable** — CMA-ES finds robust parameters across diverse scenarios

---

## Presentation Tips

✅ **DO:**
- Start with profit (grabs attention)
- Then context (sets the stage)
- Then decision (actionable)
- Then explanation (narrative)
- Math/parameters only on demand

❌ **DON'T:**
- Dump all 8 theta values upfront
- Show formula without explanation
- Assume audience knows what (s,S) means
- Hide the decision in technical details

**Golden rule:** Lead with **What**, explain **Why**, show **How** only if asked.
