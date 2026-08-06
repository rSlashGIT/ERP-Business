#!/usr/bin/env python3
"""Phase 7 — Multi-SKU M5 data preparation.

Selects 30 SKUs from M5 store CA_1 spanning three demand-volume buckets:
    10 high-volume   (mean > 30 / day)
    10 medium-volume (mean 5-30 / day)
    10 low-volume    (mean < 5 / day, but reject > 70% zero-days)

Selection rules:
    - All SKUs must come from CA_1 (same store).
    - Each SKU must have at least 1500 days of valid (priced + non-leading-zero) data.
    - Reject SKUs with > 70% zero-days as too intermittent.
    - Mix categories (FOODS / HOUSEHOLD / HOBBIES) within each bucket where possible.
    - Selection is deterministic (seed=42 sort, then take from each bucket).

Output:
    data/processed/m5_multi_sku.csv    long-format: sku_id, day, demand, price, dow, month, snap, event
    data/processed/m5_multi_sku_summary.json
        selected_skus  : [list of 30 SKU ids]
        per_sku_stats  : {sku_id: {mean, std, zero_fraction, total_days, category, bucket}}

Does not modify any single-SKU artifact.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


SALES_CSV    = 'data/m5/sales_train_evaluation.csv'
CALENDAR_CSV = 'data/m5/calendar.csv'
PRICES_CSV   = 'data/m5/sell_prices.csv'

OUT_DIR  = 'data/processed'
OUT_CSV  = os.path.join(OUT_DIR, 'm5_multi_sku.csv')
OUT_JSON = os.path.join(OUT_DIR, 'm5_multi_sku_summary.json')

TARGET_STORE = 'CA_1'
N_DAYS_RAW   = 1941
MIN_VALID_DAYS  = 1500
MAX_ZERO_FRAC   = 0.70

# Phase 7 spec called for high>30, low<5, but only 4 CA_1 SKUs exceed
# 30/day. The empirical CA_1 demand distribution is heavily right-skewed
# (median ~1.2/day, q0.99 ~17.5/day). Adjusted thresholds are scaled to
# pick the volume tail (top ~3% as "high"), the moderate-volume body
# (~25% as "medium"), and the bulk low-volume mass (~70% as "low").
# Documented in summary JSON under 'thresholds.notes'.
HIGH_THRESHOLD = 10.0   # mean > 10 -> high (47 candidates at CA_1)
LOW_THRESHOLD  = 2.0    # mean < 2  -> low  (medium is 2 <= mean <= 10)

# Same dow conversion as preprocess_m5_raw.py.
M5_WDAY_TO_DOW = {3: 0, 4: 1, 5: 2, 6: 3, 7: 4, 1: 5, 2: 6}


def _load_calendar() -> pd.DataFrame:
    cal = pd.read_csv(CALENDAR_CSV)
    cal = cal[cal['d'].isin({f'd_{i}' for i in range(1, N_DAYS_RAW + 1)})].copy()
    cal['d_index'] = cal['d'].str.slice(2).astype(int)
    cal = cal.sort_values('d_index').reset_index(drop=True)
    return cal


def _load_prices_for_store(store_id: str) -> Dict[Tuple[str, int], float]:
    """Return {(item_id, wm_yr_wk): sell_price} for the given store."""
    out: Dict[Tuple[str, int], float] = {}
    with open(PRICES_CSV) as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row[0] == store_id:
                out[(row[1], int(row[2]))] = float(row[3])
    return out


def _build_sku_series(sales_row_d: np.ndarray,
                      calendar: pd.DataFrame,
                      prices: Dict[Tuple[str, int], float],
                      item_id: str,
                      ) -> pd.DataFrame | None:
    """Build a per-day frame for a single SKU. Trim leading days with no
    real price OR zero demand. Return None if the resulting series is too
    short or too sparse."""
    n = len(sales_row_d)
    wm_weeks = calendar['wm_yr_wk'].tolist()
    raw_prices = [prices.get((item_id, wm), float('nan')) for wm in wm_weeks]

    # First day with real price AND demand>0.
    trim_idx = None
    for i in range(n):
        if sales_row_d[i] > 0 and not np.isnan(raw_prices[i]):
            trim_idx = i
            break
    if trim_idx is None:
        return None

    # Forward-fill prices from trim_idx onward; back-fill before trim_idx
    # is irrelevant because we drop those days.
    last_good = float('nan')
    filled: List[float] = []
    for i in range(n):
        p = raw_prices[i]
        if np.isnan(p):
            filled.append(last_good)
        else:
            filled.append(p)
            last_good = p

    sliced_prices = filled[trim_idx:]
    sliced_demand = sales_row_d[trim_idx:].astype(np.int64)
    n_valid = len(sliced_demand)

    if n_valid < MIN_VALID_DAYS:
        return None

    # NaN prices remaining? Shouldn't happen because trim_idx already
    # required a real price at i=trim_idx, but be safe.
    if any(np.isnan(p) for p in sliced_prices):
        return None

    zero_frac = float((sliced_demand == 0).mean())
    if zero_frac > MAX_ZERO_FRAC:
        return None

    cal_slice = calendar.iloc[trim_idx:].reset_index(drop=True)
    dows = [M5_WDAY_TO_DOW[int(w)] for w in cal_slice['wday'].tolist()]

    df = pd.DataFrame({
        'sku_id': [item_id] * n_valid,
        'day':    np.arange(n_valid, dtype=np.int64),
        'date':   cal_slice['date'].tolist(),
        'demand': sliced_demand,
        'price':  np.asarray(sliced_prices, dtype=np.float64),
        'dow':    np.asarray(dows, dtype=np.int64),
        'month':  cal_slice['month'].astype(np.int64).tolist(),
        'snap':   cal_slice['snap_CA'].astype(bool).tolist(),
        'event':  cal_slice['event_name_1'].fillna('').astype(str).tolist(),
    })
    return df


def _category_from_item(item_id: str) -> str:
    # Item ids look like "FOODS_3_090", "HOUSEHOLD_1_001", "HOBBIES_2_017".
    return item_id.split('_', 1)[0]


def _select_balanced(candidates: List[Tuple[str, str, float, float, int]],
                     n_per_bucket: int = 10,
                     ) -> List[Tuple[str, str, float, float, int]]:
    """Pick n_per_bucket SKUs from `candidates` (one bucket worth), trying
    to spread across categories. `candidates` rows are
    (item_id, category, mean, zero_frac, total_days)."""
    if len(candidates) <= n_per_bucket:
        return list(candidates)
    # Group by category, sort within category by mean (descending so the
    # representative SKUs come first), round-robin until we hit the quota.
    by_cat: Dict[str, List[Tuple[str, str, float, float, int]]] = {}
    for c in candidates:
        by_cat.setdefault(c[1], []).append(c)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda r: r[2], reverse=True)

    picked: List[Tuple[str, str, float, float, int]] = []
    cats = sorted(by_cat.keys())
    # round-robin
    idxs = {cat: 0 for cat in cats}
    while len(picked) < n_per_bucket:
        for cat in cats:
            if len(picked) >= n_per_bucket:
                break
            i = idxs[cat]
            if i < len(by_cat[cat]):
                picked.append(by_cat[cat][i])
                idxs[cat] += 1
        # safety: if we made no progress this pass, all buckets exhausted.
        if all(idxs[cat] >= len(by_cat[cat]) for cat in cats):
            break
    return picked


def run() -> Dict:
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"Loading calendar...")
    cal = _load_calendar()
    print(f"  {len(cal)} calendar days")

    print(f"Loading prices for store {TARGET_STORE}...")
    prices = _load_prices_for_store(TARGET_STORE)
    print(f"  {len(prices)} (item, week) price entries")

    print(f"Scanning SKUs at {TARGET_STORE} for valid candidates...")
    high: List[Tuple[str, str, float, float, int]] = []
    medium: List[Tuple[str, str, float, float, int]] = []
    low: List[Tuple[str, str, float, float, int]] = []

    n_scanned = 0
    n_rejected = 0
    with open(SALES_CSV) as f:
        reader = csv.reader(f)
        header = next(reader)
        d_start = header.index('d_1')
        for row in reader:
            if row[4] != TARGET_STORE:
                continue
            n_scanned += 1
            item_id = row[1]
            cat = _category_from_item(item_id)
            sales = np.asarray([int(x) for x in row[d_start:d_start + N_DAYS_RAW]],
                               dtype=np.int64)
            df = _build_sku_series(sales, cal, prices, item_id)
            if df is None:
                n_rejected += 1
                continue
            mean = float(df['demand'].mean())
            zero_frac = float((df['demand'] == 0).mean())
            entry = (item_id, cat, mean, zero_frac, len(df))
            if mean > HIGH_THRESHOLD:
                high.append(entry)
            elif mean < LOW_THRESHOLD:
                low.append(entry)
            else:
                medium.append(entry)

    print(f"  scanned={n_scanned}  rejected={n_rejected}")
    print(f"  high candidates  : {len(high)}")
    print(f"  medium candidates: {len(medium)}")
    print(f"  low candidates   : {len(low)}")

    if len(high) < 10 or len(medium) < 10 or len(low) < 10:
        raise RuntimeError(
            f"Not enough candidates per bucket "
            f"(high={len(high)}, medium={len(medium)}, low={len(low)})"
        )

    # Sort each bucket so selection is deterministic. For high/medium we
    # take the most representative (highest-mean) within bucket; for low
    # we take the least-sparse (lowest zero_frac) so the selected low-SKUs
    # are still simulatable.
    high.sort(key=lambda r: (-r[2], r[0]))
    medium.sort(key=lambda r: (-r[2], r[0]))
    low.sort(key=lambda r: (r[3], -r[2], r[0]))

    sel_high   = _select_balanced(high,   10)
    sel_medium = _select_balanced(medium, 10)
    sel_low    = _select_balanced(low,    10)

    selected = sel_high + sel_medium + sel_low

    print(f"\nSelected SKUs:")
    print(f"  HIGH (mean > {HIGH_THRESHOLD}):")
    for it, ct, m, zf, td in sel_high:
        print(f"    {it:<20} {ct:<10} mean={m:>7.2f}  zero={zf:>5.1%}  days={td}")
    print(f"  MEDIUM ({LOW_THRESHOLD} <= mean <= {HIGH_THRESHOLD}):")
    for it, ct, m, zf, td in sel_medium:
        print(f"    {it:<20} {ct:<10} mean={m:>7.2f}  zero={zf:>5.1%}  days={td}")
    print(f"  LOW (mean < {LOW_THRESHOLD}):")
    for it, ct, m, zf, td in sel_low:
        print(f"    {it:<20} {ct:<10} mean={m:>7.2f}  zero={zf:>5.1%}  days={td}")

    # Re-build the per-day frames for the selected SKUs and concatenate.
    print(f"\nRebuilding per-day frames for {len(selected)} SKUs...")
    selected_ids = {r[0] for r in selected}
    frames: List[pd.DataFrame] = []
    per_sku_stats: Dict[str, Dict] = {}
    bucket_label = (
        {r[0]: 'high'   for r in sel_high}   |
        {r[0]: 'medium' for r in sel_medium} |
        {r[0]: 'low'    for r in sel_low}
    )
    with open(SALES_CSV) as f:
        reader = csv.reader(f)
        header = next(reader)
        d_start = header.index('d_1')
        for row in reader:
            if row[4] != TARGET_STORE or row[1] not in selected_ids:
                continue
            item_id = row[1]
            sales = np.asarray([int(x) for x in row[d_start:d_start + N_DAYS_RAW]],
                               dtype=np.int64)
            df = _build_sku_series(sales, cal, prices, item_id)
            if df is None:
                raise RuntimeError(f"Selected SKU {item_id} suddenly invalid")
            frames.append(df)
            per_sku_stats[item_id] = {
                'category':      _category_from_item(item_id),
                'bucket':        bucket_label[item_id],
                'mean_demand':   float(df['demand'].mean()),
                'std_demand':    float(df['demand'].std(ddof=0)),
                'median_demand': float(df['demand'].median()),
                'min_demand':    int(df['demand'].min()),
                'max_demand':    int(df['demand'].max()),
                'zero_fraction': float((df['demand'] == 0).mean()),
                'mean_price':    float(df['price'].mean()),
                'min_price':     float(df['price'].min()),
                'max_price':     float(df['price'].max()),
                'total_days':    int(len(df)),
            }

    if len(frames) != 30:
        raise RuntimeError(f"Expected 30 SKU frames, got {len(frames)}")

    long_df = pd.concat(frames, ignore_index=True)
    long_df.to_csv(OUT_CSV, index=False)
    print(f"  wrote {OUT_CSV}  rows={len(long_df):,}")

    summary = {
        'source_file':      os.path.basename(SALES_CSV),
        'store':            TARGET_STORE,
        'n_skus':           30,
        'min_valid_days':   MIN_VALID_DAYS,
        'max_zero_frac':    MAX_ZERO_FRAC,
        'high_threshold':   HIGH_THRESHOLD,
        'low_threshold':    LOW_THRESHOLD,
        'selected_skus':    [r[0] for r in selected],
        'per_sku_stats':    per_sku_stats,
        'bucket_counts': {
            'high':   len(sel_high),
            'medium': len(sel_medium),
            'low':    len(sel_low),
        },
        'category_counts': {
            cat: sum(1 for r in selected if r[1] == cat)
            for cat in sorted({r[1] for r in selected})
        },
        'thresholds': {
            'high_threshold_mean_per_day': HIGH_THRESHOLD,
            'low_threshold_mean_per_day':  LOW_THRESHOLD,
            'notes': (
                "Original Phase 7 spec called for high>30/day and "
                "low<5/day. The CA_1 SKU population has only 4 items "
                "exceeding 30/day mean demand, so thresholds were "
                "scaled down to high>10 and low<2. This preserves the "
                "spec intent (three demand-volume regimes spanning the "
                "real-data distribution) while keeping each bucket "
                "populated enough to allow category-balanced selection."
            ),
        },
    }
    with open(OUT_JSON, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"  wrote {OUT_JSON}")
    print(f"\nCategory mix: {summary['category_counts']}")
    print(f"Bucket mix  : {summary['bucket_counts']}")
    return summary


if __name__ == '__main__':
    run()
