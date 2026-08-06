"""
Holiday-aware demand signal for supply chain environment.

Provides holiday detection and demand multiplier calculation for US and Indian
holidays. Multipliers influence demand without hardcoding spike values.

NOTE: date_index is treated as days offset from 2024-01-01 (the env's epoch).
"""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple


US_HOLIDAYS = {
    'new_year':     (1,  1),
    'independence': (7,  4),
    'veterans':     (11, 11),
    'christmas':    (12, 25),
}

INDIA_HOLIDAYS = {
    'republic_day':    (1,  26),
    'independence':    (8,  15),
    'gandhi_jayanti':  (10,  2),
}

HOLIDAY_SEASONS = {
    'us_thanksgiving':  (11, 20, 30),
    'christmas_season': (12, 15, 31),
    'new_year_season':  (1,   1,  7),
    'india_diwali':     (10, 15, 11, 15),
}


def _get_date_from_index(date_index: int, base_year: int = 2024) -> datetime:
    base_date = datetime(base_year, 1, 1)
    return base_date + timedelta(days=int(date_index))


def _is_us_floating_holiday(dt: datetime) -> bool:
    month, day, weekday = dt.month, dt.day, dt.weekday()
    if month == 5 and weekday == 0 and day >= 25:   # Memorial Day
        return True
    if month == 9 and weekday == 0 and day <= 7:    # Labor Day
        return True
    if month == 11 and weekday == 3 and 22 <= day <= 28:  # Thanksgiving
        return True
    return False


def _is_holiday(dt: datetime) -> bool:
    month, day = dt.month, dt.day
    for hm, hd in US_HOLIDAYS.values():
        if month == hm and day == hd:
            return True
    for hm, hd in INDIA_HOLIDAYS.values():
        if month == hm and day == hd:
            return True
    if _is_us_floating_holiday(dt):
        return True
    for season_range in HOLIDAY_SEASONS.values():
        if len(season_range) == 3:
            s_month, s_start, s_end = season_range
            if month == s_month and s_start <= day <= s_end:
                return True
        elif len(season_range) == 4:
            s_month1, s_start, s_month2, s_end = season_range
            if (month == s_month1 and day >= s_start) or \
               (month == s_month2 and day <= s_end):
                return True
    return False


def _days_to_next_holiday(dt: datetime, max_lookahead: int = 14) -> int:
    for i in range(max_lookahead + 1):
        if _is_holiday(dt + timedelta(days=i)):
            return i
    return max_lookahead + 1


def get_holiday_multiplier(date_index: int, base_multiplier: float = 1.0) -> float:
    """Return a demand multiplier based on holiday proximity.

    Returns 2.0 on holidays, 1.2–1.5 in the 7 days before, 1.0 otherwise.
    """
    dt = _get_date_from_index(date_index)
    if _is_holiday(dt):
        return base_multiplier * 2.0
    days_to_holiday = _days_to_next_holiday(dt, max_lookahead=7)
    if days_to_holiday <= 7:
        proximity_factor = 1.0 - (days_to_holiday / 7.0)
        multiplier_boost = 0.2 + (0.3 * proximity_factor)
        return base_multiplier * (1.0 + multiplier_boost)
    return base_multiplier


def is_holiday_near(date_index: int, lookahead_days: int = 7) -> float:
    """Continuous signal from 0.0 (no holiday) to 1.0 (holiday today)."""
    dt = _get_date_from_index(date_index)
    if _is_holiday(dt):
        return 1.0
    days_to_holiday = _days_to_next_holiday(dt, max_lookahead=lookahead_days)
    if days_to_holiday <= lookahead_days:
        return 1.0 - (days_to_holiday / (lookahead_days + 1))
    return 0.0


def get_holiday_state_vector(date_index: int) -> np.ndarray:
    """Three-element feature vector: [is_holiday_near, multiplier_normalized, days_norm]."""
    multiplier = get_holiday_multiplier(date_index)
    holiday_near = is_holiday_near(date_index)
    dt = _get_date_from_index(date_index)
    days_to_holiday = _days_to_next_holiday(dt, max_lookahead=14)
    days_normalized = 1.0 - min(days_to_holiday / 14.0, 1.0)
    return np.array([holiday_near, (multiplier - 1.0), days_normalized], dtype=np.float32)
