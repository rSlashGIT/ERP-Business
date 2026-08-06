"""
DEPRECATED — DO NOT RUN.

This script produced the legacy z-scored
`data/processed/processed_data_m5.csv`, which scrambled real integer
demand into StandardScaler-fitted floats and left downstream models
training on near-mean-zero signal.

It has been superseded by `src/preprocess_m5_raw.py`, which reads the
raw Kaggle M5 files and emits a clean, honest
`data/processed/m5_clean.csv` (real integer demand, real dollar prices,
provenance JSON). All current loaders (multi_echelon._load_m5_series,
forecaster_eval.load_m5_and_split) point at the clean CSV.

This file is kept on disk only as a historical reference for the
preprocessing decisions that produced the legacy artifact. Re-running
it would overwrite `processed_data_m5.csv` in place but would NOT
regenerate or affect `m5_clean.csv`.

Simple M5 Preprocessing: Aggregate by store and create daily time-series data.

Instead of per-product granularity, we aggregate to store level for faster processing.
This gives us 1000+ rows of realistic supply chain data vs the original 100 rows.
"""

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
import joblib
import os
import warnings

warnings.filterwarnings('ignore')

M5_DIR = 'data/m5'
OUTPUT_FILE = 'data/processed/processed_data_m5.csv'
SCALER_FILE = 'data/processed/scaler_m5.pkl'

print("=" * 70)
print("M5 WALMART DATA PREPROCESSING")
print("=" * 70)

# ============================================================================
# LOAD DATA
# ============================================================================

print("\n[1] Loading M5 data...")

sales = pd.read_csv(f'{M5_DIR}/sales_train_evaluation.csv', nrows=100)
prices = pd.read_csv(f'{M5_DIR}/sell_prices.csv')
calendar = pd.read_csv(f'{M5_DIR}/calendar.csv')

print(f"    Sales: {sales.shape}")
print(f"    Prices: {prices.shape}")
print(f"    Calendar: {calendar.shape}")

# ============================================================================
# PARSE IDS AND AGGREGATE
# ============================================================================

print("\n[2] Parsing data and extracting features...")

# Extract store and category from sales id
# Format: CATEGORY_DEPT_PRODUCT_STORE_ID_TYPE
parts = sales['id'].str.split('_', expand=True)
sales['store'] = parts[3] + '_' + parts[4]
sales['category'] = parts[0]

# Get day columns
day_cols = [col for col in sales.columns if col.startswith('d_')]
sales_values = sales[day_cols].values

# sales_values is (products, days)
# Aggregate to daily sales: sum across all products for each day
sales_by_day = sales_values.sum(axis=0)  # (days,)

# ============================================================================
# CREATE TIME-SERIES WITH FEATURES
# ============================================================================

print("\n[3] Engineering features...")

rng = np.random.RandomState(42)

n_days = len(day_cols)
records = []

for day_idx, day_col in enumerate(day_cols):
    # Get demand for this day (aggregated across all products)
    demand = sales_by_day[day_idx]

    # Get price (average across all products this week)
    if day_idx < len(calendar):
        week_id = calendar.iloc[day_idx]['wm_yr_wk'] if day_idx < len(calendar) else 11325
        week_prices = prices[prices['wm_yr_wk'] == week_id]['sell_price']
        avg_price = week_prices.mean() if len(week_prices) > 0 else 50.0
    else:
        avg_price = 50.0

    # Simulate inventory (simple model: start at 500, decrease by demand, restock by orders)
    # Order when inventory < 100
    if day_idx == 0:
        inventory = 500
    else:
        prev_demand = sales_by_day[day_idx-1]
        inventory = records[-1]['Stock levels'] - prev_demand
        if inventory < 100:
            inventory += 500  # Restock

    inventory = max(0, inventory)

    # Other features
    record = {
        'Price': avg_price,
        'Number of products sold': int(demand),
        'Stock levels': inventory,
        'Lead times': rng.randint(5, 11),
        'Order quantities': int(demand * 1.2) if inventory < 100 else 0,
        'Shipping costs': avg_price * 0.05,
        'Manufacturing costs': avg_price * 0.6,
        'Defect rates': max(0, demand * rng.uniform(0, 0.05)),
        'Costs': avg_price * 0.65,
        'Store': 'CA_1',
        'Category': 'FOODS',
        'Event': 'None'
    }
    records.append(record)

df = pd.DataFrame(records)
print(f"    Created {len(df)} daily records")

# ============================================================================
# ONE-HOT ENCODE CATEGORICAL FEATURES
# ============================================================================

print("\n[4] Encoding categorical features...")

categorical_cols = ['Store', 'Category', 'Event']
df_encoded = pd.get_dummies(df, columns=categorical_cols, drop_first=False)

print(f"    Shape after encoding: {df_encoded.shape}")

# ============================================================================
# SCALE NUMERICAL FEATURES
# ============================================================================

print("\n[5] Scaling numerical features...")

numerical_cols = [
    'Price',
    'Number of products sold',
    'Stock levels',
    'Lead times',
    'Order quantities',
    'Shipping costs',
    'Manufacturing costs',
    'Defect rates',
    'Costs'
]

train_cutoff = int(len(df_encoded) * 0.8)
scaler = StandardScaler()
scaler.fit(df_encoded[numerical_cols].iloc[:train_cutoff])
df_encoded[numerical_cols] = scaler.transform(df_encoded[numerical_cols])

# ============================================================================
# SAVE
# ============================================================================

print("\n[6] Saving data...")

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

df_encoded.to_csv(OUTPUT_FILE, index=False)
joblib.dump(scaler, SCALER_FILE)

print(f"\n{'='*70}")
print(f"✅ PREPROCESSING COMPLETE")
print(f"{'='*70}")
print(f"\nOutput:")
print(f"  CSV:    {OUTPUT_FILE} ({len(df_encoded)} rows)")
print(f"  Scaler: {SCALER_FILE}")
print(f"\nData shape: {df_encoded.shape}")
print(f"\nTo use in your environment:")
print(f"""
env = SupplyChainEnv(
    data_path='{OUTPUT_FILE}',
    scaler_path='{SCALER_FILE}'
)
""")
