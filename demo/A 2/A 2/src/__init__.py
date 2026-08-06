"""
Supply Chain Inventory Optimization via CMA-ES.

Core modules:
    environment      - SupplyChainEnv (M5 Walmart data)
    hybrid_policy    - ParameterizedSSPolicy (CMA-ES optimized)
    train_cmaes      - CMA-ES training loop
    evaluate         - Honest holdout evaluation (5 seeds)
    rl_debug_utils   - WelfordNormalizer, EpsilonSchedule, etc.
    holiday_calendar - Holiday demand multipliers
    service_level_guard - Inference-time reorder-point overlay
    preprocess_m5_data  - M5 Walmart preprocessing pipeline
"""
