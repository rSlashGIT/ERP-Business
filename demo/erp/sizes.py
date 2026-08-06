"""Size sort keys.

Apparel mixes SCALES, not just sizes. A catalogue holds alpha sizes (S/M/L/XL),
waist or chest numbers (30/32/34, 38/40/42) and one-size items, and they must
not interleave in a report: sorting a shared number space produced
    S, 38, 40, M, 42, L, XL
because waist 40 collided with alpha M.

Each scale therefore occupies its own band:

      10- 100   alpha        XXS .. 5XL
         500    one-size     FREE / OS
    1000+n      numeric      waist/chest n  ->  1000 + n

Bands are 100 apart so a scale can grow without colliding, and numeric sizes
still sort among themselves by their own value.
"""
from __future__ import annotations

from typing import Optional

ALPHA = {
    "XXS": 10, "XS": 20, "S": 30, "M": 40, "L": 50, "XL": 60,
    "XXL": 70, "2XL": 70, "XXXL": 80, "3XL": 80, "4XL": 90, "5XL": 100,
}
ONE_SIZE = {"FREE": 500, "OS": 500, "ONE SIZE": 500}
NUMERIC_BASE = 1000


def size_seq(label: Optional[str]) -> Optional[int]:
    """Sort key for a size label, or None when the scale is unrecognised.

    None sorts LAST everywhere (callers use COALESCE(size_seq, 99999)), so an
    unknown label never masquerades as the smallest size.
    """
    if not label:
        return None
    key = str(label).strip().upper()
    if key in ALPHA:
        return ALPHA[key]
    if key in ONE_SIZE:
        return ONE_SIZE[key]
    if key.isdigit():
        return NUMERIC_BASE + int(key)
    return None
