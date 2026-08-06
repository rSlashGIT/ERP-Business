"""
SKU segmentation — the mechanism that makes SmartStock scale to 10k+ SKUs.

THE SCALING PROBLEM
-------------------
Legacy SmartStock optimised a 10-vector per SKU (multi_sku_network.py:
PARAMS_PER_SKU = 10, "Total parameters: N_SKUS x 10 = 300 dims"). That is
tractable at 30 SKUs and mathematically dead at 10_000: CMA-ES is O(n^2)
per generation with an O(n^3) eigen-decomposition, so n = 100_000 is not a
large problem, it is a different kind of problem. Worse, most SKUs do not
have enough demand signal to identify 10 free parameters — you would be
fitting noise per SKU.

THE FIX
-------
Optimise policy parameters per SEGMENT, and normalise the policy inputs by
each SKU's own demand statistics. Segment count is fixed (default 12), so
the CMA-ES dimension is constant as the catalogue grows from 30 to 100_000
SKUs. Per-SKU behaviour still differs, because the policy consumes per-SKU
forecast mean, forecast sigma, lead-time mean and lead-time sigma.

SEGMENTATION SCHEME
-------------------
Syntetos-Boylan-Croston demand classification on (ADI, CV^2), crossed with a
volume tercile:

    ADI  = average inter-demand interval = n_periods / n_nonzero_periods
    CV^2 = (std(nonzero demand) / mean(nonzero demand))^2

    ADI <  1.32 and CV2 <  0.49  -> SMOOTH        (easy, tight safety stock)
    ADI >= 1.32 and CV2 <  0.49  -> INTERMITTENT  (lumpy timing, stable size)
    ADI <  1.32 and CV2 >= 0.49  -> ERRATIC       (regular timing, wild size)
    ADI >= 1.32 and CV2 >= 0.49  -> LUMPY         (hardest; fat safety stock)

Cut-offs 1.32 / 0.49 are the standard Syntetos-Boylan values.

Crossed with volume tercile (LOW / MID / HIGH by mean daily units) that gives
4 x 3 = 12 segments. A high-volume SMOOTH SKU and a low-volume LUMPY SKU end
up under genuinely different policies, which is the behaviour we want, without
the parameter blow-up.

Segments with too few SKUs to identify parameters fall back to the parent
demand class, then to a global default — see `assign` and `SegmentIndex`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

ADI_CUT = 1.32
CV2_CUT = 0.49
MIN_SKUS_PER_SEGMENT = 3


class DemandClass(str, Enum):
    SMOOTH = "smooth"
    INTERMITTENT = "intermittent"
    ERRATIC = "erratic"
    LUMPY = "lumpy"


class VolumeBand(str, Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


ALL_SEGMENTS: Tuple[str, ...] = tuple(
    f"{d.value}:{v.value}" for d in DemandClass for v in VolumeBand
)
GLOBAL_SEGMENT = "global:default"


@dataclass(frozen=True)
class DemandStats:
    """Per-SKU demand descriptors. Computed once per replenishment run."""

    sku_id: str
    mean: float
    std: float
    adi: float
    cv2: float
    nonzero_frac: float
    n_periods: int
    demand_class: DemandClass
    volume_band: VolumeBand
    segment: str

    def to_dict(self) -> dict:
        return {
            "sku_id": self.sku_id,
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "adi": round(self.adi, 3),
            "cv2": round(self.cv2, 3),
            "nonzero_frac": round(self.nonzero_frac, 3),
            "n_periods": self.n_periods,
            "demand_class": self.demand_class.value,
            "volume_band": self.volume_band.value,
            "segment": self.segment,
        }


def classify_demand(history: Sequence[float]) -> Tuple[DemandClass, float, float]:
    """Return (class, ADI, CV^2) for one demand series.

    Edge cases handled explicitly because real ERP data is full of them:
      * empty series          -> LUMPY with ADI=inf proxy (max caution)
      * all-zero series       -> LUMPY (dead SKU; policy should order nothing,
                                 which falls out of d_hat = 0, not of the class)
      * single non-zero point -> CV2 = 0 (cannot estimate spread) but ADI is
                                 large, so it lands INTERMITTENT, not SMOOTH.
    """
    d = np.asarray(history, dtype=np.float64)
    d = d[np.isfinite(d)]
    n = d.size
    if n == 0:
        return DemandClass.LUMPY, 999.0, 999.0

    nz = d[d > 0]
    k = nz.size
    if k == 0:
        return DemandClass.LUMPY, 999.0, 0.0

    adi = float(n) / float(k)
    nz_mean = float(nz.mean())
    nz_std = float(nz.std(ddof=1)) if k > 1 else 0.0
    cv2 = float((nz_std / nz_mean) ** 2) if nz_mean > 0 else 0.0

    if adi < ADI_CUT and cv2 < CV2_CUT:
        cls = DemandClass.SMOOTH
    elif adi >= ADI_CUT and cv2 < CV2_CUT:
        cls = DemandClass.INTERMITTENT
    elif adi < ADI_CUT and cv2 >= CV2_CUT:
        cls = DemandClass.ERRATIC
    else:
        cls = DemandClass.LUMPY
    return cls, adi, cv2


def _volume_bands(means: np.ndarray) -> np.ndarray:
    """Tercile split on mean daily demand. Returns integer band 0/1/2."""
    if means.size == 0:
        return np.zeros(0, dtype=int)
    if means.size < 3 or np.allclose(means, means[0]):
        return np.ones(means.size, dtype=int)  # everything MID
    q1, q2 = np.quantile(means, [1.0 / 3.0, 2.0 / 3.0])
    band = np.zeros(means.size, dtype=int)
    band[means > q1] = 1
    band[means > q2] = 2
    return band


def build_stats(
    demand_by_sku: Dict[str, Sequence[float]],
) -> Dict[str, DemandStats]:
    """Compute DemandStats for every SKU. Deterministic given the same input."""
    if not demand_by_sku:
        return {}

    sku_ids = list(demand_by_sku.keys())
    means = np.array(
        [
            float(np.mean(demand_by_sku[s])) if len(demand_by_sku[s]) else 0.0
            for s in sku_ids
        ],
        dtype=np.float64,
    )
    bands = _volume_bands(means)
    band_enum = [VolumeBand.LOW, VolumeBand.MID, VolumeBand.HIGH]

    out: Dict[str, DemandStats] = {}
    for i, sid in enumerate(sku_ids):
        series = np.asarray(demand_by_sku[sid], dtype=np.float64)
        cls, adi, cv2 = classify_demand(series)
        vb = band_enum[int(bands[i])]
        nz_frac = float((series > 0).mean()) if series.size else 0.0
        out[sid] = DemandStats(
            sku_id=sid,
            mean=float(series.mean()) if series.size else 0.0,
            std=float(series.std(ddof=1)) if series.size > 1 else 0.0,
            adi=adi,
            cv2=cv2,
            nonzero_frac=nz_frac,
            n_periods=int(series.size),
            demand_class=cls,
            volume_band=vb,
            segment=f"{cls.value}:{vb.value}",
        )
    return out


class SegmentIndex:
    """Maps SKUs to a dense segment index for vectorised policy lookup.

    Only segments with >= MIN_SKUS_PER_SEGMENT members get their own parameter
    block. Thin segments are merged upward into their demand class (pooled
    across volume bands), and a demand class that is still too thin is merged
    into GLOBAL_SEGMENT. This prevents CMA-ES from burning dimensions fitting
    a segment that contains two SKUs.
    """

    def __init__(self, stats: Dict[str, DemandStats]) -> None:
        self.stats = stats
        counts: Dict[str, int] = {}
        for st in stats.values():
            counts[st.segment] = counts.get(st.segment, 0) + 1

        class_counts: Dict[str, int] = {}
        for st in stats.values():
            key = f"{st.demand_class.value}:*"
            class_counts[key] = class_counts.get(key, 0) + 1

        resolved: Dict[str, str] = {}
        for sid, st in stats.items():
            seg = st.segment
            if counts.get(seg, 0) >= MIN_SKUS_PER_SEGMENT:
                resolved[sid] = seg
                continue
            parent = f"{st.demand_class.value}:*"
            if class_counts.get(parent, 0) >= MIN_SKUS_PER_SEGMENT:
                resolved[sid] = parent
                continue
            resolved[sid] = GLOBAL_SEGMENT

        self.sku_to_segment: Dict[str, str] = resolved
        self.segments: List[str] = sorted(set(resolved.values()))
        self.segment_to_idx: Dict[str, int] = {s: i for i, s in enumerate(self.segments)}
        logger.info(
            "segmentation: %d SKUs -> %d active segments (%s)",
            len(stats), len(self.segments), ", ".join(self.segments),
        )

    @property
    def n_segments(self) -> int:
        return len(self.segments)

    def index_array(self, sku_order: Sequence[str]) -> np.ndarray:
        """Return (n_sku,) int array of segment indices, aligned to sku_order."""
        return np.array(
            [self.segment_to_idx[self.sku_to_segment[s]] for s in sku_order],
            dtype=np.int64,
        )

    def describe(self) -> Dict[str, int]:
        out: Dict[str, int] = {s: 0 for s in self.segments}
        for seg in self.sku_to_segment.values():
            out[seg] += 1
        return out
