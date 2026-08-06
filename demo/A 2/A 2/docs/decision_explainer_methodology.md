# Decision Explainer Methodology
**Component B — PRD 2 (Abhishek's Pillar)**

---

## 1. Purpose

Every inventory ordering recommendation from any policy in this project now comes with a human-readable explanation: which features drove this decision, by how much, and what would have happened under different conditions. This addresses the "black box" critique raised by Rudin (2019): models that cannot explain themselves cannot be trusted in high-stakes operational contexts.

The explainer is deliberately policy-agnostic. It works on any callable that maps a state dictionary to an order quantity — classical (s,S), grid-tuned, CMA-ES single-SKU, CMA-ES multi-SKU, or any future policy. No modification to the policy internals is required.

---

## 2. Method: Perturbation-Based Sensitivity

### 2.1 Why perturbation, not SHAP

SHAP (Lundberg & Lee, 2017) provides the theoretically grounded Shapley decomposition of feature importance. For complex models with many interactions, SHAP's guarantees (efficiency, symmetry, dummy, linearity) are worth the computational overhead. For the (s,S) parameterized policy in this project, which has 4 input features and a deterministic closed-form, perturbation is:

- Faster (no repeated model evaluations across exponential coalitions)
- More transparent (the sensitivity formula is directly interpretable)
- Sufficient (Shapley additivity is not needed when interactions are few and policy structure is known)

This is the recommendation-in-practice of Rudin (2019): interpretable-by-design models need no post-hoc explanation.

### 2.2 Perturbation protocol

For each input feature, the explainer applies four perturbation levels: −20%, −10%, +10%, +20%. Each perturbation is applied to one feature at a time while all other features are held at their original values (a one-at-a-time / OAT design).

For each perturbation level δ:

```
sensitivity(feature, δ) = (new_decision - original_decision) / (|δ| × |original_value| + ε)
```

The denominator normalises by both perturbation magnitude and feature scale, making sensitivities comparable across features at different scales. ε = 1e-9 prevents division by zero when the feature value is exactly zero.

### 2.3 Clamping

Perturbations are clamped to plausible ranges before being passed to the policy:

| Feature | Lower | Upper | Reason |
|---|---|---|---|
| inventory_level | 0.0 | 3.0 | Normalised scale; inventory cannot be negative |
| demand_forecast | 0.0 | 2.0 | Normalised scale; demand cannot be negative |
| demand_std | 0.0 | 2.0 | Variance cannot be negative |
| lead_time | 1.0 | 10.0 | Lead time always ≥ 1 day; >10 is operationally implausible |

Without clamping, a −50% perturbation on a small inventory value could produce a negative inventory, which no real-world system would encounter. Clamping ensures counterfactuals remain within the operational envelope. The cost is that a clamped perturbation is effectively smaller than requested — the sensitivity formula handles this by using the actual post-clamp delta implicitly in the counterfactual statements.

### 2.4 Aggregation

The per-level sensitivities are aggregated across all four perturbation levels by taking the mean absolute value:

```
agg_sensitivity(feature) = mean(|sensitivity(feature, δ)| for δ in levels)
```

Mean absolute sensitivity rewards features that change the decision consistently in either direction. A feature that causes +100 at +20% and −100 at −20% is correctly identified as highly sensitive, whereas a feature that causes +100 at +20% and −100 at −20% but 0 at ±10% would still show a meaningful mean.

### 2.5 Ranking and top factors

Features are ranked by descending mean absolute sensitivity. The top 5 are reported. Each factor includes:

- **sensitivity**: the aggregated value (higher = stronger driver)
- **share**: sensitivity / sum of all sensitivities (useful for bar chart visualisation)
- **direction**: 'positive' if higher feature value → higher order on average; 'negative' if inverse
- **description**: a human-readable sentence combining feature name, current value, and directional effect

### 2.6 Counterfactual statements

Following Wachter et al. (2017), counterfactuals answer "what would have happened if the world were slightly different?" For each of the top 5 features, two counterfactuals are attempted: +20% and −20% perturbation. The first 5 non-trivial counterfactuals (those where the post-clamp new value differs from the original) are reported as complete sentences:

```
"If [feature label] were [pct]% [higher/lower] ([original] -> [new]), 
 recommend [N] units instead of [M]."
```

This format follows the Wachter et al. counterfactual template directly: minimal perturbation, concrete alternative outcome, no causal claim beyond the local approximation.

---

## 3. State Features

The explainer operates on the canonical 4-feature state used by ParameterizedSSPolicy:

| Feature | Units | Normalisation |
|---|---|---|
| inventory_level | normalized | current_inventory / 1500, clipped [0, 3] |
| demand_forecast | normalized | rolling_mean_demand / 300, clipped [0, 2] |
| demand_std | normalized | (rolling_mean / 300) × coefficient_of_variation, clipped [0, 2] |
| lead_time | days (raw) | no normalisation; 2.0 for warehouse→store, 5.0 for supplier→warehouse |

These features are constructed from the `Node.store_state()` method in `multi_echelon.py` and passed directly to `ParameterizedSSPolicy.act()`. The explainer does not re-engineer these features; it explains decisions in the same feature space the policy uses.

---

## 4. Policy-Agnostic Design

The explainer receives any `policy_fn: Callable[[Dict], float]`. The `make_policy_fn()` factory converts any saved theta file to a callable, handling all theta formats (8-vec, 10-vec, 24-vec, 26-vec) transparently. Tested types:

- `default`: ParameterizedSSPolicy with DEFAULT_THETA (context-insensitive baseline)
- `cmaes_seed*`: Phase 1 single-store CMA-ES thetas
- `uniform_robust`: Phase 2.8 uniform multi-echelon theta
- `pernode_robust`: Phase 2.8 per-node multi-echelon theta (production-selected — satisfies per-store SL floors; Uniform CMA-ES has higher mean profit at -$2,335 vs -$6,083 but abandons store C at 61.8% SL)
- `grid_tuned`: Phase 4.5 grid-tuned classical baseline

For the Phase 7 multi-SKU theta, `make_policy_fn('custom', theta=theta26_block)` can be called with the per-SKU 26-vec block extracted from the multi-SKU theta array.

---

## 5. Sample Explanations

`models/sample_explanations_pernode_storeA.json` contains 12 explanations for the Phase 2.8 per-node robust policy at Store A, covering a range of inventory levels and demand conditions from the held-out test split (days 1553–1940). `models/sample_explanations_cmaes_storeA.json` contains 5 explanations for the Phase 1 CMA-ES seed-42 policy.

Across the pernode_robust sample:
- Lead time is the most sensitive feature for Store A (sensitivity ~93.75 in states where the policy is near its ordering threshold), because the Phase 2.8 theta learned a strong lead-time coefficient (θ_w3 = 4.94) for Store A's s-threshold computation.
- In states with very low inventory (inventory_level < 0.1), inventory becomes the primary driver.
- Demand forecast sensitivity is lower because the per-node training used a MAE-robust objective that partially decouples ordering from short-term demand fluctuations.

---

## 6. Limitations

**Discrete action space.** The policy outputs one of eight discrete order quantities: {0, 50, 150, 300, 500, 750, 1000, 1500} units. Perturbation-based sensitivity is computed over these discrete steps, which means small perturbations may show zero sensitivity until the threshold is crossed. The ±10%/±20% grid mitigates this but does not eliminate it. For very flat or very steep threshold regions, the sensitivity estimate may miss the true inflection point.

**Local approximation only.** Perturbation-based explanations are local (valid near the query state). A feature ranked as low-sensitivity at current inventory may become high-sensitivity at much lower inventory. For understanding global policy behavior, the segmentation analysis in `analyze_segmentation.py` is more appropriate.

**Correlations not captured.** OAT perturbation does not capture interaction effects. If inventory and demand_forecast are jointly correlated in real operation (high demand days coincide with low inventory after stockouts), OAT would not reveal this. A full factorial perturbation grid would, but at 4^4 = 256 evaluations per decision vs. 4 × 4 = 16 here.

---

## 7. References

Rudin, C. (2019). Stop explaining black box machine learning models for high stakes decisions and use interpretable models instead. *Nature Machine Intelligence*, 1(5), 206–215.

Wachter, S., Mittelstadt, B., & Russell, C. (2017). Counterfactual explanations without opening the black box: Automated decisions and the GDPR. *Harvard Journal of Law & Technology*, 31(2).

Lundberg, S. M., & Lee, S.-I. (2017). A unified approach to interpreting model predictions. *Advances in Neural Information Processing Systems*, 30.
