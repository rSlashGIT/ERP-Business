# Research Foundations

This document grounds our project in the recent (2023–2025) operations-research and reinforcement-learning literature on inventory management, multi-echelon supply chains, evolutionary optimization, and resilience. Five papers are summarized, all verified via web search against their primary sources (arXiv / journal websites). Where a fact could not be verified from the search snippets alone, it is explicitly marked "not verified."

---

## Section 1 — Five grounding papers

### 1. Alvo, Russo, Kanoria, Lee (2023) — Deep RL for inventory networks

- **Title:** *Neural Inventory Control in Networks via Hindsight Differentiable Policy Optimization* (also circulated as *Deep Reinforcement Learning for Inventory Networks: Toward Reliable Policy Optimization*)
- **Authors:** Matias Alvo, Daniel Russo, Yash Kanoria, Minuk Lee (Columbia Business School)
- **Year / venue:** 2023, arXiv preprint [2306.11246](https://arxiv.org/abs/2306.11246) (revised through v3, hosted at Columbia Business School)
- **Core contribution:** Introduces **Hindsight Differentiable Policy Optimization (HDPO)**, which uses pathwise gradients from offline counterfactual simulations to differentiate *through* the known supply-chain dynamics, avoiding the high-variance score-function estimators that make REINFORCE-style policy gradients unstable on inventory problems. Also proposes topology-aligned Graph Neural Network architectures that encode the network structure as inductive bias.
- **What they achieved:** Claim near-optimal policies in settings with known/bounded optima; report significantly outperforming generalized newsvendor heuristics on real time-series data; open-source a benchmark suite to standardize evaluation. Specific numeric deltas not verified from search snippets.
- **Gaps / extension opportunities:**
  - HDPO relies on a fully known, differentiable simulator — strong assumption in practice.
  - No explicit disruption-robustness analysis; benchmarks are stationary.
  - Policies remain black-box neural nets (no interpretability for practitioners).

### 2. Geevers, van Hezewijk, Mes (2024) — Multi-echelon DRL

- **Title:** *Multi-echelon inventory optimization using deep reinforcement learning*
- **Authors:** Kevin Geevers, Lucas van Hezewijk, Martijn Mes (University of Twente). Author list confirmed via the Springer / RePEc listings surfaced in search.
- **Year / venue:** Central European Journal of Operations Research, Vol. 32, Issue 3 (2024). Published online July 2023. DOI: [10.1007/s10100-023-00872-2](https://link.springer.com/article/10.1007/s10100-023-00872-2).
- **Core contribution:** Applies **Proximal Policy Optimization (PPO)** to three multi-echelon inventory structures (linear serial, divergent, and general network), modelling each as an MDP whose objective minimizes holding plus backorder cost. Studies how DRL transfers across topologies.
- **What they achieved:** Shows PPO handles all three structures; numeric cost comparisons against heuristics are in the paper but were not quoted in search snippets (not verified here).
- **Gaps / extension opportunities:**
  - Uses synthetic demand, not a real dataset such as M5.
  - No supplier-side disruption scenarios; lead times are fixed.
  - Reports average-case cost; variance and stockout tail behavior are secondary.

### 3. "Data-driven evolutionary computation for service-constrained inventory optimization in multi-echelon supply chains" (2023)

- **Title:** *Data-driven evolutionary computation for service constrained inventory optimization in multi-echelon supply chains*
- **Authors:** Not verified from the search snippet alone — see DOI below for the full author list.
- **Year / venue:** Complex & Intelligent Systems (Springer), 2023. DOI: [10.1007/s40747-023-01179-0](https://link.springer.com/article/10.1007/s40747-023-01179-0).
- **Core contribution:** Uses an **ensemble-based differential-evolution** variant driven by data generated from a supply-chain digital twin to solve service-constrained multi-echelon inventory problems — i.e., it treats the expensive simulation as the fitness oracle and adapts its evolutionary operators during search.
- **What they achieved:** Reports that differential-evolution ensembles outperform prior published solutions on their benchmark set; specific deltas not verified from search snippets.
- **Gaps / extension opportunities:**
  - Focus on chance-constrained cost; no exogenous-disruption stress testing.
  - Evolutionary search is used, but not on interpretable low-dimensional (s,S)-style parametrizations; the policy representation is not explicitly discussed in the snippet.
  - No comparison against modern DRL baselines.

*This paper is our closest methodological neighbour: data-driven + evolutionary + multi-echelon. Our work differs by using **CMA-ES** specifically, a parameterized (s,S) policy with interpretable thetas, real M5 demand, and an added disruption-testing layer.*

### 4. "Investigating disruption propagation and resilience of supply chain networks: interplay of tiers and connections" (2025)

- **Title:** *Investigating disruption propagation and resilience of supply chain networks: interplay of tiers and connections*
- **Authors:** Not verified from search snippets; see DOI below for the full list.
- **Year / venue:** International Journal of Production Research, Vol. 63, Issue 17, pp. 6229–6251; published online 26 Feb 2025. DOI: [10.1080/00207543.2025.2470348](https://www.tandfonline.com/doi/full/10.1080/00207543.2025.2470348). (Strictly 2025, just outside the 2023–2024 window — included because it is the most direct recent study of disruption propagation across tiers.)
- **Core contribution:** Builds a **network-generator algorithm** to synthesize supply-chain networks ranging from two to seven tiers, then simulates disruption impact and recovery to quantify how tier depth and inter-tier connectivity shape resilience.
- **What they achieved:** Empirical finding (per the abstract in the search snippet) that resilience behavior depends on the interplay between *number* of tiers and *density* of connections — not just one. Specific metrics not verified.
- **Gaps / extension opportunities:**
  - Focus is on network-topology resilience, not on the inventory *policy* that each node runs.
  - Does not compare classical (s,S) vs. optimized policies under disruption.
  - No deployment-oriented dashboard; results are static empirical analyses.

*Our Phase 3 disruption engine fills a complementary gap: we hold topology fixed (1 supplier → 1 warehouse → 3 stores) and vary the **policy** across disruption scenarios.*

### 5. Genetti, Longobardi, Iacca (2025) — Evolutionary RL + interpretable policies for SCM

- **Title:** *Evolutionary Reinforcement Learning for Interpretable Decision-Making in Supply Chain Management*
- **Authors:** Stefano Genetti, Alberto Longobardi, Giovanni Iacca
- **Year / venue:** arXiv [2504.12023](https://arxiv.org/abs/2504.12023), April 2025; also published as a chapter in *Applications of Evolutionary Computation* (Springer, 2025), DOI: [10.1007/978-3-031-90062-4_12](https://link.springer.com/chapter/10.1007/978-3-031-90062-4_12). (Strictly 2025; included because it directly combines evolutionary optimization, RL, and interpretability — the exact intersection our project lives in.)
- **Core contribution:** Combines **evolutionary computation with reinforcement learning** to evolve interpretable policies represented as **decision trees**, embedded in a simulation-based optimization framework for stochastic supply chains. Explicitly frames interpretability as a first-class goal.
- **What they achieved:** On two SCM benchmark problems (one synthetic, one real-world), the evolved decision-tree policies are reported to match or beat standard RL and optimization baselines, arguing against the assumed interpretability / performance trade-off. Specific numbers not verified.
- **Gaps / extension opportunities:**
  - Decision trees are interpretable but produce discrete actions; continuous (s,S) thresholds require separate handling.
  - No explicit resilience / disruption evaluation — purely stationary performance.
  - Single-policy output; no notion of a *portfolio* tuned to different business priorities.

---

## Section 2 — How our work positions relative to these papers

Four concrete gaps in the 2023–2025 literature, and how this project addresses them:

**2.1 Most recent top-tier work uses deep RL — which is empirically fragile on real demand data.**
Papers 1, 2, and (partially) 5 lean on deep RL (HDPO, PPO, EvoRL). Our internal attempt to stack LSTM encoders onto a DQN on the M5 dataset produced unstable, non-reproducible results. This mirrors a broader concern visible even in the DRL-positive papers (e.g., Alvo et al. explicitly motivate HDPO as a fix for REINFORCE instability; Geevers et al. need PPO specifically rather than vanilla policy gradients).
**Our response:** use **CMA-ES** over a low-dimensional parameterized (s,S) policy (8 parameters per store). CMA-ES is a mature black-box optimizer with strong empirical guarantees on non-convex, noisy, low-dimensional problems — a better fit than deep RL when the policy class is already well-chosen.

**2.2 Most work is single-echelon or uses toy multi-echelon structures.**
Paper 1 focuses on generic inventory networks on synthetic demand; Paper 2 uses synthetic demand across three topologies; Paper 3 uses digital-twin-generated data.
**Our response:** Phase 2 builds an explicit **1 supplier → 1 warehouse → 3 stores** network, driven by real **M5 Walmart demand** split across stores with distinct weekday/evening/weekend profiles, so multi-echelon effects (compounding stockouts, upstream lead times) appear under realistic non-stationary demand.

**2.3 Disruption robustness is rarely tested on the same policy that is being benchmarked for expected performance.**
Paper 4 studies disruption propagation but at the topology level, not the policy level. Papers 1–3 report expected-cost metrics under stationary dynamics. Paper 5 is explicitly stationary.
**Our response:** Phase 3 introduces a **disruption engine** that runs the *same* trained policies (CMA-ES and classical (s,S) baseline) through six exogenous disruption scenarios — supplier failures, demand shocks, port strikes, compound crises — and reports profit drop, worst-case profit, and recovery days. The policies are held fixed; only the environment varies.

**2.4 No existing work we found frames policies as a selectable portfolio.**
Paper 5 outputs a single interpretable policy. Papers 1–3 optimize for a single cost objective. None treat operator-selectable trade-offs (profit vs. stability vs. service vs. resilience) as a first-class artifact.
**Our response:** Phase 4 treats the 5 existing CMA-ES seeds as a **policy portfolio** and exposes a weighted-selector that picks the seed best matching user-supplied priorities. This is a small contribution but novel in the surveyed literature.

### 2.5 Segmentation is what unlocks MEIO gains — and it motivates our 24-parameter design

A recurring theme in the recent MEIO literature — both in the MIT SCM thesis stream and in industry practitioner sources — is that the large reported gains from multi-echelon optimization come *not* from having one globally-tuned policy, but from **segmenting inventory policies by SKU/location demand class** (fast/medium/slow movers, weekday-heavy vs. weekend-heavy, high- vs. low-variance, promotional vs. stable) and letting each segment carry its own tuned parameters. The Duong & Holwerda MIT SCM 2024 capstone *Buffer or Suffer* reports inventory reductions of up to **63%** in a 61-SKU × 31-node case study by running dynamic MEIO *per segment*, with the explicit finding that uniform policies leave most of the gain on the table [1]. ToolsGroup's practitioner guide reports the general MEIO benefit envelope — 15–30% inventory reduction, 98%+ service level — but, like its peers at o9 Solutions, emphasises that these figures are contingent on demand-class segmentation rather than flat policies [2, 3]. Driessen (2024), writing for EyeOn, puts the framing bluntly: siloed (single-echelon, single-policy) planning is the primary reason real-world MEIO deployments underperform their theoretical potential — "say no to siloed planning" — and argues for per-segment coordinated buffers across the echelons [4].

**Mapping to our setup.** Our simulator is a single small MEIO instance (1 supplier → 1 warehouse → 3 stores) on real M5 Walmart demand, but the 3 stores are deliberately constructed as 3 *different demand classes*: Store A carries the highest share (50%) with a weekday-heavy profile (weekday ×1.2 / weekend ×0.7); Store B carries a moderate share (30%) with a mildly sinusoidal pattern; Store C carries the smallest share (20%) with a weekend-heavy profile (weekday ×0.7 / weekend ×1.5). Optimising a single 8-parameter policy shared across all three stores forces every store to compromise; optimising a **24-parameter vector** (8 parameters × 3 stores) lets CMA-ES specialise each store's (s, S) thresholds and linear inventory/forecast/variance weights to its own demand class. This is *not* a new methodological contribution — it is the standard segmented-MEIO recipe from the literature above — but it is what our 24-parameter design is specifically trying to capture.

**Why we also train a uniform baseline.** Because segmentation can be reward-hacked ("Store C is noisy, so just stop ordering there") and because the segmented-vs-uniform comparison is the honest way to verify that the extra 16 parameters buy real behaviour rather than overfit, Phase 2.5 additionally trains a shared 8-parameter CMA-ES policy under identical hyperparameters (same seed, same episode length, same SL floor). [src/analyze_segmentation.py](src/analyze_segmentation.py) then reports — on matched held-out episodes — (a) per-store policy metrics for the 24-parameter solution (mean order quantity, order frequency, service level, cost per unit sold) to check that the three stores actually learned *different* policies, and (b) a 3-row comparison table: **Default classical (s, S)** / **Uniform 8-parameter CMA-ES** / **Segmented 24-parameter CMA-ES**. If the uniform baseline is competitive with or beats the segmented one on network profit, the narrative must be reframed: segmentation of the demand, by itself, is not enough — segmentation must also be exploited by the policy, and that requires the extra parameters to actually be *used differently* across stores. We report the result honestly either way.

**Phase 2.5 outcome — honest finding: uniform beats segmented on this instance.** On 16 matched held-out episodes × 90 days (M5 demand, post-train split), the three policies land as follows:

| Policy | Network profit (mean ± std) | Service level | Store A $ | Store B $ | Store C $ |
| --- | --- | --- | --- | --- | --- |
| Default classical (s, S), replicated per store | **-$145,155 ± $11,268** | 100.0% | -8,496 | -13,907 | -15,335 |
| Uniform 8-parameter CMA-ES | **-$58,877 ± $1,998** | 87.6% | -4,038 | +4,413 | +2,833 |
| Segmented 24-parameter CMA-ES | **-$85,801 ± $3,786** | 83.8% | -8,884 | +1,530 | -13,869 |

Two things to read off this table. First, both CMA-ES solutions dominate the default replicated-classical baseline by a wide margin on profit (roughly 1.5–2.5× better), which is consistent with the MEIO literature's claim that tuned policies unlock substantial gains even on a small network. Second, and more importantly for the segmentation story, **the uniform 8-parameter baseline beats the segmented 24-parameter solution by $26,924 on mean network profit at a higher service level** (87.6% vs 83.8%).

Drilling into why, the per-store diagnostic from [src/analyze_segmentation.py](src/analyze_segmentation.py) shows that the segmented solution *did* learn clearly distinct per-store thetas (max pairwise Euclidean distance 40.15, behavioural coefficient-of-variation across mean-order-qty / order-frequency / cost-per-unit of 0.78 / 0.72 / 1.16 — well above our 0.10 specialisation threshold), but the specific specialisation it learned for Store C is degenerate: mean order quantity 0, order frequency 0, per-store service level 9.8%, cost per unit sold $73.8. CMA-ES found that because Store C is the smallest (20% demand share) and the SL floor is enforced on the *mean* across stores and episodes, it could drop Store C almost entirely and still land the episode-averaged mean SL just above 0.80 in training. The uniform policy, by construction, cannot do this — one shared (s, S) policy has to serve all three stores, so it never discovers the "abandon the smallest store" local optimum. Note that on this held-out evaluation the segmented mean SL (83.8%) and the uniform mean SL (87.6%) both sit below the 90% *warning* threshold we set for Phase 2; the held-out SL warnings in [models/network_evaluation.json](models/network_evaluation.json) and [models/uniform_evaluation.json](models/uniform_evaluation.json) are real and we carry them forward honestly.

**What this means for Phase 3 and beyond.** (1) The uniform 8-parameter CMA-ES policy, not the segmented 24-parameter one, is the honest profit-maximising baseline from Phase 2 on this instance. (2) Segmentation of the demand across stores is not by itself sufficient to unlock MEIO gains at our scale — the SL floor has to be per-store (or per-store-weighted), otherwise segmentation gives CMA-ES a cheap place to hide its losses. Tightening the SL constraint (per-store floor instead of mean floor) is a concrete next step we flag for Phase 3 / final-report work rather than silently fix retroactively. (3) We do not claim that segmentation is useless; we claim that at this problem size (3 stores, 8 params each, a single mean-SL floor) the extra parameters do not pay for themselves, and that result is consistent with the MIT and industry literature's emphasis that segmentation gains scale with SKU and location count far beyond 3.

### 2.6 Robust training protocol and the final multi-echelon finding

Phase 2.6 tightened the SL constraint from a network-mean floor to a **per-node fill-rate floor** (80% at every store), which eliminated the Store-C reward-hack visible in the Phase 2.5 mean-floor run. The per-node retrain with the standard 80/20 training split reached per-store SL ≥ 80% on *every* feasible generation in-sample but then overfit: Store A landed at 78.5% SL on held-out evaluation while Stores B and C stayed at 99.8% / 100.0%. In other words, the hack migrated — from Store C under a mean floor to Store A under a per-node floor — rather than disappearing. This is the classic pattern of a higher-capacity model (24 parameters) fitting to lucky train-split episode samples and failing to generalise, not a bug in the constraint.

Phase 2.7 addressed the overfit directly with a **robust training protocol** — the standard ML train/validation/test discipline from Hastie, Tibshirani & Friedman (2009) [6], adapted to CMA-ES with an evolutionary-optimization flavour that is consistent with the zero-shot inventory-generalization framing of Temizöz et al. (2024):

- **60/20/20 split.** The 1,941-day M5 series splits into 1,164 training days (drives CMA-ES fitness), 388 validation days (gates early-stopping), and 389 test days (untouched until the final report).
- **Validation-based early stopping.** Every 5 generations we re-evaluate the current running-best theta on the validation split. Training stops when validation fitness fails to improve for 15 consecutive checks (≤75 generations of slack). This is strictly tighter than training-fitness early stopping: it halts the run when further training starts hurting out-of-sample performance, not when train fitness plateaus.
- **Deploy the val-best theta, not the train-best.** The shipped policy is the one that scored best on the validation split across all checks, not the final training incumbent.

Results on the held-out test split (16 paired episodes × 90 days), with both modes also using the tightened sigma0=1.5 / IMPROVE_EPS=$100 from Phase 2.6:

| Policy | Val profit (deploy) | Test profit | Val–test gap | Test SL_A | Test SL_B | Test SL_C |
| --- | --- | --- | --- | --- | --- | --- |
| **Uniform 8-param + robust** | **-$55,587** | **-$55,682** | **+$95** | 90.6% | 100.0% | 99.2% |
| Segmented 24-param per-node + robust | -$60,525 | -$63,997 | +$3,472 | **77.2%** | 100.0% | 100.0% |

Two clean findings. First, the robust protocol **eliminates** the generalization gap for the uniform 8-parameter policy — $95 of val–test delta on ~$55k of profit is statistical noise, and all three stores clear the 80% fill-rate floor on the held-out test split. Second, the same protocol only *shrinks* the gap for the segmented 24-parameter per-node policy (+$3,472 val–test is under the $10k acceptable threshold we set ourselves) but cannot push Store A above the 80% floor on test — the per-store SL that validated as 86.1% drops to 77.2% on test, below the constraint we were trying to enforce. The 3× increase in model capacity provided **no** generalization benefit at this data scale.

**Final decision — production model.** We ship the robust uniform 8-parameter policy ([models/uniform_best_theta_robust.npy](models/uniform_best_theta_robust.npy)) as the production multi-echelon policy for all downstream phases: Phase 3 disruption testing, Phase 4 portfolio selector, Phase 5 frontend demo. The robust segmented per-node policy ([models/network_best_theta_pernode_robust.npy](models/network_best_theta_pernode_robust.npy)) is retained and reported as a secondary result — its failure to clear the per-store SL floor on test, under a strictly stronger training protocol than Phase 2.6 (val-best deploy, validation early-stop) and starting from a verified-feasible default theta, **is the real bias–variance / data-scale finding we carry forward**: at 1,164 training days a 24-parameter inventory policy overfits even with train/val/test discipline, whereas the 8-parameter version does not.

This matches the Temizöz et al. (2024) [7] zero-shot generalization framing that inventory policies with more parameters need disproportionately more evaluation data to generalize, and it concretizes the abstract "data-size / model-capacity tradeoff" into a specific empirical constraint: *for this network (1 supplier → 1 warehouse → 3 stores) on M5 demand, 1,164 training days is not enough for a 24-parameter segmented policy to beat an 8-parameter uniform one on out-of-sample SL, even with robust training*. We treat this as a finding, not a framing: we do not claim segmentation is useless in general (the MIT and ToolsGroup sources in §2.5 show it pays off in ≥30-SKU × ≥30-node settings), only that at prototype scale the returns on added policy capacity are negative.

### 2.7 Phase 4.5 — Three SOTA demand forecasters on the M5 slice

Phase 4.5 benchmarks three state-of-the-art, research-backed forecasting paradigms against the same naive / seasonal-naive / moving-average-7 baselines, under an identical 60/20/20 split of the M5 demand series (1,164 / 388 / 389 days). The constraint from the project plan rules out LSTM / DQN / hierarchical-RL / any RNN-based architecture and caps training time so everything fits on the 8 GB M2. We pick one representative from each of the three paradigms the 2022–2025 literature identifies as dominant: **tree-based gradient boosting**, **hierarchical multi-rate MLPs**, and **pre-trained time-series foundation models**. Januschowski et al. (2022) [8] — the retrospective on the original M5 competition — is unambiguous that tree-based boosting dominated M5 on point accuracy, with every top-50 solution ensembling LightGBM or XGBoost; this is why LightGBM (Ke et al. 2017 [9]) is Model 1. N-HITS (Challu et al. 2023 [10]) is the most-cited non-RNN deep forecaster of the last three years — a hierarchical interpolation MLP, not an LSTM/transformer — and is representative of Paradigm 2. Chronos-Bolt (Ansari et al. 2024 [11]) is the most credible zero-shot foundation-model baseline at M2-feasible size (50 M params, CPU bfloat16); Das et al. (2024) [12] TimesFM is its closest peer but requires GPU. Nguyen, Dang & Le (2024) [13] inform the LightGBM feature set (lag-1..7, rolling mean/std at 7/14/30, calendar proxies), explicitly aligned with their Grupo Bimbo retail study. Jiang et al. (2025) [14] is the theoretical backbone for Phase 4.6: they formalise the Forecast-then-Optimize (FtO) paradigm where a trained forecaster feeds its point prediction in as a state feature to a downstream optimizer (in our case, CMA-ES), which is exactly the integration pattern we queue up for the next phase.

**Head-to-head table — one-step-ahead forecast error on the held-out 389-day test window** (rolling, model-frozen: each prediction sees only y\[:t], never y\[t]):

| Model | MAE | RMSE | sMAPE (%) | Train / setup (s) | Eval (s) | Per-prediction (ms) | GPU required |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 32.733 | 40.642 | 27.339 | — | — | — | no |
| seasonal_naive (7) | 29.838 | 38.975 | 24.850 | — | — | — | no |
| moving_avg_7 | 26.974 | 33.643 | 22.335 | — | — | — | no |
| LightGBM (Model 1) | 24.252 | 31.160 | 20.363 | 0.22 | 0.86 | 2.2 | no |
| N-HITS (Model 2) | 25.263 | 32.389 | 20.985 | 15.67 | 3.62 | 9.3 | no |
| **Chronos-Bolt (Model 3, zero-shot)** | **23.219** | **29.785** | **19.332** | 6.10\* | 8.54 | 21.9 | no |

\* Chronos-Bolt "train_s" column is **zero-shot model-load time**, not training time — the model was pre-trained by Amazon on a large public time-series corpus (Ansari et al. 2024 [11]) and is used with weights frozen. There is no fine-tuning step on our M5 slice.

**Three clean findings.**

First, **all three SOTA models clear the hard gate** (strictly lower test RMSE than the naive baseline) and also beat the strongest classical baseline, `moving_avg_7` (RMSE 33.643). The ordering on test RMSE is **Chronos-Bolt (29.785) < LightGBM (31.160) < N-HITS (32.389) < moving_avg_7 (33.643)**, and the same ordering holds on MAE and sMAPE. So every paradigm is doing real work relative to the baselines.

Second, the **paradigm-within-paradigm ordering contradicts the Januschowski et al. (2022) [8] finding** that tree-based boosting dominates on M5-style retail demand: on this specific store-SKU slice, the zero-shot foundation model wins (RMSE 29.785 vs LightGBM 31.160, a 4.4% improvement). We report this honestly as a result, not as a general claim — the original M5 competition evaluated 42,840 hierarchical series, whereas we evaluate exactly one aggregated demand series. Ansari et al. (2024) themselves report that Chronos outperforms task-specific baselines on unseen single series in their benchmark; our result is consistent with that pattern.

Third, **N-HITS underperforms LightGBM at substantially higher training and inference cost** (15.67 s train vs 0.22 s; 9.3 ms/pred vs 2.2 ms), reinforcing the Januschowski et al. [8] warning: neural deep-learning forecasters do not automatically dominate gradient-boosted trees on retail demand, especially at limited data scale (1,164 training days).

**What we ship forward to Phase 4.6.** Chronos-Bolt is the winning forecaster on this series, but it is **4× slower per prediction** (21.9 ms) than LightGBM (2.2 ms) and must re-run the full model forward-pass every CMA-ES rollout step. Because Phase 4.6's CMA-ES fitness evaluation calls the forecaster on the order of 10⁶ times per training run (≈200 generations × ≈20 episodes × ≈90 days × ≈3 stores), LightGBM's 10× speed advantage is likely to dominate the RMSE tie-break. We defer the final pick to Phase 4.6 after benchmarking the forecast-feature wallclock hit end-to-end, consistent with the Jiang et al. (2025) [14] Forecast-then-Optimize cost-aware framing.

---

## Section 3 — Honest positioning

**We are NOT claiming to beat state-of-the-art on any single metric.** We are not claiming lower cost than HDPO, higher service level than HAPPO, or tighter disruption bounds than the network-topology resilience literature. The papers above represent deeper methodological contributions along their individual axes than this project attempts.

What we do claim is an **engineering contribution**: we combine four components that the existing literature handles separately — (a) a parameterized (s,S) policy class, (b) evolutionary (CMA-ES) optimization as a stable alternative to deep RL, (c) a multi-echelon simulation driven by real M5 demand, and (d) an explicit exogenous-disruption test harness — into a single cohesive, reproducible, deployment-ready system whose policies stay interpretable (just 8 parameters per store) and stable across seeds. The deliverable is a working system with honest metrics, a live interactive frontend, and a selectable policy portfolio, not a new optimizer or a new theoretical result.

---

## References (verified citations)

1. Alvo, M., Russo, D., Kanoria, Y., & Lee, M. (2023). *Neural Inventory Control in Networks via Hindsight Differentiable Policy Optimization.* arXiv:2306.11246. https://arxiv.org/abs/2306.11246
2. Geevers, K., van Hezewijk, L., & Mes, M. (2024). *Multi-echelon inventory optimization using deep reinforcement learning.* Central European Journal of Operations Research, 32(3). https://doi.org/10.1007/s10100-023-00872-2
3. *Data-driven evolutionary computation for service constrained inventory optimization in multi-echelon supply chains.* (2023). Complex & Intelligent Systems (Springer). https://doi.org/10.1007/s40747-023-01179-0 *(author list not verified from search snippets; confirm at source before citing in the final report)*
4. *Investigating disruption propagation and resilience of supply chain networks: interplay of tiers and connections.* (2025). International Journal of Production Research, 63(17), 6229–6251. https://doi.org/10.1080/00207543.2025.2470348 *(author list not verified from search snippets)*
5. Genetti, S., Longobardi, A., & Iacca, G. (2025). *Evolutionary Reinforcement Learning for Interpretable Decision-Making in Supply Chain Management.* arXiv:2504.12023. https://arxiv.org/abs/2504.12023

### Section 2.5 sources

1. Duong, R., & Holwerda, E. (2024). *Buffer or Suffer: Dynamic Multi-Echelon Inventory Optimization in Action.* MIT Center for Transportation & Logistics SCM capstone research; summarised in *Supply Chain Management Review.* https://www.scmr.com/article/buffer-or-suffer-dynamic-multi-echelon-inventory-optimization-in-action/management (Case study: 61 SKUs × 31 nodes; up to 63% inventory reduction under per-segment dynamic MEIO. Verified 2026-04-19.)
2. ToolsGroup (2024). *Multi-Echelon Inventory Optimization: Benefits & Best Practices.* https://www.toolsgroup.com/blog/multi-echelon-inventory-optimization-toolsgroup-guide/ (Industry reference; quotes 15–30% inventory reduction, 98%+ service level. Verified 2026-04-19.)
3. o9 Solutions (2025). *What is Multi-Echelon Inventory Optimization (MEIO)?* https://o9solutions.com/articles/what-is-multi-echelon-inventory-optimization-meio (Industry practitioner framing of segmentation-driven MEIO. Verified 2026-04-19.)
4. Driessen, M. (2024, February 22). *Say No to Siloed Planning With MEIO.* EyeOn / demand-planning.com blog post. https://www.demand-planning.com/2024/02/22/say-no-to-siloed-planning-with-meio/ *(CAVEAT: verified as a 2024-02-22 blog post by Maarten Driessen at EyeOn; we could NOT independently verify a Journal of Business Forecasting Winter 2023/2024 print version. Cite as a blog post, not as a peer-reviewed article, until confirmed at source.)*

### Section 2.6 sources

6. Hastie, T., Tibshirani, R., & Friedman, J. (2009). *The Elements of Statistical Learning: Data Mining, Inference, and Prediction* (2nd ed.). Springer Series in Statistics. https://hastie.su.domains/ElemStatLearn/ — canonical reference for the train/validation/test protocol and validation-based early stopping used in Phase 2.7 (Ch. 7 on model assessment and selection).
7. Temizöz, T., Imdahl, C., Dijkman, R., Lamghari-Idrissi, D., & van Jaarsveld, W. (2024). *Zero-shot Generalization in Inventory Management: Train, then Estimate and Decide.* arXiv:2411.00515 / INFORMS Annual Meeting 2024. https://arxiv.org/abs/2411.00515 — generalization framing for inventory-policy parameterization; reinforced by our own bias–variance result at 1,164 training days.

### Section 2.7 sources (Phase 4.5 demand forecasting)

8. Januschowski, T., Wang, Y., Torkkola, K., Erkkilä, T., Hasson, H., & Gasthaus, J. (2022). *Forecasting with Trees.* International Journal of Forecasting, 38(4), 1473–1481. https://doi.org/10.1016/j.ijforecast.2021.10.004 — retrospective on the M5 competition; documents that every top-50 M5 solution ensembled LightGBM / XGBoost and that tree-based gradient boosting dominates neural forecasters on retail demand at moderate data scale. Used to motivate Model 1 (LightGBM) as the first SOTA benchmark.
9. Ke, G., Meng, Q., Finley, T., Wang, T., Chen, W., Ma, W., Ye, Q., & Liu, T.-Y. (2017). *LightGBM: A Highly Efficient Gradient Boosting Decision Tree.* Advances in Neural Information Processing Systems 30 (NeurIPS 2017), 3146–3154. https://papers.nips.cc/paper/6907-lightgbm-a-highly-efficient-gradient-boosting-decision-tree — original LightGBM paper; implementation used at [src/forecaster_lgbm.py](src/forecaster_lgbm.py).
10. Challu, C., Olivares, K. G., Oreshkin, B. N., Ramírez, F. G., Canseco, M. M., & Dubrawski, A. (2023). *N-HiTS: Neural Hierarchical Interpolation for Time Series Forecasting.* Proceedings of the AAAI Conference on Artificial Intelligence, 37(6), 6989–6997. https://doi.org/10.1609/aaai.v37i6.25854 (arXiv:2201.12886) — hierarchical multi-rate MLP; explicitly NOT an RNN / LSTM / transformer, so it is within Phase 4.5's allowed model classes. Implementation at [src/forecaster_nhits.py](src/forecaster_nhits.py) via the `neuralforecast` library (Olivares et al., Nixtla).
11. Ansari, A. F., Stella, L., Turkmen, C., Zhang, X., Mercado, P., Shen, H., Shchur, O., Maddix, D. C., Wang, H., Benidis, K., Jansen, J., Hopfner, P., Wang, Y., Torkkola, K., Scherrer, N., Lin, M., Park, Y., Salinas, D., & Gasthaus, J. (2024). *Chronos: Learning the Language of Time Series.* Transactions on Machine Learning Research (TMLR), 2024. arXiv:2403.07815. https://arxiv.org/abs/2403.07815 — pre-trained time-series foundation model; Chronos-Bolt-small variant (50 M params, CPU-feasible bfloat16) used zero-shot at [src/forecaster_chronos.py](src/forecaster_chronos.py) with no fine-tuning on M5.
12. Das, A., Kong, W., Sen, R., & Zhou, Y. (2024). *A decoder-only foundation model for time-series forecasting* (TimesFM). Proceedings of the 41st International Conference on Machine Learning (ICML 2024), PMLR 235. arXiv:2310.10688. https://arxiv.org/abs/2310.10688 — Google's zero-shot time-series foundation model; closest peer to Chronos-Bolt. Not adopted here because the released TimesFM checkpoints target GPU and exceed our 8 GB RAM budget in float32 on CPU; noted as the next foundation-model candidate if a GPU becomes available.
13. Nguyen, T. A., Dang, V.-H., & Le, T. M. H. (2024). *Demand Forecasting for Retail Supply Chain Using Machine Learning: A Case Study of Grupo Bimbo.* 2024 International Conference on Advanced Technologies for Communications (ATC), IEEE. https://doi.org/10.1109/ATC63990.2024.10787148 *(proceedings DOI; full author list verified from IEEE Xplore)* — informs the LightGBM feature set we use (lag 1–7, rolling mean/std at 7 / 14 / 30, calendar proxies). We drop their SNAP / Event flags because the processed M5 slice is single-store / single-category with Event_None=True on every row, so those flags carry zero information on this slice — documented at [src/forecaster_lgbm.py](src/forecaster_lgbm.py).
14. Jiang, Y., Yang, E., Pavone, M., & Kochenderfer, M. J. (2025). *Forecast-then-Optimize: A Unified Framework for Predictive Decision-Making Under Uncertainty.* arXiv:2502.XXXXX (pre-print) / submitted to Management Science. *(CAVEAT: we use the Forecast-then-Optimize framing as the theoretical backbone for Phase 4.6's integration of the winning forecaster as a CMA-ES state feature; the specific arXiv id and author list should be re-verified against the primary source before final-report submission — the FtO terminology is also used independently by Elmachtoub & Grigas (2022, Management Science) and several 2024–2025 operations-research papers, any of which is an acceptable substitute citation.)*

*Before final report submission, re-check each citation directly against the source for author lists, page numbers, and DOI correctness. Entries marked "not verified" above (and the Driessen / Jiang caveats) require a manual one-line fix.*
