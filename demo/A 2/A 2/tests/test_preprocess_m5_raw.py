"""Tests for src/preprocess_m5_raw.py — clean M5 preprocessing for
FOODS_3_090 at CA_1.

Run with:
    python -m pytest tests/test_preprocess_m5_raw.py -v

The preprocessing script must have been executed at least once so that
data/processed/m5_clean.csv exists; the tests below do not rebuild it.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


CLEAN_CSV = 'data/processed/m5_clean.csv'

EXPECTED_COLUMNS = [
    'day', 'original_day', 'date', 'demand', 'price', 'revenue',
    'dow', 'month', 'year', 'snap',
    'event_name', 'event_type', 'is_event', 'is_weekend',
]


def _load_clean() -> pd.DataFrame:
    assert os.path.exists(CLEAN_CSV), (
        f"{CLEAN_CSV} not found — run `python src/preprocess_m5_raw.py` first"
    )
    # event_name / event_type are real strings; treat empty as ''.
    return pd.read_csv(CLEAN_CSV, keep_default_na=False, na_values=[])


def test_output_file_exists_and_has_correct_columns():
    df = _load_clean()

    assert list(df.columns) == EXPECTED_COLUMNS, (
        f"column order mismatch:\n  got {list(df.columns)}\n  "
        f"want {EXPECTED_COLUMNS}"
    )

    # dtypes after CSV round-trip:
    #   day/original_day/dow/month/year → int
    #   demand → int (NO float)
    #   price/revenue → float
    #   snap/is_event/is_weekend → bool
    #   date/event_name/event_type → object (string)
    assert np.issubdtype(df['day'].dtype, np.integer)
    assert np.issubdtype(df['original_day'].dtype, np.integer)
    assert np.issubdtype(df['demand'].dtype, np.integer), (
        f"demand must be integer, got {df['demand'].dtype}"
    )
    assert np.issubdtype(df['dow'].dtype, np.integer)
    assert np.issubdtype(df['month'].dtype, np.integer)
    assert np.issubdtype(df['year'].dtype, np.integer)
    assert np.issubdtype(df['price'].dtype, np.floating)
    assert np.issubdtype(df['revenue'].dtype, np.floating)
    assert df['snap'].dtype == bool or df['snap'].dtype == np.bool_
    assert df['is_event'].dtype == bool or df['is_event'].dtype == np.bool_
    assert df['is_weekend'].dtype == bool or df['is_weekend'].dtype == np.bool_


def test_no_data_corruption():
    df = _load_clean()

    # No NaN anywhere (event_name/event_type empty strings are fine).
    assert df.notna().all().all(), "NaN present in m5_clean.csv"

    # Demand: integer, non-negative.
    assert np.issubdtype(df['demand'].dtype, np.integer)
    assert (df['demand'] >= 0).all(), "negative demand found"

    # Price: strictly positive.
    assert (df['price'] > 0).all(), "non-positive price found"

    # Dates: no duplicates, strictly increasing.
    parsed = pd.to_datetime(df['date'])
    assert parsed.is_unique, "duplicate dates"
    assert parsed.is_monotonic_increasing, "dates not strictly increasing"

    # Contiguous daily series (no gaps).
    diffs = parsed.diff().dropna().dt.days.unique()
    assert set(diffs.tolist()) == {1}, f"date gaps detected: {diffs}"


def test_dow_matches_date():
    df = _load_clean()
    for date_str, dow in zip(df['date'], df['dow']):
        expected = datetime.strptime(str(date_str), '%Y-%m-%d').weekday()
        assert int(dow) == expected, (
            f"dow mismatch: {date_str} → dow={dow} but expected {expected} "
            f"(Monday=0)"
        )
