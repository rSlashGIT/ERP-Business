#!/usr/bin/env python3
"""Clean preprocessing on RAW Kaggle M5 for FOODS_3_090 at CA_1.

Produces real integer demand and real dollar prices — no z-scoring, no
silent imputation. Replaces the legacy z-scored processed_data_m5.csv
with a round-trippable honest artifact:

    data/processed/m5_clean.csv           # one row per day
    data/processed/m5_clean_summary.json  # provenance + stats

This script does NOT touch any existing file, does NOT delete the
legacy processed CSV, and does NOT retrain any model. It is the first
half of the M5-truth-up project; downstream retraining is a separate
step the user approves manually.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


# ───────────────── paths ─────────────────

SALES_CSV    = 'data/m5/sales_train_evaluation.csv'
CALENDAR_CSV = 'data/m5/calendar.csv'
PRICES_CSV   = 'data/m5/sell_prices.csv'

OUT_DIR      = 'data/processed'
OUT_CSV      = os.path.join(OUT_DIR, 'm5_clean.csv')
OUT_JSON     = os.path.join(OUT_DIR, 'm5_clean_summary.json')

TARGET_ID       = 'FOODS_3_090_CA_1_evaluation'
TARGET_ITEM     = 'FOODS_3_090'
TARGET_STORE    = 'CA_1'
TARGET_STATE    = 'CA'
N_DAYS          = 1941

# M5 wday encodes Saturday=1, Sunday=2, Monday=3, ..., Friday=7.
# We convert to the standard Python weekday() convention: Monday=0..Sunday=6.
M5_WDAY_TO_DOW = {3: 0, 4: 1, 5: 2, 6: 3, 7: 4, 1: 5, 2: 6}
# Name → expected dow, used as a belt-and-braces cross-check.
WEEKDAY_NAME_TO_DOW = {
    'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
    'Friday': 4, 'Saturday': 5, 'Sunday': 6,
}


# ───────────────── step 1: load sales ─────────────────

def load_target_sales(sales_path: str = SALES_CSV,
                      target_id: str = TARGET_ID) -> np.ndarray:
    """Read the one row matching target_id and return d_1..d_1941 as int array."""
    with open(sales_path) as f:
        reader = csv.reader(f)
        header = next(reader)
        # d_1 starts at index 6 (after id, item_id, dept_id, cat_id, store_id, state_id).
        assert header[0] == 'id' and header[6] == 'd_1', \
            f"unexpected sales header: {header[:8]}"
        for row in reader:
            if row[0] == target_id:
                sales = np.asarray([int(x) for x in row[6:6 + N_DAYS]],
                                    dtype=np.int64)
                assert len(sales) == N_DAYS, \
                    f"expected {N_DAYS} days, got {len(sales)}"
                assert (sales >= 0).all(), "negative sales value(s)"
                return sales
    raise KeyError(f"target id {target_id!r} not found in {sales_path}")


# ───────────────── step 2: load calendar ─────────────────

def load_calendar(calendar_path: str = CALENDAR_CSV,
                  n_days: int = N_DAYS) -> pd.DataFrame:
    """Load calendar.csv and keep exactly the first n_days (d_1..d_n_days)."""
    cal = pd.read_csv(calendar_path)
    want = {f'd_{i}' for i in range(1, n_days + 1)}
    cal = cal[cal['d'].isin(want)].copy()
    cal['d_index'] = cal['d'].str.slice(2).astype(int)
    cal = cal.sort_values('d_index').reset_index(drop=True)
    assert len(cal) == n_days, f"calendar has {len(cal)} rows, want {n_days}"
    assert cal['d_index'].iloc[0] == 1 and cal['d_index'].iloc[-1] == n_days
    return cal


# ───────────────── step 3: load prices ─────────────────

def load_prices(prices_path: str = PRICES_CSV,
                store_id: str = TARGET_STORE,
                item_id: str = TARGET_ITEM) -> Dict[int, float]:
    """Return dict wm_yr_wk -> sell_price for the target SKU/store pair."""
    prices: Dict[int, float] = {}
    with open(prices_path) as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ['store_id', 'item_id', 'wm_yr_wk', 'sell_price'], \
            f"unexpected prices header: {header}"
        for row in reader:
            if row[0] == store_id and row[1] == item_id:
                prices[int(row[2])] = float(row[3])
    if not prices:
        raise KeyError(f"no prices for ({store_id}, {item_id})")
    return prices


# ───────────────── step 4–6: join, fill, trim ─────────────────

def build_day_frame(sales: np.ndarray,
                    calendar: pd.DataFrame,
                    prices: Dict[int, float]) -> Tuple[pd.DataFrame, Dict]:
    """Join sales + calendar + prices into one row-per-day frame and trim
    leading zero-demand / no-price prefix. Returns (frame, provenance)."""
    n = len(sales)
    dates    = calendar['date'].tolist()
    wm_weeks = calendar['wm_yr_wk'].tolist()
    m5_wdays = calendar['wday'].tolist()
    names    = calendar['weekday'].tolist()
    months   = calendar['month'].tolist()
    years    = calendar['year'].tolist()
    snaps    = calendar['snap_CA'].tolist()
    ev_names = calendar['event_name_1'].fillna('').tolist()
    ev_types = calendar['event_type_1'].fillna('').tolist()

    # 4 + 5. Join prices, back-fill the leading missing-price block with
    # the first available weekly price.
    raw_prices: List[float] = [prices.get(wm, float('nan')) for wm in wm_weeks]
    first_priced_idx = next(
        (i for i, p in enumerate(raw_prices) if not np.isnan(p)),
        None,
    )
    if first_priced_idx is None:
        raise RuntimeError("no priced days at all — cannot backward-fill")
    leading_missing = first_priced_idx
    first_price = raw_prices[first_priced_idx]
    filled_prices = [
        first_price if (i < first_priced_idx or np.isnan(p)) else p
        for i, p in enumerate(raw_prices)
    ]
    # Sanity: after leading-fill and with a continuous weekly price stream
    # from first_priced_idx onward, no NaN should remain. If it does (a gap
    # mid-series), forward-fill with the last seen price — documented.
    mid_gaps_filled = 0
    last_good = first_price
    for i, p in enumerate(filled_prices):
        if np.isnan(p):
            filled_prices[i] = last_good
            mid_gaps_filled += 1
        else:
            last_good = filled_prices[i]

    # 6. Trim leading days where demand is 0 OR price is unavailable in
    # the raw (pre-fill) feed. We start the usable series at the first day
    # with demand > 0 AND a real (not back-filled) price.
    trim_idx = 0
    for i in range(n):
        if sales[i] > 0 and not np.isnan(raw_prices[i]):
            trim_idx = i
            break
    else:
        raise RuntimeError("no day with demand>0 AND a real price")

    # dow conversion + cross-check against the name column
    dows: List[int] = []
    for i in range(n):
        dow = M5_WDAY_TO_DOW[int(m5_wdays[i])]
        expected = WEEKDAY_NAME_TO_DOW[names[i]]
        assert dow == expected, (
            f"dow mismatch at d_{i+1}: wday={m5_wdays[i]} → {dow}, "
            f"but weekday name is {names[i]} → {expected}"
        )
        dows.append(dow)

    # Slice every column to [trim_idx:].
    df = pd.DataFrame({
        'day':          np.arange(n - trim_idx, dtype=np.int64),
        'original_day': np.arange(trim_idx + 1, n + 1, dtype=np.int64),
        'date':         dates[trim_idx:],
        'demand':       sales[trim_idx:].astype(np.int64),
        'price':        np.asarray(filled_prices[trim_idx:], dtype=np.float64),
        'dow':          np.asarray(dows[trim_idx:], dtype=np.int64),
        'month':        np.asarray(months[trim_idx:], dtype=np.int64),
        'year':         np.asarray(years[trim_idx:], dtype=np.int64),
        'snap':         np.asarray(snaps[trim_idx:], dtype=bool),
        'event_name':   ev_names[trim_idx:],
        'event_type':   ev_types[trim_idx:],
    })
    df['revenue']    = df['demand'].astype(np.float64) * df['price']
    df['is_event']   = df['event_name'].astype(str).str.len() > 0
    df['is_weekend'] = df['dow'].isin([5, 6])

    # Final column order exactly as the spec demands.
    df = df[[
        'day', 'original_day', 'date', 'demand', 'price', 'revenue',
        'dow', 'month', 'year', 'snap',
        'event_name', 'event_type', 'is_event', 'is_weekend',
    ]]

    provenance = {
        'original_days':                 n,
        'trim_days':                     int(trim_idx),
        'final_days':                    int(n - trim_idx),
        'leading_missing_prices_filled': int(leading_missing),
        'mid_gap_prices_filled':         int(mid_gaps_filled),
    }
    return df, provenance


# ───────────────── verification ─────────────────

def verify(df: pd.DataFrame) -> None:
    """Hard assertions — raise if any invariant is violated."""
    assert df['demand'].dtype == np.int64, f"demand dtype = {df['demand'].dtype}"
    assert df['price'].dtype == np.float64, f"price dtype = {df['price'].dtype}"
    assert (df['demand'] >= 0).all(), "negative demand"
    assert (df['price']  >  0).all(), "non-positive price"
    assert df.notna().all().all(), "NaN somewhere in the frame"

    # dates strictly increasing + unique
    parsed = pd.to_datetime(df['date'])
    assert parsed.is_monotonic_increasing, "dates not increasing"
    assert parsed.is_unique, "duplicate dates"

    # dow, month, year must match the date column
    assert (parsed.dt.weekday.values == df['dow'].values).all(), \
        "dow does not match parsed date"
    assert (parsed.dt.month.values == df['month'].values).all(), \
        "month does not match parsed date"
    assert (parsed.dt.year.values == df['year'].values).all(), \
        "year does not match parsed date"

    # is_weekend must match dow ∈ {5,6}
    assert (df['is_weekend'].values == df['dow'].isin([5, 6]).values).all(), \
        "is_weekend does not match dow"

    # is_event must match event_name non-empty
    assert (df['is_event'].values
            == (df['event_name'].astype(str).str.len() > 0).values).all(), \
        "is_event does not match event_name"


# ───────────────── summary JSON ─────────────────

def build_summary(df: pd.DataFrame, provenance: Dict) -> Dict:
    dem = df['demand']
    pri = df['price']
    rev = df['revenue']
    # Top 10 events by count, ignoring empty '' rows.
    evs = df.loc[df['is_event'], 'event_name'].value_counts().head(10)
    top_events = [[str(name), int(cnt)] for name, cnt in evs.items()]
    return {
        'source_file': os.path.basename(SALES_CSV),
        'sku': {
            'id':       TARGET_ID,
            'item_id':  TARGET_ITEM,
            'store_id': TARGET_STORE,
            'state':    TARGET_STATE,
        },
        'preprocessing': provenance,
        'date_range': {
            'start': str(df['date'].iloc[0]),
            'end':   str(df['date'].iloc[-1]),
        },
        'demand': {
            'min':           int(dem.min()),
            'max':           int(dem.max()),
            'mean':          float(dem.mean()),
            'std':           float(dem.std(ddof=0)),
            'median':        float(dem.median()),
            'zero_count':    int((dem == 0).sum()),
            'zero_fraction': float((dem == 0).mean()),
        },
        'price': {
            'min':                float(pri.min()),
            'max':                float(pri.max()),
            'mean':               float(pri.mean()),
            'unique_price_count': int(pri.nunique()),
        },
        'revenue': {
            'total':      float(rev.sum()),
            'mean_daily': float(rev.mean()),
        },
        'snap': {
            'snap_days':     int(df['snap'].sum()),
            'snap_fraction': float(df['snap'].mean()),
        },
        'events': {
            'event_days':    int(df['is_event'].sum()),
            'top_10_events': top_events,
        },
    }


# ───────────────── CLI ─────────────────

def run() -> Dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    sales     = load_target_sales()
    calendar  = load_calendar()
    prices    = load_prices()
    df, prov  = build_day_frame(sales, calendar, prices)
    verify(df)
    summary   = build_summary(df, prov)

    # Cross-check: CSV row count equals summary.preprocessing.final_days.
    assert len(df) == summary['preprocessing']['final_days'], \
        "row count ≠ final_days"

    df.to_csv(OUT_CSV, index=False)
    with open(OUT_JSON, 'w') as f:
        json.dump(summary, f, indent=2)

    # Belt: reload and re-verify, so a dtype drift in to_csv/read_csv
    # round-trip fails here rather than downstream.
    reload = pd.read_csv(OUT_CSV)
    assert list(reload.columns) == list(df.columns), \
        "column order changed on round-trip"
    assert len(reload) == len(df), "row count changed on round-trip"

    dmin  = summary['demand']['min']
    dmax  = summary['demand']['max']
    dmean = summary['demand']['mean']
    dstd  = summary['demand']['std']
    zc    = summary['demand']['zero_count']
    zf    = summary['demand']['zero_fraction']
    pmin  = summary['price']['min']
    pmax  = summary['price']['max']
    pmean = summary['price']['mean']
    rev   = summary['revenue']['total']
    snapd = summary['snap']['snap_days']
    snapf = summary['snap']['snap_fraction']
    evd   = summary['events']['event_days']
    start = summary['date_range']['start']
    end   = summary['date_range']['end']

    print("=== M5 Clean Preprocessing Complete ===")
    print(f"Source: {TARGET_ITEM} at {TARGET_STORE}")
    print(f"Trimmed {prov['trim_days']} leading zero-demand days")
    print(f"Final rows: {prov['final_days']}")
    print(f"Date range: {start} to {end}")
    print(f"Demand: min={dmin}, max={dmax}, mean={dmean:.2f}, std={dstd:.2f}")
    print(f"Zero days: {zc} ({zf:.1%})")
    print(f"Price: ${pmin:.2f} - ${pmax:.2f} (mean ${pmean:.2f})")
    print(f"Revenue total: ${rev:,.2f}")
    print(f"SNAP days: {snapd} ({snapf:.1%})")
    print(f"Event days: {evd}")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_JSON}")
    return summary


if __name__ == '__main__':
    run()
