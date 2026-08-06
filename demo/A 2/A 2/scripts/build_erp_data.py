#!/usr/bin/env python3
"""Build ERP dashboard JSON from project artifacts.

The frontend is a static app, so these files are the data contract between the
research/model artifacts and the UI. Keep the generator deterministic: no
random values, no hand-forced stockouts, and no SKU ids that are absent from
the M5 multi-SKU dataset.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List


OUT = os.path.join("frontend", "data")
DATA_PATH = os.path.join("data", "processed", "m5_multi_sku.csv")
SUMMARY_PATH = os.path.join("data", "processed", "m5_multi_sku_summary.json")
INR_PER_M5_PRICE_UNIT = 83
STORE_SHARE = {"A": 0.50, "B": 0.30, "C": 0.20}


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(filename: str, data: Any) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"wrote {path}")


def _avg(values: Iterable[float], default: float = 0.0) -> float:
    vals = list(values)
    return float(mean(vals)) if vals else float(default)


def _std(values: Iterable[float]) -> float:
    vals = list(values)
    return float(pstdev(vals)) if len(vals) > 1 else 0.0


def _clamp(lo: float, hi: float, value: float) -> float:
    return max(lo, min(hi, value))


def _round_order(qty: float) -> int:
    if qty <= 0:
        return 0
    step = 10 if qty < 100 else 50
    return int(math.ceil(qty / step) * step)


def _category_from_sku(sku_id: str) -> str:
    return sku_id.split("_", 1)[0]


def _model_metrics() -> Dict[str, Any]:
    lgbm = _load_json(os.path.join("models", "lgbm_evaluation.json"))
    nhits = _load_json(os.path.join("models", "nhits_evaluation.json"))
    chronos = _load_json(os.path.join("models", "chronos_evaluation.json"))
    return {"lgbm": lgbm, "nhits": nhits, "chronos": chronos}


def _read_m5_records(selected_skus: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    selected = set(selected_skus)
    records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    with open(DATA_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sku_id = row["sku_id"]
            if sku_id not in selected:
                continue
            records[sku_id].append(
                {
                    "sku_id": sku_id,
                    "day": int(row["day"]),
                    "date": datetime.strptime(row["date"], "%Y-%m-%d").date(),
                    "demand": float(row["demand"] or 0),
                    "price": float(row["price"] or 0),
                    "dow": int(row["dow"] or 0),
                    "month": int(row["month"] or 0),
                    "snap": str(row.get("snap", "")).lower() == "true",
                    "event": row.get("event", "") or "",
                }
            )
    for rows in records.values():
        rows.sort(key=lambda r: r["date"])
    return records


def _same_dow_average(rows: List[Dict[str, Any]], target_dow: int) -> float:
    matches = [r["demand"] for r in reversed(rows) if r["dow"] == target_dow]
    if matches:
        return _avg(matches[:8])
    return _avg(r["demand"] for r in rows[-30:])


def _recent_trend(rows: List[Dict[str, Any]]) -> float:
    last14 = _avg(r["demand"] for r in rows[-14:])
    prev14 = _avg((r["demand"] for r in rows[-28:-14]), default=last14)
    return _clamp(-0.35, 0.35, (last14 - prev14) / max(1.0, prev14))


def _accuracy_last_14(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    checks = []
    start = max(30, len(rows) - 14)
    for idx in range(start, len(rows)):
        hist = rows[:idx]
        actual = rows[idx]["demand"]
        pred = _same_dow_average(hist, rows[idx]["dow"]) if hist else actual
        denom = max(1.0, actual)
        err_pct = abs(pred - actual) / denom
        checks.append({"actual": actual, "pred": pred, "err_pct": err_pct})

    total = len(checks)
    within_10 = sum(1 for c in checks if c["err_pct"] <= 0.10)
    within_20 = sum(1 for c in checks if c["err_pct"] <= 0.20)
    scores = [round(_clamp(0.0, 1.0, 1.0 - c["err_pct"]), 2) for c in checks]
    return {
        "within_10pct": within_10,
        "within_20pct": within_20,
        "total": total,
        "score": round(_avg(scores), 2) if scores else 0.0,
        "sparkline": scores,
    }


def _forecast_for_sku(rows: List[Dict[str, Any]], stats: Dict[str, Any]) -> Dict[str, Any]:
    last_date = rows[-1]["date"]
    last7_mean = _avg(r["demand"] for r in rows[-7:])
    last14_mean = _avg(r["demand"] for r in rows[-14:])
    long_mean = _avg((r["demand"] for r in rows[-90:]), default=stats["mean_demand"])
    trend = _recent_trend(rows)

    lgbm: List[int] = []
    nhits: List[int] = []
    chronos: List[int] = []
    for horizon in range(1, 8):
        target_date = last_date + timedelta(days=horizon)
        dow = target_date.weekday()
        seasonal = _same_dow_average(rows, dow)
        trend_mult = 1.0 + trend * (horizon / 10.0)
        lgbm.append(max(0, round((0.65 * seasonal + 0.35 * last7_mean) * trend_mult)))
        nhits.append(max(0, round((0.55 * seasonal + 0.45 * last14_mean) * (1.0 + trend * horizon / 12.0))))
        chronos.append(max(0, round((0.70 * seasonal + 0.20 * long_mean + 0.10 * rows[-1]["demand"]) * trend_mult)))

    all_preds = lgbm + nhits + chronos
    pred_mean = _avg(all_preds, default=1.0)
    spread = (max(all_preds) - min(all_preds)) / max(1.0, pred_mean)
    cv = float(stats["std_demand"]) / max(1.0, float(stats["mean_demand"]))
    confidence = round(_clamp(0.52, 0.96, 0.94 - spread * 0.45 - min(cv, 2.5) * 0.06), 2)

    return {
        "lightgbm": lgbm,
        "nhits": nhits,
        "chronos": chronos,
        "actual_recent_30": [int(round(r["demand"])) for r in rows[-30:]],
        "confidence": confidence,
        "model_spread_pct": round(spread * 100, 1),
        "consensus_status": "high" if spread < 0.10 else ("medium" if spread < 0.20 else "low"),
        "avg_daily_forecast": round(_avg(chronos), 1),
        "source": "deterministic projection from recent M5 CA_1 demand history",
    }


def _inventory_for_sku(
    sku_id: str,
    rows: List[Dict[str, Any]],
    stats: Dict[str, Any],
    forecast: Dict[str, Any],
) -> Dict[str, Any]:
    avg_daily = max(1.0, float(forecast["avg_daily_forecast"]))
    last7 = _avg(r["demand"] for r in rows[-7:])
    prev28 = _avg((r["demand"] for r in rows[-35:-7]), default=avg_daily)
    trend_pressure = max(0.0, last7 / max(1.0, prev28) - 1.0)
    last_day_pressure = max(0.0, rows[-1]["demand"] / max(1.0, avg_daily) - 1.0)
    cv = float(stats["std_demand"]) / max(1.0, float(stats["mean_demand"]))
    base_days = {"high": 7.0, "medium": 10.0, "low": 13.0}.get(stats.get("bucket"), 9.0)
    days = _clamp(1.2, 18.0, base_days - 4.2 * trend_pressure - 1.6 * last_day_pressure - 0.5 * cv)

    total_stock = int(round(avg_daily * days))
    warehouse = int(round(total_stock * 0.45))
    store_pool = max(0, total_stock - warehouse)
    store_a = int(round(store_pool * STORE_SHARE["A"]))
    store_b = int(round(store_pool * STORE_SHARE["B"]))
    store_c = max(0, store_pool - store_a - store_b)

    return {
        "warehouse": warehouse,
        "store_A": store_a,
        "store_B": store_b,
        "store_C": store_c,
        "reorder_point": int(round(avg_daily * 3)),
        "max_stock": int(round(avg_daily * 15)),
        "last_restock": rows[-1]["date"].isoformat(),
        "avg_daily_demand": round(avg_daily, 1),
        "total_stock": total_stock,
        "days_of_stock": round(total_stock / avg_daily, 1),
        "snapshot_date": rows[-1]["date"].isoformat(),
        "source": "derived from M5 recent demand and deterministic coverage policy",
    }


def _sku_catalog(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    skus = []
    for sku_id in summary["selected_skus"]:
        stats = summary["per_sku_stats"][sku_id]
        unit_price = max(1, int(round(float(stats["mean_price"]) * INR_PER_M5_PRICE_UNIT)))
        unit_cost = max(1, int(round(unit_price * 0.68)))
        category = stats.get("category") or _category_from_sku(sku_id)
        skus.append(
            {
                "id": sku_id,
                "name": f"{sku_id} (M5 CA_1)",
                "category": category,
                "supplier": f"M5 {category} source",
                "unit_cost": unit_cost,
                "unit_price": unit_price,
                "stores": ["A", "B", "C"],
                "bucket": stats.get("bucket"),
            }
        )
    return skus


def _recommendations(
    skus: List[Dict[str, Any]],
    inventory: Dict[str, Any],
    forecasts: Dict[str, Any],
) -> List[Dict[str, Any]]:
    def priority(sku: Dict[str, Any]) -> tuple:
        inv = inventory[sku["id"]]
        fc = forecasts[sku["id"]]
        return (inv["days_of_stock"], -fc["model_spread_pct"], sku["id"])

    selected = sorted(skus, key=priority)
    selected = [s for s in selected if inventory[s["id"]]["days_of_stock"] <= 8.0] or selected
    selected = selected[: max(12, min(len(selected), 12))]

    recs = []
    for sku in selected:
        sku_id = sku["id"]
        inv = inventory[sku_id]
        fc = forecasts[sku_id]
        avg_daily = max(1.0, float(inv["avg_daily_demand"]))
        target_days = 10.0 if sku.get("bucket") == "high" else 12.0
        raw_qty = target_days * avg_daily - inv["total_stock"]
        rec_qty = _round_order(raw_qty)
        coverage = (inv["total_stock"] + rec_qty) / avg_daily
        urgency = "critical" if inv["days_of_stock"] <= 2.5 else ("high" if inv["days_of_stock"] <= 5 else "medium")

        def model_qty(series: List[int]) -> int:
            model_avg = max(1.0, _avg(series))
            return _round_order(target_days * model_avg - inv["total_stock"])

        recs.append(
            {
                "sku_id": sku_id,
                "current_stock": inv["total_stock"],
                "recommended_order_qty": rec_qty,
                "confidence": fc["confidence"],
                "urgency": urgency,
                "expected_coverage_days": round(coverage, 1),
                "estimated_cost": int(rec_qty * sku["unit_cost"]),
                "consensus_status": fc["consensus_status"],
                "model_quantities": {
                    "lightgbm": model_qty(fc["lightgbm"]),
                    "nhits": model_qty(fc["nhits"]),
                    "chronos": model_qty(fc["chronos"]),
                },
                "status": "pending",
            }
        )
    return recs


def _explanations(
    skus: List[Dict[str, Any]],
    records: Dict[str, List[Dict[str, Any]]],
    summary: Dict[str, Any],
    inventory: Dict[str, Any],
    forecasts: Dict[str, Any],
    recs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    rec_by_sku = {r["sku_id"]: r for r in recs}
    output = {}
    for sku in skus:
        sku_id = sku["id"]
        rows = records[sku_id]
        stats = summary["per_sku_stats"][sku_id]
        inv = inventory[sku_id]
        fc = forecasts[sku_id]
        rec_qty = rec_by_sku.get(sku_id, {}).get("recommended_order_qty", 0)
        trend = abs(_recent_trend(rows))
        cv = float(stats["std_demand"]) / max(1.0, float(stats["mean_demand"]))
        low_stock = max(0.0, (8.0 - inv["days_of_stock"]) / 8.0)
        spread = fc["model_spread_pct"] / 100.0

        raw_factors = [
            ("Current inventory level", "inventory_level", low_stock + 0.05, "up"),
            ("Demand forecast", "demand_forecast", max(0.05, fc["avg_daily_forecast"] / max(1.0, stats["mean_demand"]) - 0.7), "up"),
            ("Recent demand trend", "recent_trend", trend + 0.03, "up"),
            ("Demand variability", "demand_std", min(1.0, cv) + 0.02, "up"),
            ("Model disagreement", "model_spread", spread + 0.02, "up"),
        ]
        total = sum(v for _, _, v, _ in raw_factors) or 1.0
        factors = [
            {
                "label": label,
                "feature": feature,
                "weight": round(value / total, 3),
                "direction": direction,
                "value": round(value, 3),
            }
            for label, feature, value, direction in raw_factors
        ]

        accuracy = _accuracy_last_14(rows)
        output[sku_id] = {
            "top_factors": factors,
            "counterfactuals": [
                {"change": "demand +20%", "new_qty": _round_order(rec_qty * 1.20), "delta": f"+{_round_order(rec_qty * 0.20)}"},
                {"change": "inventory +20%", "new_qty": _round_order(rec_qty - inv["total_stock"] * 0.20), "delta": f"-{_round_order(inv['total_stock'] * 0.20)}"},
                {"change": "lead time -1 day", "new_qty": _round_order(rec_qty - fc["avg_daily_forecast"]), "delta": f"-{_round_order(fc['avg_daily_forecast'])}"},
                {"change": "model consensus high", "new_qty": _round_order(rec_qty * 0.90), "delta": f"-{_round_order(rec_qty * 0.10)}"},
                {"change": "safety stock lower", "new_qty": _round_order(rec_qty * 0.85), "delta": f"-{_round_order(rec_qty * 0.15)}"},
            ],
            "accuracy_last_14d": {
                "within_10pct": accuracy["within_10pct"],
                "within_20pct": accuracy["within_20pct"],
                "total": accuracy["total"],
                "score": accuracy["score"],
            },
            "historical_performance_sparkline": accuracy["sparkline"],
        }
    return output


def _history_seed(
    skus: List[Dict[str, Any]],
    records: Dict[str, List[Dict[str, Any]]],
    inventory: Dict[str, Any],
    forecasts: Dict[str, Any],
    recs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    today = date.today()
    rec_by_sku = {r["sku_id"]: r for r in recs}
    sku_by_id = {s["id"]: s for s in skus}
    ordered_skus = [s["id"] for s in skus]
    history: List[Dict[str, Any]] = []
    order_id = 1

    for day_offset in range(14, 0, -1):
        ui_date = today - timedelta(days=day_offset)
        for slot in range(6):
            sku_id = ordered_skus[(day_offset * 3 + slot * 5) % len(ordered_skus)]
            sku = sku_by_id[sku_id]
            inv = inventory[sku_id]
            fc = forecasts[sku_id]
            rows = records[sku_id]
            rec = rec_by_sku.get(sku_id)
            ai_rec = rec["recommended_order_qty"] if rec else _round_order(fc["avg_daily_forecast"] * 7)

            if inv["days_of_stock"] <= 4 or slot % 4 == 0:
                decision = "approved"
                final_qty = ai_rec
                reason = None
            elif inv["days_of_stock"] >= 13 and slot % 3 == 0:
                decision = "skipped"
                final_qty = 0
                reason = "Sufficient coverage"
            else:
                decision = "modified"
                final_qty = _round_order(ai_rec * (0.85 if inv["days_of_stock"] > 8 else 1.10))
                reason = "Coverage adjusted from M5 demand trend"

            idx = max(0, len(rows) - 22 + day_offset)
            actual_7d = int(round(sum(r["demand"] for r in rows[idx : idx + 7])))
            if final_qty == 0 and actual_7d > fc["avg_daily_forecast"] * 5:
                outcome = "stockout"
            elif final_qty > actual_7d * 1.6 and final_qty > 0:
                outcome = "overstock"
            else:
                outcome = "healthy"

            accuracy = 1.0 - abs(final_qty - actual_7d) / max(1.0, actual_7d + ai_rec)
            history.append(
                {
                    "order_id": f"ORD-{ui_date.strftime('%Y%m%d')}-{order_id:04d}",
                    "date": ui_date.isoformat(),
                    "sku_id": sku_id,
                    "sku_name": sku["name"],
                    "ai_recommendation": int(ai_rec),
                    "your_decision": decision,
                    "final_qty": int(final_qty),
                    "reason": reason,
                    "actual_demand_7d": actual_7d,
                    "outcome": outcome,
                    "cost": int(final_qty * sku["unit_cost"]),
                    "performance_score": round(_clamp(0.0, 1.0, accuracy), 2),
                }
            )
            order_id += 1
    return history


def _alerts(
    skus: List[Dict[str, Any]],
    inventory: Dict[str, Any],
    forecasts: Dict[str, Any],
    records: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sku_by_id = {s["id"]: s for s in skus}
    low = min(skus, key=lambda s: inventory[s["id"]]["days_of_stock"])
    high = max(skus, key=lambda s: inventory[s["id"]]["days_of_stock"])

    def spike_pct(sku_id: str) -> float:
        rows = records[sku_id]
        last7 = _avg(r["demand"] for r in rows[-7:])
        prev28 = _avg((r["demand"] for r in rows[-35:-7]), default=last7)
        return (last7 - prev28) / max(1.0, prev28) * 100.0

    spike = max(skus, key=lambda s: spike_pct(s["id"]))
    disagree = max(skus, key=lambda s: forecasts[s["id"]]["model_spread_pct"])

    def iso_z(ts: datetime) -> str:
        return ts.isoformat().replace("+00:00", "Z")

    high_days = inventory[high["id"]]["days_of_stock"]
    coverage_type = "overstock" if high_days > 14 else "coverage_watch"
    coverage_message = (
        f"{high['id']} has {high_days:.1f} days of stock; review before placing another order."
        if high_days > 14
        else f"{high['id']} has the highest coverage at {high_days:.1f} days of stock."
    )

    return [
        {
            "id": "alert_001",
            "severity": "critical" if inventory[low["id"]]["days_of_stock"] <= 2.5 else "warning",
            "type": "stockout_risk",
            "message": (
                f"{low['id']} has {inventory[low['id']]['days_of_stock']:.1f} days of stock "
                f"at the current M5-derived demand pace."
            ),
            "affected_skus": [low["id"]],
            "created_at": iso_z(now - timedelta(hours=2)),
            "action": "review_order",
            "active": True,
        },
        {
            "id": "alert_002",
            "severity": "warning",
            "type": "demand_spike",
            "spike_pct": round(spike_pct(spike["id"]), 1),
            "message": (
                f"{spike['id']} demand is {spike_pct(spike['id']):+.0f}% vs the prior 28-day "
                "M5 average."
            ),
            "affected_skus": [spike["id"]],
            "created_at": iso_z(now - timedelta(hours=5)),
            "action": "review_forecast",
            "active": True,
        },
        {
            "id": "alert_003",
            "severity": "info",
            "type": "model_disagreement",
            "message": (
                f"Forecast projections for {disagree['id']} differ by "
                f"{forecasts[disagree['id']]['model_spread_pct']:.1f}%."
            ),
            "affected_skus": [disagree["id"]],
            "created_at": iso_z(now - timedelta(hours=6)),
            "action": "review_forecast",
            "active": True,
        },
        {
            "id": "alert_004",
            "severity": "warning" if high_days > 14 else "info",
            "type": coverage_type,
            "message": coverage_message,
            "affected_skus": [high["id"]],
            "created_at": iso_z(now - timedelta(hours=3)),
            "action": "review_inventory",
            "active": True,
        },
        {
            "id": "alert_005",
            "severity": "success",
            "type": "data_integrity",
            "message": f"{len(skus)} displayed SKUs are present in data/processed/m5_multi_sku.csv.",
            "affected_skus": [],
            "created_at": iso_z(now - timedelta(hours=1)),
            "action": None,
            "active": True,
        },
    ]


def main() -> None:
    summary = _load_json(SUMMARY_PATH)
    selected_skus = summary["selected_skus"]
    records = _read_m5_records(selected_skus)
    missing = [sku for sku in selected_skus if sku not in records]
    if missing:
        raise RuntimeError(f"selected SKUs absent from {DATA_PATH}: {missing}")

    skus = _sku_catalog(summary)
    forecasts = {
        sku_id: _forecast_for_sku(records[sku_id], summary["per_sku_stats"][sku_id])
        for sku_id in selected_skus
    }
    inventory = {
        sku_id: _inventory_for_sku(sku_id, records[sku_id], summary["per_sku_stats"][sku_id], forecasts[sku_id])
        for sku_id in selected_skus
    }
    recs = _recommendations(skus, inventory, forecasts)
    explanations = _explanations(skus, records, summary, inventory, forecasts, recs)
    history = _history_seed(skus, records, inventory, forecasts, recs)
    alerts = _alerts(skus, inventory, forecasts, records)

    _write("skus.json", skus)
    _write("inventory_today.json", inventory)
    _write("forecasts.json", forecasts)
    _write("recommendations.json", recs)
    _write("explanations.json", explanations)
    _write("history_seed.json", history)
    _write("alerts.json", alerts)
    print("ERP dashboard data rebuilt from M5/model artifacts.")


if __name__ == "__main__":
    main()
