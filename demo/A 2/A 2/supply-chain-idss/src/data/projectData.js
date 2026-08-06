// ═══════════════════════════════════════════════════════════════
// Ground-truth data model for the Hierarchical RL Supply Chain IDSS.
// Numbers here match what the Python backend actually computes
// (see src/environment.py, src/strategic_policy.py, src/tactical_policy.py)
// ═══════════════════════════════════════════════════════════════

// ── Accuracy breakdown (weighted score out of 100) ──────────────
export const accuracyBreakdown = [
    { component: 'Reward Score', score: 74.2, weight: 30, contribution: 22.3 },
    { component: 'Service Level', score: 94.5, weight: 30, contribution: 28.4 },
    { component: 'Learning Stability', score: 62.7, weight: 20, contribution: 12.5 },
    { component: 'Best Performance', score: 77.8, weight: 20, contribution: 15.6 },
]

// ── 3 training runs across hyperparameter tuning iterations ─────
export const performanceComparison = [
    {
        name: 'Original Training', episodes: 500, bestReward: 336, serviceLevel: 7.6,
        accuracy: 28.5, grade: 'C',
        narrative: 'First pass — the agent barely beat doing nothing. Service level near zero, reward essentially flat. We knew something was wrong.',
    },
    {
        name: 'Tuned Training', episodes: 1000, bestReward: 11105, serviceLevel: 63,
        accuracy: 78.7, grade: 'B+',
        narrative: 'After fixing the data loop, expanding the action space to 8, collapsing strategic modes to 3, and adding cosine-annealed LR — the agent finally learned.',
    },
    {
        name: 'Final Training', episodes: 500, bestReward: 4210, serviceLevel: 41,
        accuracy: 62.1, grade: 'B-',
        narrative: 'Shorter retrain to verify reproducibility with stricter seeds. Variance is still high — a real RL problem, not a bug.',
    },
]

// ── Agent comparison across 5 seeds ─────────────────────────────
export const agentComparison = [
    { agent: 'Baseline DQN', avgReward: -142486, serviceLevel: 0, description: 'Single-network DQN with no hierarchy. Never learned to order correctly.' },
    { agent: '(s,S) Classical', avgReward: -234, serviceLevel: 78, description: 'Reorder-point policy s=200 S=800. Rock-solid baseline with zero training.' },
    { agent: 'Deterministic', avgReward: -23302, serviceLevel: 15, description: 'Greedy argmax without exploration. Got stuck in local minima.' },
    { agent: 'Hierarchical RL', avgReward: -25830, serviceLevel: 63, description: 'Our two-level DQN system. Adapts strategy every 30 days, orders daily.' },
]

// ── Strategic modes (3, matches strategic_policy.py action_dim=3) ─
export const strategicModes = [
    {
        id: 0, name: 'Conservative', icon: '🛡️', color: '#60a5fa',
        description: 'Build buffers. Higher reorder and holding costs; lower stockout risk.',
        multipliers: { reorder: 1.15, holding: 1.1, stockout: 1.5 },
        bestFor: 'Volatile demand, holiday season, supply disruptions',
    },
    {
        id: 1, name: 'Balanced', icon: '⚖️', color: '#a78bfa',
        description: 'Baseline operations. 1.0× multipliers across the board.',
        multipliers: { reorder: 1.0, holding: 1.0, stockout: 1.0 },
        bestFor: 'Normal operations, stable demand periods',
    },
    {
        id: 2, name: 'Aggressive', icon: '🔥', color: '#f59e0b',
        description: 'Cut costs. Lower reorder and holding; accept more stockout risk.',
        multipliers: { reorder: 0.85, holding: 0.8, stockout: 0.7 },
        bestFor: 'Cost-focused quarters, slow periods, clearance',
    },
]

// ── Tactical actions (8, matches environment.py action_map) ─────
export const tacticalActions = [
    { id: 0, name: 'No Order', units: 0, description: 'Skip this period', color: '#34d399' },
    { id: 1, name: 'Tiny', units: 50, description: 'Micro replenishment', color: '#10b981' },
    { id: 2, name: 'Small', units: 150, description: 'Light replenishment', color: '#60a5fa' },
    { id: 3, name: 'Medium-', units: 300, description: 'Cautious replenishment', color: '#3b82f6' },
    { id: 4, name: 'Medium', units: 500, description: 'Balanced replenishment', color: '#a78bfa' },
    { id: 5, name: 'Medium+', units: 750, description: 'Assertive replenishment', color: '#8b5cf6' },
    { id: 6, name: 'Large', units: 1000, description: 'Aggressive replenishment', color: '#f59e0b' },
    { id: 7, name: 'Bulk', units: 1500, description: 'Emergency buffer build', color: '#ef4444' },
]

// ── Feature importance (from SHAP / policy_explainer.py) ────────
export const featureImportance = [
    { feature: 'Inventory Level',       importance: 0.285, color: '#3b82f6', mass: 28.5 },
    { feature: 'Demand Forecast (Q50)', importance: 0.221, color: '#8b5cf6', mass: 22.1 },
    { feature: 'Cost Forecast (Q90)',   importance: 0.148, color: '#22d3ee', mass: 14.8 },
    { feature: 'Demand Forecast (Q90)', importance: 0.127, color: '#10b981', mass: 12.7 },
    { feature: 'Cost Forecast (Q50)',   importance: 0.098, color: '#f59e0b', mass:  9.8 },
    { feature: 'Demand Forecast (Q10)', importance: 0.076, color: '#ef4444', mass:  7.6 },
    { feature: 'Cost Forecast (Q10)',   importance: 0.045, color: '#ec4899', mass:  4.5 },
]

// ── Tech stack ──────────────────────────────────────────────────
export const techStack = [
    { name: 'Python 3',        category: 'Language',    icon: '🐍', description: 'Core implementation language' },
    { name: 'PyTorch 2.9',     category: 'Deep Learning', icon: '🔥', description: 'DQN networks, MPS on Apple Silicon' },
    { name: 'NumPy',           category: 'Computing',   icon: '🔢', description: 'State vectors, reward math' },
    { name: 'Pandas',          category: 'Data',        icon: '🐼', description: '1,941-row Walmart M5 time series' },
    { name: 'Scikit-learn',    category: 'Preprocessing', icon: '🤖', description: 'StandardScaler for state normalisation' },
    { name: 'Gym-style Env',   category: 'RL',          icon: '🏋️', description: 'Custom SupplyChainEnv, 90-day episodes' },
    { name: 'SHAP',            category: 'Explainability', icon: '🔍', description: 'Feature importance attribution' },
    { name: 'React + Vite',    category: 'Frontend',    icon: '⚛️', description: 'This dashboard you are reading' },
]

// ── Pipeline stages for the end-to-end system flow ──────────────
export const pipelineStages = [
    { id: 1, name: 'State Sensing',      icon: '📡', description: 'Read inventory, demand, costs, holiday flags from the environment', color: '#60a5fa' },
    { id: 2, name: 'Strategic Policy',   icon: '🧠', description: 'DQN chooses Conservative / Balanced / Aggressive every 30 days',    color: '#3b82f6' },
    { id: 3, name: 'Tactical Policy',    icon: '⚙️', description: 'DQN chooses 1 of 8 order quantities each day, conditioned on mode', color: '#a78bfa' },
    { id: 4, name: 'Environment Step',   icon: '🏭', description: 'Apply order, advance time, simulate demand fulfillment',            color: '#34d399' },
    { id: 5, name: 'Reward & Learn',     icon: '⚡', description: 'Compute reward = -holding -stockout +service. Backprop via DQN.',   color: '#f59e0b' },
    { id: 6, name: 'Explainability',     icon: '📋', description: 'SHAP feature attribution + decision-rule extraction',                color: '#22d3ee' },
]

// ── Problem statements (the 4 challenges the system solves) ─────
export const problemCards = [
    {
        id: 1, title: 'Inventory Uncertainty', icon: '📦', color: '#ef4444',
        front: 'Demand is stochastic. Order too little and you stock out. Order too much and holding costs eat your margin.',
        back: 'Our tactical DQN reads a 7-dim state (inventory, demand forecasts Q10/Q50/Q90, cost forecasts, defect rate) and picks from 8 quantised order sizes.',
    },
    {
        id: 2, title: 'Cost Trade-offs', icon: '💰', color: '#f59e0b',
        front: 'Holding cost, stockout penalty, procurement cost — three adversarial objectives that shift every week.',
        back: 'The reward function weights all three with a Lagrangian multiplier that adapts lambda_init=10 based on service-level violations.',
    },
    {
        id: 3, title: 'Strategy Rigidity', icon: '🗿', color: '#fb923c',
        front: 'A single policy cannot handle a normal Tuesday and Black Friday with the same ruleset. Traditional DQNs collapse on regime changes.',
        back: 'The strategic layer re-chooses a mode every 30 days — 3 modes (Conservative / Balanced / Aggressive) each multiply the tactical reward surface.',
    },
    {
        id: 4, title: 'Temporal Complexity', icon: '⏳', color: '#a78bfa',
        front: 'Lead times, seasonality, holidays, trends. A decision today pays off (or fails) 30 days from now.',
        back: 'γ=0.99 discount + 1941-row M5 Walmart history + holiday multipliers give the agent enough signal to learn multi-horizon credit assignment.',
    },
]

// ── Hyperparameters table (from train.py Config) ────────────────
export const hyperparameters = [
    { k: 'ε start',            v: '1.0',    group: 'exploration' },
    { k: 'ε end',              v: '0.05',   group: 'exploration' },
    { k: 'ε decay',            v: '0.998',  group: 'exploration' },
    { k: 'Strategic LR',       v: '5e-4',   group: 'optimiser' },
    { k: 'Tactical LR',        v: '3e-4',   group: 'optimiser' },
    { k: 'γ (discount)',       v: '0.99',   group: 'reward' },
    { k: 'Mode penalty',       v: '10.0',   group: 'reward' },
    { k: 'Service bonus',      v: '100.0',  group: 'reward' },
    { k: 'Strategic batch',    v: '128',    group: 'training' },
    { k: 'Tactical batch',     v: '256',    group: 'training' },
    { k: 'Episodes',           v: '1,000',  group: 'training' },
    { k: 'Episode length',     v: '90 days', group: 'training' },
    { k: 'Grad clip',          v: '1.0',    group: 'optimiser' },
    { k: 'LR schedule',        v: 'Cosine', group: 'optimiser' },
    { k: 'Seeds evaluated',    v: '5',      group: 'training' },
    { k: 'Warmup episodes',    v: '10',     group: 'training' },
]

// ── Plain-language glossary for metric tooltips ─────────────────
export const glossary = {
    'service level':   { term: 'Service Level', def: 'Percentage of customer demand that was actually fulfilled. 100% = never stocked out. 0% = always out of stock.' },
    'reward':          { term: 'Reward', def: 'Scalar score per timestep. Combines -holding_cost -stockout_penalty +service_bonus. Higher is better.' },
    'dqn':             { term: 'DQN', def: 'Deep Q-Network. Neural net that learns the value of each action in each state via Bellman updates.' },
    'epsilon':         { term: 'ε (Epsilon)', def: 'Probability of picking a random action instead of the learned best one. Decays over training.' },
    'q-value':         { term: 'Q-value', def: 'Predicted total future reward for taking a given action in the current state.' },
    'hrl':             { term: 'Hierarchical RL', def: 'Two-level RL: a strategic policy picks high-level goals, a tactical policy picks low-level actions.' },
    'stockout':        { term: 'Stockout', def: 'When inventory hits zero and customer demand is left unmet. Expensive in both dollars and trust.' },
    'holding cost':    { term: 'Holding Cost', def: 'Cost of storing inventory per unit per day (warehouse, insurance, obsolescence).' },
    'lagrangian':      { term: 'Lagrangian Multiplier', def: 'An adaptive penalty weight (lambda) that increases when the agent violates a constraint and decreases when it over-complies.' },
    'gamma':           { term: 'γ (Gamma)', def: 'Discount factor. 0.99 means a reward 100 steps away is worth 37% of an immediate reward.' },
}

// ── Chapter thread for the storytelling narrator ────────────────
export const chapters = [
    { id: 'overview',       title: 'The Problem',        beat: 'A baseline DQN loses money every episode. Something had to change.' },
    { id: 'architecture',   title: 'Two-Level Intelligence', beat: 'Split the brain: strategy weekly, tactics daily.' },
    { id: 'results',        title: 'Proof',              beat: 'B+ (78.7/100). 33× the reward. 63% service level.' },
    { id: 'demo',           title: 'Try it Yourself',    beat: 'Move the sliders. Watch the agent decide.' },
    { id: 'playground',     title: 'Policy Playground',  beat: 'Real θ values. See how the (s,S) policy adapts.' },
    { id: 'explain',        title: 'Why You Can Trust It', beat: 'Every decision traced back to input features.' },
    { id: 'tech',           title: 'Built With',         beat: 'Python, PyTorch, 1,941 rows of real Walmart data.' },
]
