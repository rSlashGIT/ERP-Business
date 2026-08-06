# Hack Detector Methodology
**Component A — PRD 2 (Abhishek's Pillar)**

---

## 1. Purpose

Reward hacking occurs when a policy satisfies its training objective through unintended means — scoring high on the metric without achieving the underlying goal. Skalse et al. (2022) define it formally as a policy that achieves high proxy reward while violating the spirit of the true objective. In a supply chain context, an optimizer can exploit feasibility constraints by abandoning a low-margin store (improving average service level at the cost of a specific node), or by over-performing on the validation split in ways that do not generalize.

This detector automates four checks against saved policy theta files, producing a structured audit report without re-training or modifying any existing results.

---

## 2. Four Checks

### Check 1 — Store Abandonment

**What it detects.** A policy that implicitly ignores a store by never ordering for it, allowing service level to collapse.

**Implementation.** Compute per-store service level on the held-out test split. Flag any store where SL < 60% AND that store's demand share ≥ 15% of total demand.

**Thresholds.** SL floor 60% and demand share 15% are Phase 2.5 project standards. The 60% floor is a project minimum, not a target — the trained SL target is 80% per node. Any policy that cannot meet 60% on a substantial node (≥15% share) is considered abandoning that node. Thresholds were not adjusted post-hoc.

**Known finding.** The Phase 2.5 mean-SL theta (`network_best_theta.npy`) shows Store C at 22.8% SL with 20% demand share. This was discovered during Phase 2.5 analysis and is the motivating case for this check. The per-node constraint introduced in Phase 2.6 fixed it.

**Severity.** High — store abandonment directly harms end customers and invalidates any profit metric computed at the network level.

---

### Check 2 — Constraint Migration

**What it detects.** A policy that satisfied the SL floor during validation but fails to hold it on the test split. This is a generalization-failure pattern: the optimizer sat on the feasibility boundary in training and that boundary did not transfer.

**Implementation.** Compute per-store SL on both validation and test splits. Flag if every store passes the 80% floor on validation AND at least one store falls below 80% on test.

**Threshold.** 80% SL floor is the per-node constraint used in Phase 2.6 and Phase 2.8 training. Using the same floor avoids comparing against a different standard.

**Known finding.** `uniform_best_theta_robust.npy` shows Store C at 91.7% SL on validation but 61.8% on test. The constraint migrated — the policy learned to satisfy the SL constraint within the validation distribution but generalized poorly. This is flagged as severity medium.

**Severity.** Medium — the policy is not outright degenerate, but the constraint does not hold out-of-sample.

---

### Check 3 — Generalization Gap

**What it detects.** A policy that achieved meaningfully higher profit on validation than on test, indicating training-specific memorization.

**Implementation.** Compute:

```
gap_ratio = (val_profit - test_profit) / |val_profit|
```

Flag if gap_ratio > 5%. A positive ratio means the policy looked better on validation than test. A negative ratio (test > val) is not flagged.

**Threshold.** 5% was chosen as the minimum economically meaningful gap at the episode profit scale of this project. At mean test profit of −$2,335 (uniform robust), a 5% gap corresponds to $116 per 90-day episode — a detectable signal above simulation noise. The threshold is conservative: it catches clear overfitting, not noise.

**Severity.** Medium — a gap this size suggests the chosen validation set may not be representative, warranting additional held-out evaluation.

---

### Check 4 — Degenerate Policy

**What it detects.** A policy that literally never orders for a non-trivial demand node across all evaluation episodes.

**Implementation.** Compute mean order quantity per store across all held-out episodes. Flag any store with mean order quantity ≤ 0 AND demand share ≥ 15%.

**Rationale.** A policy that places zero orders for a store, regardless of inventory, has degenerated to a pathological fixed-point. This typically happens when the optimizer found that not ordering reduces holding costs in the objective without sufficiently penalizing stockouts — a reward-shaping failure. Unlike Check 1 (which catches behavioral outcomes), Check 4 catches behavioral inputs, enabling earlier diagnosis.

**Known finding.** `network_best_theta.npy` (segmented mean-SL) has Store C with order_frequency = 0.0 and mean_order_qty = 0.0 across all 16 held-out episodes. Triggered jointly with Check 1.

**Severity.** High — a policy that never orders is not a viable inventory policy.

---

## 3. Evaluation Protocol

**Data.** M5 Walmart FOODS_3_090 @ CA_1, 1,941 days. Train split: 80% (days 0–1552). Held-out test: 20% (days 1553–1940).

**Held-out evaluation.** 16 matched episodes × 90 days, seeded with `HELDOUT_RNG_SEED = 20260419`. Episodes start at random positions within the held-out window; the same seed produces the same episode set for every policy, ensuring fair comparison.

**Theta normalization.** All saved thetas are normalized to 26-vec format via `_to_theta26()` from `analyze_segmentation.py` before evaluation. This handles legacy 8-vec, 10-vec, 24-vec, and 26-vec formats transparently.

**Data priority.** The detector uses existing evaluation JSON files when available (avoiding redundant computation), then falls back to entries in `segmentation_analysis.json`, then to fresh simulation. Val/test split data is only available from the robust-format evaluation JSONs produced by Phase 2.8.

---

## 4. Audit Results Summary

From `models/hack_detection_report.json` (11 policies audited):

| Policy | Check 1 | Check 2 | Check 3 | Check 4 | Max Severity |
|---|---|---|---|---|---|
| network_best_theta_pernode_robust | False | False | False | False | none |
| uniform_best_theta_robust | False | **True** | False | False | medium |
| network_best_theta (legacy) | **True** | — | — | **True** | high |
| network_best_theta_pernode | False | False | — | False | none |
| uniform_best_theta (initial) | False | — | — | False | none |
| cmaes_theta_seed42 | False | — | — | False | none |
| cmaes_theta_seed456 | **True** | — | — | **True** | high |
| cmaes_theta_seed789 | **True** | — | — | False | high |
| cmaes_theta_seed123/999 | False | — | — | False | none |
| grid_tuned_classical | False | — | — | False | none |

(— = check not applicable; val/test split not available for this policy format.)

The Phase 2.8 per-node robust theta is clean on all checks. The legacy segmented mean-SL theta flags both abandonment and degenerate ordering for Store C, confirming the Phase 2.5 finding. The uniform robust theta flags constraint migration, consistent with the SL warning in its evaluation JSON.

---

## 5. Limitations

**Single series.** Results are for FOODS_3_090 @ CA_1 only. Thresholds calibrated for this demand scale may not generalize to other SKUs or stores.

**Simulation noise.** Held-out evaluations use 8–16 episodes; per-store SL estimates carry noise on the order of ±3–5%. The 60% SL threshold has enough margin to avoid false positives from noise. The 5% generalization gap threshold may be marginal at very small profit scales.

**No val/test split for legacy thetas.** CMA-ES single-store thetas (Phase 1) and older multi-echelon thetas lack explicit validation and test profit splits. Checks 2 and 3 are skipped for these policies.

**Multi-SKU thetas not included.** Phase 7 multi-SKU thetas operate on a different 30-SKU simulator. Check 1 (per-store SL) is in principle applicable but would require a 30 × 3 SL grid. These thetas are excluded from the current audit and are known to produce a negative reward-hacking signal ($−35,233 delta from Phase 7 report).

---

## 6. References

Skalse, J., Howe, N., Krasheninnikov, D., & Krueger, D. (2022). Defining and characterizing reward hacking. *Advances in Neural Information Processing Systems*, 35, 9460–9471.

Weng, L. (2024). Reward hacking in reinforcement learning. *Lil'Log*. https://lilianweng.github.io/posts/2024-11-28-reward-hacking/

Shihab, M. B., et al. (2025). Detecting and mitigating reward hacking. *arXiv preprint arXiv:2507.05619*.
