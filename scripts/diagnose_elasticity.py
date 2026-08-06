#!/usr/bin/env python3
"""Why did the fitted elasticity beat the textbook prior on only 3 SKUs in 30?

    python3 scripts/diagnose_elasticity.py

Runs the same M5 backtest under a ladder of estimators, each adding one thing,
so the improvement can be attributed rather than guessed at. Held-out MAE on
the last 30% of weeks is the score; nothing here is tuned on it.

  A  prior only              the textbook -1.8, ignores this SKU entirely
  B  current                 within-month demeaning, per-SKU, shrunk to a global prior
  C  + daily not weekly      weekly aggregation may be destroying the variation
  D  + outlier trimming      promo spikes are leverage points in a log-log fit
  E  + partial pooling       shrink to the CATEGORY mean, not a global constant
  F  + pooled slope          one slope per category, no per-SKU slope at all
"""
from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "erp-api"))

from app.domain.pricing import PRIOR_ELASTICITY, demand_at  # noqa: E402

CSV = ROOT / "demo" / "data" / "m5_multi_sku.csv"
TRAIN_FRAC = 0.70
SHRINK = 8.0


def load_daily():
    rows = defaultdict(list)
    with open(CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                d, p, q = int(r["day"]), float(r["price"] or 0), float(r["demand"] or 0)
            except (ValueError, KeyError):
                continue
            if p > 0:
                rows[r["sku_id"]].append((d, p, q, int(r.get("dow") or 0)))
    return {k: sorted(v) for k, v in rows.items() if len(v) > 200}


def category_of(sku: str) -> str:
    return sku.split("_")[0]          # FOODS / HOBBIES / HOUSEHOLD


def to_weekly(daily):
    agg = defaultdict(lambda: {"px": [], "q": 0.0})
    for d, p, q, _ in daily:
        b = agg[d // 7]
        b["px"].append(p)
        b["q"] += q
    return [(w, sum(b["px"]) / len(b["px"]), b["q"]) for w, b in sorted(agg.items())]


def ols(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    sxx = sum(x * x for x in xs)
    if sxx <= 1e-12:
        return None
    return sum(x * y for x, y in zip(xs, ys)) / sxx


def demeaned(points, period_of, trim=0.0):
    """points: (period, price, units) -> within-period log deviations."""
    by = defaultdict(list)
    for per, p, q in points:
        if p > 0 and q > 0:
            by[per].append((p, q))
    xs, ys = [], []
    for rows in by.values():
        if len(rows) < 2:
            continue
        lp = [math.log(p) for p, _ in rows]
        lq = [math.log(q) for _, q in rows]
        mp, mq = sum(lp) / len(lp), sum(lq) / len(lq)
        xs += [v - mp for v in lp]
        ys += [v - mq for v in lq]
    if trim and len(xs) > 20:
        # A promo week is a huge price move AND a huge volume move; in log-log
        # it is a leverage point that can swing the slope on its own.
        keep = sorted(range(len(xs)), key=lambda i: abs(xs[i]))
        keep = keep[:max(10, int(len(keep) * (1 - trim)))]
        xs = [xs[i] for i in keep]
        ys = [ys[i] for i in keep]
    return xs, ys


def fit(xs, ys, prior):
    raw = ols(xs, ys)
    if raw is None or raw >= 0:
        return prior, 0
    n = len(xs)
    w = n / (n + SHRINK)
    return max(-4.0, min(-0.2, w * raw + (1 - w) * prior)), n


def score(series, elasticity):
    cut = int(len(series) * TRAIN_FRAC)
    train, test = series[:cut], series[cut:]
    if len(test) < 5:
        return None
    bp = statistics.median([p for _, p, _ in train])
    bu = statistics.median([u for _, _, u in train])
    if bp <= 0 or bu <= 0:
        return None
    err = sum(abs(demand_at(bu, bp, p, elasticity) - u) for _, p, u in test)
    flat = sum(abs(bu - u) for _, _, u in test)
    return err / len(test), flat / len(test)


def main() -> int:
    daily = load_daily()
    weekly = {k: to_weekly(v) for k, v in daily.items()}
    cats = {k: category_of(k) for k in weekly}
    print(f"M5: {len(weekly)} SKUs, {len(set(cats.values()))} categories\n")

    def run(label, elast_for, note=""):
        tot = totflat = totprior = 0.0
        beat_flat = beat_prior = 0
        n = 0
        for sku, series in weekly.items():
            e = elast_for(sku, series)
            s = score(series, e)
            sp = score(series, PRIOR_ELASTICITY)
            if not s or not sp:
                continue
            n += 1
            tot += s[0]; totflat += s[1]; totprior += sp[0]
            if s[0] < s[1]:
                beat_flat += 1
            if s[0] < sp[0]:
                beat_prior += 1
        print(f"  {label:<28} MAE {tot:>7.0f}  vs flat {totflat:>7.0f}"
              f"  vs prior {totprior:>7.0f}   beat flat {beat_flat:>2}/{n}"
              f"   beat prior {beat_prior:>2}/{n}   {note}")
        return tot

    # ── A: prior only ──
    run("A prior only (-1.8)", lambda s, ser: PRIOR_ELASTICITY)

    # ── B: current production estimator ──
    cache_b = {}
    def est_b(sku, series):
        if sku not in cache_b:
            cut = int(len(series) * TRAIN_FRAC)
            xs, ys = demeaned([(w // 4, p, q) for w, p, q in series[:cut]], None)
            cache_b[sku] = fit(xs, ys, PRIOR_ELASTICITY)[0]
        return cache_b[sku]
    run("B current (weekly, month FE)", est_b)

    # ── C: daily, with day-of-week as part of the period ──
    cache_c = {}
    def est_c(sku, series):
        if sku not in cache_c:
            d = daily[sku]
            cut = int(len(d) * TRAIN_FRAC)
            pts = [((day // 28, dow), p, q) for day, p, q, dow in d[:cut] if q > 0]
            xs, ys = demeaned(pts, None)
            cache_c[sku] = fit(xs, ys, PRIOR_ELASTICITY)[0]
        return cache_c[sku]
    run("C daily + month x dow FE", est_c)

    # ── D: C plus trimming the biggest price moves ──
    cache_d = {}
    def est_d(sku, series):
        if sku not in cache_d:
            d = daily[sku]
            cut = int(len(d) * TRAIN_FRAC)
            pts = [((day // 28, dow), p, q) for day, p, q, dow in d[:cut] if q > 0]
            xs, ys = demeaned(pts, None, trim=0.05)
            cache_d[sku] = fit(xs, ys, PRIOR_ELASTICITY)[0]
        return cache_d[sku]
    run("D  + trim 5% leverage", est_d)

    # ── E: shrink to the CATEGORY mean rather than a global constant ──
    per_sku_raw = {}
    for sku in weekly:
        d = daily[sku]
        cut = int(len(d) * TRAIN_FRAC)
        pts = [((day // 28, dow), p, q) for day, p, q, dow in d[:cut] if q > 0]
        xs, ys = demeaned(pts, None, trim=0.05)
        raw = ols(xs, ys)
        per_sku_raw[sku] = (raw if (raw is not None and raw < 0) else None, len(xs))

    cat_mean = {}
    for cat in set(cats.values()):
        vals = [r for s, (r, _) in per_sku_raw.items() if cats[s] == cat and r is not None]
        cat_mean[cat] = statistics.median(vals) if len(vals) >= 3 else PRIOR_ELASTICITY

    def est_e(sku, series):
        raw, n = per_sku_raw[sku]
        prior = cat_mean[cats[sku]]
        if raw is None:
            return prior
        w = n / (n + SHRINK)
        return max(-4.0, min(-0.2, w * raw + (1 - w) * prior))
    run("E  + shrink to category", est_e,
        note="cat medians " + ", ".join(f"{c}:{v:.2f}" for c, v in sorted(cat_mean.items())))

    # ── F: no per-SKU slope at all, just the category ──
    run("F category slope only", lambda s, ser: cat_mean[cats[s]])

    print("\nThe question is whether ANY per-SKU signal survives, or whether the")
    print("honest answer is one elasticity per category.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
