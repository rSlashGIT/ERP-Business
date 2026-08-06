#!/usr/bin/env python3
"""
End-to-end SmartStock validation against a real retail dataset.

    ingest -> forecast -> reorder calculation -> policy output -> verify

Runs the PRODUCTION code paths, not a reimplementation: the same
core.forecast, core.policy and core.recommend.generate that the FastAPI
service and the nightly Celery task call.

    python3 scripts/validate_dataset.py --csv demo/data/m5_multi_sku.csv \
        --sku-col sku_id --date-col date --qty-col demand --price-col price

Column names are arguments, so any retail extract works. Auto-detection covers
the common spellings when the flags are omitted.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "smartstock"))

from smartstock.contracts import (  # noqa: E402
    OrderPolicyConstraints, ReplenishmentRequest, SkuNodeState, SupplierRef,
)
from smartstock.core import policy as P  # noqa: E402
from smartstock.core.forecast import forecast_for  # noqa: E402
from smartstock.core.leadtime import fit_profile  # noqa: E402
from smartstock.core.recommend import ENGINE_VERSION, PolicyStore, generate  # noqa: E402
from smartstock.core.segmentation import SegmentIndex, build_stats  # noqa: E402

FAILURES: List[str] = []
WARNINGS: List[str] = []


def check(name: str, cond: bool, extra: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)
    return cond


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"  WARN  {msg}")


# ───────────────────────────── 1. INGEST ─────────────────────────────

SYNONYMS = {
    "sku": ["sku_id", "sku", "item_id", "item", "product_id", "product", "article", "stockcode"],
    "date": ["date", "day", "ds", "invoicedate", "order_date", "timestamp"],
    "qty": ["demand", "qty", "quantity", "units", "sales", "sold", "y", "order_qty"],
    "price": ["price", "sell_price", "unit_price", "unitprice", "amount", "rate"],
}


def autodetect(header: Sequence[str]) -> Dict[str, Optional[str]]:
    low = {h.lower().strip(): h for h in header}
    out: Dict[str, Optional[str]] = {}
    for field, opts in SYNONYMS.items():
        out[field] = next((low[o] for o in opts if o in low), None)
    return out


def ingest(path: Path, cols: Dict[str, Optional[str]],
           max_skus: Optional[int] = None) -> Tuple[Dict[str, List[float]], Dict[str, float], Dict[str, Any]]:
    """Read a retail CSV into per-SKU daily demand series and mean price.

    Deliberately tolerant: real extracts contain blank rows, thousands
    separators, currency symbols, negative quantities (returns) and duplicate
    (sku, date) rows. Every one of those is handled and COUNTED, because a
    silent drop is how an ingest quietly loses 8% of a catalogue.
    """
    if not path.exists():
        raise SystemExit(f"dataset not found: {path}")

    stats = {"rows": 0, "skipped_blank": 0, "skipped_badqty": 0, "skipped_baddate": 0,
             "negative_qty": 0, "duplicate_keys": 0, "skus_raw": 0}
    daily: Dict[str, Dict[str, float]] = defaultdict(dict)
    prices: Dict[str, List[float]] = defaultdict(list)

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        for f in ("sku", "date", "qty"):
            if not cols.get(f):
                raise SystemExit(f"could not resolve the '{f}' column; header was {header}")
        for row in reader:
            stats["rows"] += 1
            sku = (row.get(cols["sku"]) or "").strip()
            raw_date = (row.get(cols["date"]) or "").strip()
            raw_qty = (row.get(cols["qty"]) or "").strip()
            if not sku or not raw_date or raw_qty == "":
                stats["skipped_blank"] += 1
                continue
            try:
                qty = float(str(raw_qty).replace(",", "").replace("₹", "").replace("$", ""))
            except ValueError:
                stats["skipped_badqty"] += 1
                continue
            if not math.isfinite(qty):
                stats["skipped_badqty"] += 1
                continue
            if qty < 0:
                # A return, not demand. Clamp to zero rather than dropping: the
                # day still happened and dropping it corrupts the date index.
                stats["negative_qty"] += 1
                qty = 0.0
            key = raw_date[:10]
            if key in daily[sku]:
                stats["duplicate_keys"] += 1
                daily[sku][key] += qty           # same SKU twice in a day = one day's demand
            else:
                daily[sku][key] = qty
            if cols.get("price"):
                try:
                    p = float(str(row.get(cols["price"]) or "").replace(",", "").replace("$", ""))
                    if math.isfinite(p) and p > 0:
                        prices[sku].append(p)
                except ValueError:
                    pass

    stats["skus_raw"] = len(daily)
    series: Dict[str, List[float]] = {}
    for sku, byday in daily.items():
        ordered = [byday[d] for d in sorted(byday)]
        series[sku] = ordered
    if max_skus:
        series = dict(sorted(series.items(), key=lambda kv: -sum(kv[1]))[:max_skus])
    unit_price = {s: (float(np.mean(prices[s])) if prices.get(s) else 1.0) for s in series}
    return series, unit_price, stats


# ───────────────────────────── main ─────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--sku-col"); ap.add_argument("--date-col")
    ap.add_argument("--qty-col"); ap.add_argument("--price-col")
    ap.add_argument("--single-sku", default=None,
                    help="dataset has no SKU column; treat the whole file as this SKU")
    ap.add_argument("--max-skus", type=int, default=None)
    ap.add_argument("--label", default=None)
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.is_absolute():
        path = ROOT / path
    label = args.label or path.name

    print("=" * 74)
    print(f" SmartStock end-to-end validation — {label}")
    print(f" engine {ENGINE_VERSION}")
    print("=" * 74)

    # ---------- STAGE 1: INGEST ----------
    print("\n[1/5] INGEST")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh))
    cols = autodetect(header)
    for k, v in (("sku", args.sku_col), ("date", args.date_col),
                 ("qty", args.qty_col), ("price", args.price_col)):
        if v:
            cols[k] = v
    if args.single_sku and not cols.get("sku"):
        # Dataset is one product per file. Synthesise the column.
        tmp = ROOT / ".ingest_tmp.csv"
        with open(path, newline="", encoding="utf-8-sig") as src, open(tmp, "w", newline="") as dst:
            r = csv.DictReader(src)
            w = csv.DictWriter(dst, fieldnames=["__sku"] + (r.fieldnames or []))
            w.writeheader()
            for row in r:
                row["__sku"] = args.single_sku
                w.writerow(row)
        path = tmp
        cols["sku"] = "__sku"
    print(f"  header    : {header[:9]}{' …' if len(header) > 9 else ''}")
    print(f"  resolved  : sku={cols['sku']!r} date={cols['date']!r} qty={cols['qty']!r} price={cols['price']!r}")

    series, unit_price, st = ingest(path, cols, args.max_skus)
    if (ROOT / ".ingest_tmp.csv").exists():
        (ROOT / ".ingest_tmp.csv").unlink()

    total_units = sum(sum(v) for v in series.values())
    lens = [len(v) for v in series.values()]
    print(f"  rows read : {st['rows']:,}")
    print(f"  SKUs      : {len(series):,}   days/SKU min={min(lens)} max={max(lens)}")
    print(f"  units     : {total_units:,.0f}")
    print(f"  data hygiene: blank={st['skipped_blank']} bad_qty={st['skipped_badqty']} "
          f"negative={st['negative_qty']} duplicate_day_keys={st['duplicate_keys']}")
    check("ingest produced at least one SKU", len(series) > 0)
    check("ingest produced usable history (>=60 days for some SKU)", max(lens) >= 60, f"max={max(lens)}")
    check("no NaN survived ingest",
          all(np.all(np.isfinite(v)) for v in series.values()))
    check("no negative demand survived ingest",
          all(min(v) >= 0 for v in series.values()))
    dropped = st["skipped_blank"] + st["skipped_badqty"] + st["skipped_baddate"]
    if dropped > st["rows"] * 0.05:
        warn(f"{dropped/max(st['rows'],1):.1%} of rows dropped during ingest — inspect the source")

    usable = {s: v for s, v in series.items() if len(v) >= 60}
    check("at least one SKU has enough history to forecast", len(usable) > 0, f"{len(usable)} SKUs")
    if not usable:
        return 1

    # ---------- STAGE 2: SEGMENT + FORECAST ----------
    print("\n[2/5] FORECAST  (production core.forecast, real models)")
    stats_map = build_stats({s: np.asarray(v) for s, v in usable.items()})
    idx = SegmentIndex(stats_map)
    print(f"  segments  : {idx.describe()}")

    HOLD = min(60, max(14, min(len(v) for v in usable.values()) // 5))
    fc_rows: Dict[str, Tuple[float, float, str]] = {}
    ae_model = ae_naive = n_pts = 0.0
    for s, v in usable.items():
        y = np.asarray(v, dtype=float)
        mu, sd, model = forecast_for(y[:-HOLD], horizon=1)
        fc_rows[s] = (mu, sd, model)
        for t in range(len(y) - HOLD, len(y)):
            m, _, _ = forecast_for(y[:t], horizon=1)
            ae_model += abs(y[t] - m)
            ae_naive += abs(y[t] - float(np.mean(y[max(0, t - 28):t])))
            n_pts += 1
    mae_model, mae_naive = ae_model / n_pts, ae_naive / n_pts
    models_used = sorted({m for _, _, m in fc_rows.values()})
    print(f"  backtest  : {int(n_pts):,} one-step forecasts over the last {HOLD} days/SKU")
    print(f"  models    : {models_used}")
    print(f"  MAE       : SmartStock {mae_model:.3f}   28-day mean {mae_naive:.3f}   "
          f"({(1-mae_model/mae_naive)*100:+.1f}%)")
    check("every SKU produced a finite forecast",
          all(math.isfinite(m) and math.isfinite(s_) for m, s_, _ in fc_rows.values()))
    check("no forecast is negative", all(m >= 0 for m, _, _ in fc_rows.values()))
    check("sigma is strictly positive where demand varies",
          all(s_ > 0 for s, (m, s_, _) in fc_rows.items() if np.std(usable[s]) > 0.5))
    # Beating a naive mean is a property of the DATA, not of the code. On a
    # series with no autocorrelation (white noise) the mean IS the optimal
    # one-step predictor and no model can beat it -- so gate this check on the
    # data actually containing signal, and report the autocorrelation either
    # way rather than silently passing.
    acs = []
    for v in usable.values():
        y = np.asarray(v, dtype=float)
        if y.size > 30 and y.std() > 1e-9:
            acs.append(float(np.corrcoef(y[:-1], y[1:])[0, 1]))
    lag1 = float(np.nanmean(acs)) if acs else 0.0
    print(f"  lag-1 autocorrelation: {lag1:+.3f}"
          f"  ({'signal present' if lag1 > 0.05 else 'no exploitable signal'})")
    if lag1 > 0.05:
        check("forecast beats the naive 28-day mean on this dataset",
              mae_model <= mae_naive, f"{mae_model:.3f} vs {mae_naive:.3f}")
    else:
        check("forecast is within 15% of the naive mean on a signal-free series",
              mae_model <= mae_naive * 1.15, f"{mae_model:.3f} vs {mae_naive:.3f}")
        warn("dataset has no exploitable autocorrelation; forecast accuracy is "
             "bounded by the data, not by the model")
    check("real model names reported (not 'mock'/'stub')",
          all("mock" not in m and "stub" not in m for m in models_used), str(models_used))

    # ---------- STAGE 3: REORDER CALCULATION ----------
    print("\n[3/5] REORDER CALCULATION  (production core.policy)")
    lead_obs = {"FAST": [3, 4, 3, 5, 4, 3, 6, 4], "SLOW": [12, 18, 14, 25, 16, 30, 15, 19]}
    reorder: Dict[str, Dict[str, float]] = {}
    for i, (s, (mu, sd, _)) in enumerate(sorted(fc_rows.items())):
        tier = "FAST" if i % 2 == 0 else "SLOW"
        prof = fit_profile(tier, s, "DC-01", lead_obs[tier],
                           contract_days=4.0 if tier == "FAST" else 15.0)
        params = P.unpack(P.DEFAULT_RAW)[None, :]
        s_arr, S_arr, safety = P.target_levels(
            params, np.array([mu]), np.array([sd]),
            np.array([prof.mean_days]), np.array([prof.std_days]), review_period=1)
        reorder[s] = {"s": float(s_arr[0]), "S": float(S_arr[0]),
                      "safety": float(safety[0]), "lt_mean": prof.mean_days,
                      "lt_std": prof.std_days, "tier": tier, "d_hat": mu, "sigma": sd}
    ex = sorted(reorder.items(), key=lambda kv: -kv[1]["d_hat"])[:3]
    for s, r in ex:
        print(f"  {s:<18} d={r['d_hat']:6.2f}/day  LT={r['lt_mean']:5.1f}±{r['lt_std']:.1f}  "
              f"s={r['s']:8.1f}  S={r['S']:8.1f}  safety={r['safety']:7.1f}")
    check("reorder point exceeds lead-time demand for every SKU",
          all(r["s"] >= r["d_hat"] * r["lt_mean"] - 1e-6 for r in reorder.values()))
    check("order-up-to strictly exceeds the reorder point",
          all(r["S"] > r["s"] for r in reorder.values() if r["d_hat"] > 0))
    check("safety stock is non-negative", all(r["safety"] >= 0 for r in reorder.values()))
    # Days-of-cover is meaningless when d_hat ~ 0, so exclude those SKUs from
    # the comparison rather than letting a 1e-9 denominator produce 4e8 days.
    fast = [r["safety"] / r["d_hat"] for r in reorder.values()
            if r["tier"] == "FAST" and r["d_hat"] > 0.05]
    slow = [r["safety"] / r["d_hat"] for r in reorder.values()
            if r["tier"] == "SLOW" and r["d_hat"] > 0.05]
    if fast and slow:
        check("unreliable supplier gets more safety stock (days of cover)",
              np.mean(slow) > np.mean(fast),
              f"slow={np.mean(slow):.1f}d fast={np.mean(fast):.1f}d")

    # ---------- STAGE 4: POLICY OUTPUT ----------
    print("\n[4/5] POLICY OUTPUT  (production core.recommend.generate)")
    rng = np.random.default_rng(4)
    items = []
    # Deterministically place the highest-demand SKU below its reorder point so
    # the stage exercises the ordering path on ANY dataset, including a
    # single-SKU file. Randomising every SKU and hoping one triggers made this
    # stage a coin flip at small N.
    forced_low = max(reorder, key=lambda k: reorder[k]["d_hat"])
    for s, v in usable.items():
        r = reorder[s]
        if s == forced_low:
            on_hand = float(max(0.0, r["s"] * 0.10))
        else:
            on_hand = float(max(0.0, r["s"] * rng.uniform(0.05, 1.6)))
        items.append(SkuNodeState(
            sku_id=s, node_id="DC-01", on_hand=on_hand, on_order=0.0,
            unit_cost=round(unit_price[s] * 0.6, 4), unit_price=round(unit_price[s], 4),
            demand_history=list(v),
            supplier=SupplierRef(supplier_id=r["tier"], name=f"{r['tier']} supplier",
                                 contract_lead_days=r["lt_mean"]),
            lead_time_observations=lead_obs[r["tier"]],
            constraints=OrderPolicyConstraints(moq=0.0, order_multiple=1.0)))
    resp = generate(ReplenishmentRequest(run_id="validate", as_of_date=date.today().isoformat(),
                                         items=items), PolicyStore())
    lines = [l for d in resp.draft_purchase_orders for l in d.lines]
    print(f"  {resp.stats['items_received']} states -> {resp.stats['lines_recommended']} lines, "
          f"{resp.stats['draft_po_count']} draft POs, value {resp.stats['total_value']:,.0f}, "
          f"{resp.stats['critical_lines']} critical, {resp.stats['items_held']} held")
    check("policy produced at least one draft PO", len(resp.draft_purchase_orders) > 0,
          f"forced {forced_low} to 10% of its reorder point")
    check("no SKU was skipped as malformed", resp.stats["items_skipped"] == 0, str(resp.skipped[:2]))
    check("every recommended quantity is a positive integer",
          all(isinstance(l.recommended_qty, int) and l.recommended_qty > 0 for l in lines))
    check("every line carries a rationale", all(l.rationale is not None for l in lines))
    check("every line carries a lead-time provenance",
          all(l.rationale.lead_time_source in {"empirical", "shrunk", "contract", "default"}
              for l in lines))
    check("confidence is a probability", all(0.0 <= l.confidence <= 1.0 for l in lines))
    check("POs are grouped by supplier",
          len({d.supplier_id for d in resp.draft_purchase_orders}) ==
          len(resp.draft_purchase_orders))
    check("line value equals qty x unit cost",
          all(abs(l.line_value - l.recommended_qty * l.unit_cost) < 0.02 for l in lines))

    # ---------- STAGE 5: VERIFY BEHAVIOUR ----------
    print("\n[5/5] VERIFY")
    below = [l for l in lines if l.rationale.inventory_position <= l.rationale.reorder_point]
    check("every ordered line was genuinely below its reorder point",
          len(below) == len(lines), f"{len(below)}/{len(lines)}")
    covered = [l for l in lines
               if l.rationale.days_of_cover_after >= l.rationale.days_of_cover_before]
    check("ordering increases days of cover", len(covered) == len(lines))
    crit = [l for l in lines if (l.urgency.value if hasattr(l.urgency, "value") else l.urgency) == "critical"]
    if crit:
        check("critical lines project a stockout inside the lead time",
              all(l.rationale.projected_stockout_day is None
                  or l.rationale.projected_stockout_day <= math.ceil(l.rationale.lead_time_mean_days)
                  for l in crit))
    # idempotence: same inputs, same outputs
    resp2 = generate(ReplenishmentRequest(run_id="validate2", as_of_date=date.today().isoformat(),
                                          items=items), PolicyStore())
    q1 = {(l.sku_id, l.node_id): l.recommended_qty for l in lines}
    q2 = {(l.sku_id, l.node_id): l.recommended_qty
          for d in resp2.draft_purchase_orders for l in d.lines}
    check("policy is deterministic for identical inputs", q1 == q2,
          f"{sum(1 for k in q1 if q1[k] != q2.get(k))} differ")
    # A fully-stocked catalogue must order nothing. On-hand is taken from the
    # order_up_to that generate() ITSELF reported, not from the `reorder` dict:
    # that dict was computed on truncated history for the backtest, so using it
    # here compared two different forecasts and produced a false failure.
    target_S = {(l.sku_id, l.node_id): l.rationale.order_up_to for l in lines}
    stocked = [SkuNodeState(sku_id=i.sku_id, node_id=i.node_id,
                            on_hand=max(target_S.get((i.sku_id, i.node_id),
                                                     reorder[i.sku_id]["S"]), 1.0) * 3,
                            on_order=0.0,
                            unit_cost=i.unit_cost, unit_price=i.unit_price,
                            demand_history=i.demand_history, supplier=i.supplier,
                            lead_time_observations=i.lead_time_observations) for i in items]
    resp3 = generate(ReplenishmentRequest(run_id="stocked", as_of_date=date.today().isoformat(),
                                          items=stocked), PolicyStore())
    check("a fully stocked catalogue orders nothing",
          resp3.stats["lines_recommended"] == 0, str(resp3.stats["lines_recommended"]))

    print("\n" + "=" * 74)
    if FAILURES:
        print(f" {len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
    else:
        print(f" ALL CHECKS PASSED — {label}")
    if WARNINGS:
        print(f" {len(WARNINGS)} warning(s): {'; '.join(WARNINGS)}")
    print("=" * 74)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
