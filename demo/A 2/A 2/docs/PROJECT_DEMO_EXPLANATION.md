# SmartStock Project Explanation

## Introduction

SmartStock is an AI-powered inventory decision-support system for retail supply chains. The project focuses on a very practical question: **how much stock should a store order today so that it avoids both stockouts and overstocking?**

In real retail operations, ordering too little means customers cannot buy the product, which causes lost sales and poor service. Ordering too much creates storage cost, wasted capital, and slow-moving inventory. SmartStock tries to balance both sides by combining demand forecasting, inventory policy logic, explainability, and stress testing into one interactive demo.

The project uses historical retail demand data inspired by the M5 Walmart dataset. It compares modern forecasting models such as Chronos-Bolt, N-HITS, and LightGBM, then uses those signals inside an inventory ordering engine. The final output is not just a graph or a prediction; it is a usable recommendation such as **order 300 units**, with supporting reasons, risk indicators, model comparison, and scenario tests.

In simple words, SmartStock acts like an intelligent assistant for a store manager. It reads current stock, demand trends, forecasted demand, supplier delay, and risk conditions, then helps the user make a safer replenishment decision.

## UI Design Overview

The UI is designed as a premium interactive demo rather than a plain dashboard. The goal is that a teacher, customer, or evaluator can understand the project by simply exploring the screen.

The application uses a dark premium theme with teal and gold accents. This gives the project a modern AI-product feel while keeping the interface readable. The navigation has been moved to the top so the main screens feel more like a polished product than a generic admin panel.

The main navigation options are:

- **Showcase**: the main interactive demo page.
- **Command**: the overall control dashboard with key metrics and alerts.
- **Stock Ops**: inventory visibility and product-level stock information.
- **Orders**: replenishment recommendations and order review.
- **Demand AI**: forecasting and demand model views.
- **Risk Lab**: disruption and stress-test analysis.
- **Logs**: decision history and audit trail.
- **Setup**: configuration and demo settings.

The Showcase screen is the centerpiece. It contains a premium hero section, a live recommendation card, SKU selection, sliders for stock and demand conditions, charts, decision factors, model comparison, and stress-test scenarios. A user can change demand, lead time, inventory, or shock level and immediately see how the recommendation changes.

The UI is intentionally interactive because the project is easier to demonstrate when the viewer can touch the system. Instead of only saying "the model reacts to demand changes," the demo lets the teacher move a slider and see the order quantity, service level, and risk update in real time.

The notification bell is also interactive. It opens an alerts panel showing live-style signals such as stockout risk, demand spikes, and model disagreement. This makes the project feel more like a real operational tool.

## Low-Level Design Overview

At a lower level, SmartStock is divided into three major layers: the data/model layer, the frontend data layer, and the user interface layer.

### 1. Data and Model Layer

The backend/modeling side is written in Python. It contains scripts for preprocessing retail demand data, training and evaluating forecasting models, simulating inventory behavior, optimizing policies, and generating explainability outputs.

Important modules include:

- `src/preprocess_m5_raw.py` and `src/preprocess_m5_data.py`: prepare raw retail demand data for modeling.
- `src/forecaster_chronos.py`, `src/forecaster_nhits.py`, and `src/forecaster_lgbm.py`: generate demand forecasts using different model families.
- `src/hybrid_policy.py`: implements a parameterized inventory ordering policy.
- `src/train_cmaes.py`: optimizes policy parameters using CMA-ES.
- `src/policy_explainer.py`: explains decisions using perturbation-based sensitivity analysis.
- `src/disruption_engine.py` and `src/disruption_engine_multi_sku.py`: simulate stress scenarios and supply-chain disruptions.

The core inventory policy is based on the classical **(s,S)** idea. In a classical system, if inventory falls below a reorder point `s`, the store orders up to a target level `S`. SmartStock improves this by making `s` and `S` context-dependent. That means the thresholds can change based on demand forecast, demand uncertainty, and supplier lead time.

The simplified idea is:

```text
Read current state:
inventory, demand forecast, demand uncertainty, lead time

Compute dynamic reorder point:
s = base + demand weight + uncertainty weight + lead-time weight

Compute dynamic order target:
S = base + demand weight + uncertainty weight + lead-time weight

If inventory is below s:
    recommend an order quantity
Else:
    recommend no order
```

CMA-ES is used to tune the policy parameters. This keeps the model interpretable because the result is still an inventory policy, not a black-box action with no explanation.

### 2. Frontend Data Layer

The frontend is a static single-page application. It does not need a heavy backend server for the demo. It reads prepared JSON files from `frontend/data`.

Key files include:

- `skus.json`: product information.
- `inventory_today.json`: current stock and demand summary.
- `forecasts.json`: forecast outputs for each SKU.
- `recommendations.json`: recommended order quantities.
- `explanations.json`: decision factors and counterfactuals.
- `comparison.json`: model and policy comparison metrics.
- `disruptions.json`: stress-test scenarios.
- `alerts.json`: alert data used by dashboard views.

The `frontend/js/api.js` file acts as a small local API wrapper. It loads these JSON files, caches them, and gives the screens a clean way to request data.

### 3. User Interface Layer

The UI is built with vanilla JavaScript, HTML, CSS, Chart.js, and Lucide icons. The app starts from `frontend/index.html`, loads `frontend/js/app.js`, then uses the router in `frontend/js/router.js` to display the selected screen.

Each screen lives in `frontend/js/screens`, for example:

- `demo.js`: main interactive Showcase.
- `dashboard.js`: command dashboard.
- `inventory.js`: stock operations.
- `orders.js`: replenishment workflow.
- `forecasts.js`: demand forecasting view.
- `stress.js`: risk and disruption testing.
- `history.js`: decision logs.
- `settings.js`: configuration.

The application state is handled in `frontend/js/state.js`. It stores user decisions locally, so actions such as approving or modifying an order can appear in history without requiring a database.

Charts are rendered with Chart.js through helper functions in `frontend/js/components/chart-helpers.js`. The UI updates dynamically when the user changes sliders, selects products, or opens different scenarios.

## Conclusion

SmartStock is more than a dashboard. It is a complete demonstration of how forecasting, inventory policy, explainability, and risk analysis can work together in a retail decision-support system.

The strength of the project is that it connects technical AI work to a real business problem. It does not stop at predicting demand; it turns the prediction into an action, explains why that action was chosen, and lets the user test what happens under different conditions.

For a teacher or evaluator, the project can be explained as an end-to-end AI inventory prototype:

- It reads retail inventory and demand data.
- It forecasts future demand.
- It recommends replenishment quantities.
- It compares model and policy performance.
- It explains the reasoning behind decisions.
- It stress-tests the system against disruptions.
- It presents everything through an interactive premium UI.

In short, SmartStock shows how AI can support better inventory decisions while still keeping the final recommendation understandable to a human user.

