#!/usr/bin/env python3
"""
End-to-end runnable demo — NO PIP INSTALLS REQUIRED (stdlib + numpy only).

WHY THIS EXISTS
---------------
The production stack is FastAPI + SQLAlchemy + Postgres + Celery + React
(services/, apps/). That stack needs `pip install` and `npm install`. This file
runs the SAME engine and the SAME JSON contracts on Python's stdlib http.server
and sqlite3, so the whole pipeline can be demonstrated on a machine with
nothing installed. It is a demo harness, not the production server: single
threaded-per-request, no auth, no migrations.

WHAT IT DOES
------------
  seed   build demo.db from the real M5 Walmart dataset (30 SKUs, 1574 days),
         3 locations, 4 suppliers with genuinely different reliability, plus
         synthetic goods-receipt history so lead times are LEARNED not assumed
  fit    run CMA-ES over segment-level policy parameters, benchmark against
         naive and classical (s,S) baselines, persist the winning policy
  run    execute a replenishment run: ERP state -> SmartStock -> draft POs
  serve  expose the ERP + SmartStock APIs and the approval console

USAGE
    python3 demo/run_demo.py            # seed + fit + run + serve
    python3 demo/run_demo.py --serve    # serve an already-seeded db
    python3 demo/run_demo.py --fit      # refit the policy only
    python3 demo/run_demo.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "services" / "smartstock"))

from smartstock.contracts import (                      # noqa: E402
    OrderPolicyConstraints, ReplenishmentRequest, SkuNodeState, SupplierRef,
)
from smartstock.core import policy as P                 # noqa: E402
from smartstock.core.network import NetworkConfig, SkuData  # noqa: E402
from smartstock.core.recommend import ENGINE_VERSION, PolicyStore, generate  # noqa: E402
from smartstock.core.segmentation import SegmentIndex, build_stats  # noqa: E402
from smartstock.training.fit import FitConfig, fit_policy  # noqa: E402

DB_PATH = Path(os.getenv("SMARTSTOCK_DEMO_DB", HERE / "demo.db"))
POLICY_PATH = HERE / "policy.json"
DATA_CSV = HERE / "data" / "m5_multi_sku.csv"

LOCATIONS = [
    ("DC-01", "Central Distribution Centre", "distribution_center", None, 250_000),
    ("ST-01", "Store 01 - Downtown", "store", "DC-01", 12_000),
    ("ST-02", "Store 02 - Suburban", "store", "DC-01", 9_000),
]
# (code, name, contract_lead_days, true_mean, true_std) -- "true" drives the
# synthetic receipt history. The gap between contract and true is deliberate:
# it is exactly the systematic supplier optimism the engine must learn.
SUPPLIERS = [
    ("SUP-ACME", "Acme Foods Distribution", 5.0, 5.4, 0.9),    # reliable
    ("SUP-GLOBAL", "Global Household Supply", 10.0, 13.8, 5.2),  # optimistic + erratic
    ("SUP-HOBBY", "Hobby Imports Ltd", 21.0, 22.1, 3.4),       # slow but honest
    ("SUP-FAST", "FastTrack Local", 2.0, 2.2, 0.4),            # fast, tight
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  id TEXT PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  category TEXT, uom TEXT DEFAULT 'EA', unit_cost REAL NOT NULL DEFAULT 0,
  unit_price REAL NOT NULL DEFAULT 0, shelf_life_days INTEGER, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS locations(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  type TEXT NOT NULL, parent_code TEXT, capacity_units REAL, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS suppliers(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  contract_lead_days REAL NOT NULL, contract_lead_cv REAL NOT NULL DEFAULT 0.35,
  reliability_score REAL, is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS supplier_products(
  id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL, product_id TEXT NOT NULL,
  unit_cost REAL DEFAULT 0, moq REAL DEFAULT 0, order_multiple REAL DEFAULT 1,
  max_order_qty REAL, is_preferred INTEGER DEFAULT 0,
  UNIQUE(supplier_id, product_id));
CREATE TABLE IF NOT EXISTS inventory_levels(
  id TEXT PRIMARY KEY, product_id TEXT NOT NULL, location_id TEXT NOT NULL,
  on_hand REAL DEFAULT 0, on_order REAL DEFAULT 0, reserved REAL DEFAULT 0,
  backorder REAL DEFAULT 0, reorder_point REAL, order_up_to REAL, safety_stock REAL,
  UNIQUE(product_id, location_id));
CREATE TABLE IF NOT EXISTS demand_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL,
  location_id TEXT NOT NULL, bucket_date TEXT NOT NULL, quantity REAL NOT NULL,
  was_stocked_out INTEGER DEFAULT 0, UNIQUE(product_id, location_id, bucket_date));
CREATE TABLE IF NOT EXISTS lead_time_observations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id TEXT NOT NULL,
  product_id TEXT NOT NULL, ordered_at TEXT, received_at TEXT, lead_days REAL NOT NULL);
CREATE TABLE IF NOT EXISTS replenishment_runs(
  id TEXT PRIMARY KEY, run_date TEXT NOT NULL, status TEXT NOT NULL,
  policy_version TEXT, engine_version TEXT, items_sent INTEGER DEFAULT 0,
  lines_recommended INTEGER DEFAULT 0, total_value REAL DEFAULT 0,
  duration_ms INTEGER, stats TEXT DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS recommendations(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, product_id TEXT NOT NULL,
  location_id TEXT NOT NULL, supplier_id TEXT, recommended_qty REAL NOT NULL,
  unconstrained_qty REAL, unit_cost REAL, line_value REAL, urgency TEXT,
  confidence REAL, status TEXT DEFAULT 'pending', final_qty REAL,
  decided_by TEXT, decided_at TEXT, decision_note TEXT,
  rationale TEXT DEFAULT '{}', warnings TEXT DEFAULT '[]',
  UNIQUE(run_id, product_id, location_id));
CREATE TABLE IF NOT EXISTS purchase_orders(
  id TEXT PRIMARY KEY, po_number TEXT UNIQUE NOT NULL, supplier_id TEXT,
  location_id TEXT, status TEXT NOT NULL, total_value REAL DEFAULT 0,
  source TEXT DEFAULT 'smartstock', approved_by TEXT, approved_at TEXT,
  expected_delivery_date TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS purchase_order_lines(
  id TEXT PRIMARY KEY, purchase_order_id TEXT NOT NULL, product_id TEXT NOT NULL,
  line_no INTEGER, ai_recommended_qty REAL, ordered_qty REAL NOT NULL,
  unit_cost REAL, line_value REAL, override_reason TEXT, ai_rationale TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT,
  action TEXT, actor TEXT, before TEXT, after TEXT, occurred_at TEXT);
CREATE INDEX IF NOT EXISTS ix_dh ON demand_history(product_id, location_id, bucket_date);
CREATE INDEX IF NOT EXISTS ix_reco ON recommendations(run_id, status);
"""


def connect() -> sqlite3.Connection:
    """Open the demo db.

    WAL is preferred for concurrent readers, but it needs shared-memory
    locking that network mounts (NFS, SMB, FUSE, Docker bind mounts on some
    hosts) do not provide -- there it fails with "disk I/O error". Fall back
    to TRUNCATE, which works everywhere at the cost of writer concurrency
    that a single-process demo does not need. Set SMARTSTOCK_DEMO_DB to move
    the file to local disk if you want WAL.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    for mode in ("WAL", "TRUNCATE", "DELETE"):
        try:
            conn.execute(f"PRAGMA journal_mode={mode}")
            conn.execute("SELECT 1").fetchone()
            break
        except sqlite3.OperationalError:
            continue
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ────────────────────────────── seed ──────────────────────────────

def seed(force: bool = False) -> None:
    if DB_PATH.exists() and not force:
        print(f"  db exists at {DB_PATH.name} (use --reseed to rebuild)")
        return
    if DB_PATH.exists():
        DB_PATH.unlink()
    if not DATA_CSV.exists():
        raise SystemExit(f"missing dataset: {DATA_CSV}")

    conn = connect()
    conn.executescript(SCHEMA)
    rng = np.random.default_rng(20260804)

    # --- load real M5 demand ---
    import csv
    series: Dict[str, List[float]] = {}
    prices: Dict[str, List[float]] = {}
    with open(DATA_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            series.setdefault(row["sku_id"], []).append(float(row["demand"]))
            prices.setdefault(row["sku_id"], []).append(float(row["price"]))
    skus = sorted(series)
    n_days = min(len(v) for v in series.values())
    print(f"  loaded {len(skus)} SKUs x {n_days} days of real M5 demand")

    # --- locations / suppliers ---
    loc_ids: Dict[str, str] = {}
    for code, name, typ, parent, cap in LOCATIONS:
        lid = str(uuid.uuid4()); loc_ids[code] = lid
        conn.execute(
            "INSERT INTO locations(id,code,name,type,parent_code,capacity_units) VALUES(?,?,?,?,?,?)",
            (lid, code, name, typ, parent, cap))
    sup_ids: Dict[str, str] = {}
    for code, name, contract, tmean, tstd in SUPPLIERS:
        sid = str(uuid.uuid4()); sup_ids[code] = sid
        conn.execute(
            "INSERT INTO suppliers(id,code,name,contract_lead_days,contract_lead_cv,reliability_score)"
            " VALUES(?,?,?,?,?,?)",
            (sid, code, name, contract, round(tstd / max(tmean, 1e-6), 4),
             round(float(np.clip(1.0 - tstd / max(tmean, 1e-6), 0, 1)), 4)))

    def pick_supplier(sku: str) -> str:
        if sku.startswith("FOODS"):
            return "SUP-ACME" if hash(sku) % 3 else "SUP-FAST"
        if sku.startswith("HOUSEHOLD"):
            return "SUP-GLOBAL"
        return "SUP-HOBBY"

    prod_ids: Dict[str, str] = {}
    dh_rows, lt_rows, inv_rows, sp_rows, prod_rows = [], [], [], [], []
    today = date.today()

    for sku in skus:
        pid = str(uuid.uuid4()); prod_ids[sku] = pid
        d = np.asarray(series[sku][:n_days], dtype=float)
        price = float(np.mean(prices[sku][:n_days]))
        cost = round(price * 0.6, 4)
        cat = sku.split("_")[0].title()
        prod_rows.append((pid, sku, f"{cat} item {sku.split('_')[-1]}", cat, "EA",
                          cost, round(price, 4),
                          90 if cat == "Foods" else None, 1))

        sup_code = pick_supplier(sku)
        moq = float(rng.choice([0, 0, 50, 100, 250]))
        mult = float(rng.choice([1, 1, 6, 12, 24]))
        sp_rows.append((str(uuid.uuid4()), sup_ids[sup_code], pid, cost, moq, mult, None, 1))

        # --- demand history split across the two stores ---
        keep = min(400, n_days)
        tail = d[-keep:]
        for j, code in enumerate(("ST-01", "ST-02")):
            share = (0.6, 0.4)[j]
            for i, q in enumerate(tail):
                bucket = (today - timedelta(days=keep - i)).isoformat()
                dh_rows.append((pid, loc_ids[code], bucket, round(float(q) * share, 3), 0))

        # --- synthetic goods-receipt history -> LEARNED lead times ---
        _, _, contract, tmean, tstd = next(s for s in SUPPLIERS if s[0] == sup_code)
        n_obs = int(rng.integers(0, 16))   # some SKUs have none: tests the prior
        for k in range(n_obs):
            days = float(np.clip(rng.gamma((tmean / max(tstd, 0.3)) ** 2,
                                           (max(tstd, 0.3) ** 2) / tmean), 0.5, 120))
            ordered = today - timedelta(days=int(rng.integers(30, 400)))
            lt_rows.append((sup_ids[sup_code], pid, ordered.isoformat(),
                            (ordered + timedelta(days=days)).isoformat(), round(days, 2)))

        # --- opening stock: deliberately varied so the queue is interesting ---
        mean_d = float(d[-90:].mean())
        for code, mult_days in (("DC-01", rng.uniform(0.5, 30)),
                                ("ST-01", rng.uniform(0.0, 18)),
                                ("ST-02", rng.uniform(0.0, 18))):
            share = {"DC-01": 1.0, "ST-01": 0.6, "ST-02": 0.4}[code]
            on_hand = round(max(0.0, mean_d * share * mult_days), 2)
            on_order = round(float(rng.choice([0, 0, 0, mean_d * 5])), 2)
            inv_rows.append((str(uuid.uuid4()), pid, loc_ids[code], on_hand, on_order, 0, 0))

    conn.executemany("INSERT INTO products(id,sku,name,category,uom,unit_cost,unit_price,"
                     "shelf_life_days,is_active) VALUES(?,?,?,?,?,?,?,?,?)", prod_rows)
    conn.executemany("INSERT INTO supplier_products(id,supplier_id,product_id,unit_cost,moq,"
                     "order_multiple,max_order_qty,is_preferred) VALUES(?,?,?,?,?,?,?,?)", sp_rows)
    conn.executemany("INSERT INTO inventory_levels(id,product_id,location_id,on_hand,on_order,"
                     "reserved,backorder) VALUES(?,?,?,?,?,?,?)", inv_rows)
    conn.executemany("INSERT INTO demand_history(product_id,location_id,bucket_date,quantity,"
                     "was_stocked_out) VALUES(?,?,?,?,?)", dh_rows)
    conn.executemany("INSERT INTO lead_time_observations(supplier_id,product_id,ordered_at,"
                     "received_at,lead_days) VALUES(?,?,?,?,?)", lt_rows)
    conn.commit()
    print(f"  seeded: {len(prod_rows)} products, {len(LOCATIONS)} locations, "
          f"{len(SUPPLIERS)} suppliers, {len(inv_rows)} inventory rows,")
    print(f"          {len(dh_rows):,} demand buckets, {len(lt_rows)} goods-receipt lead times")
    conn.close()


# ─────────────────────── ERP -> SmartStock payload ───────────────────────

def build_items(conn: sqlite3.Connection, history_days: int = 400) -> List[SkuNodeState]:
    rows = conn.execute("""
        SELECT i.on_hand, i.on_order, i.backorder, p.id pid, p.sku, p.unit_cost, p.unit_price,
               p.shelf_life_days, l.id lid, l.code node, l.capacity_units,
               s.id sid, s.code scode, s.name sname, s.contract_lead_days, s.contract_lead_cv,
               sp.moq, sp.order_multiple, sp.max_order_qty
        FROM inventory_levels i
        JOIN products p ON p.id = i.product_id
        JOIN locations l ON l.id = i.location_id
        LEFT JOIN supplier_products sp ON sp.product_id = p.id
        LEFT JOIN suppliers s ON s.id = sp.supplier_id
        WHERE p.is_active = 1 AND l.is_active = 1
    """).fetchall()

    hist: Dict[tuple, List[float]] = {}
    for r in conn.execute(
        "SELECT product_id, location_id, quantity FROM demand_history ORDER BY bucket_date"
    ):
        hist.setdefault((r["product_id"], r["location_id"]), []).append(float(r["quantity"]))

    leads: Dict[tuple, List[float]] = {}
    for r in conn.execute(
        "SELECT supplier_id, product_id, lead_days FROM lead_time_observations ORDER BY received_at DESC"
    ):
        leads.setdefault((r["supplier_id"], r["product_id"]), []).append(float(r["lead_days"]))

    items: List[SkuNodeState] = []
    for r in rows:
        h = hist.get((r["pid"], r["lid"]), [])[-history_days:]
        # A DC has no direct customer demand; it inherits the sum of its stores.
        if not h and r["node"].startswith("DC"):
            agg: Dict[int, float] = {}
            for (pid, _lid), vals in hist.items():
                if pid != r["pid"]:
                    continue
                for i, v in enumerate(vals[-history_days:]):
                    agg[i] = agg.get(i, 0.0) + v
            h = [agg[i] for i in sorted(agg)]
        sup = None
        cons = None
        if r["sid"]:
            sup = SupplierRef(supplier_id=r["sid"], name=r["sname"],
                              contract_lead_days=float(r["contract_lead_days"]),
                              contract_lead_cv=float(r["contract_lead_cv"]))
            cons = OrderPolicyConstraints(
                moq=float(r["moq"] or 0), order_multiple=float(r["order_multiple"] or 1),
                max_order_qty=float(r["max_order_qty"]) if r["max_order_qty"] else None,
                max_inventory_position=float(r["capacity_units"]) if r["capacity_units"] else None,
                shelf_life_days=r["shelf_life_days"])
        items.append(SkuNodeState(
            sku_id=r["sku"], node_id=r["node"], on_hand=float(r["on_hand"]),
            on_order=float(r["on_order"]), backorder=float(r["backorder"]),
            unit_cost=float(r["unit_cost"]), unit_price=float(r["unit_price"]),
            demand_history=h, supplier=sup,
            lead_time_observations=leads.get((r["sid"], r["pid"]), [])[:30] if r["sid"] else [],
            constraints=cons))
    return items


# ────────────────────────────── fit ──────────────────────────────

def fit(generations: int = 45) -> dict:
    conn = connect()
    items = build_items(conn)
    by_sku: Dict[str, List[float]] = {}
    cost: Dict[str, float] = {}
    price: Dict[str, float] = {}
    ltm: Dict[str, float] = {}
    lts: Dict[str, float] = {}
    for it in items:
        if it.node_id != "DC-01":
            continue
        by_sku[it.sku_id] = it.demand_history or []
        cost[it.sku_id] = it.unit_cost
        price[it.sku_id] = it.unit_price
        obs = it.lead_time_observations or []
        contract = it.supplier.contract_lead_days if it.supplier else 7.0
        cv = (it.supplier.contract_lead_cv if it.supplier else 0.35) or 0.35
        ltm[it.sku_id] = float(np.mean(obs)) if obs else contract
        lts[it.sku_id] = float(np.std(obs, ddof=1)) if len(obs) > 1 else cv * contract

    skus = sorted(k for k, v in by_sku.items() if len(v) >= 120)
    if not skus:
        raise SystemExit("not enough demand history to fit; run --reseed")
    n = min(len(by_sku[s]) for s in skus)
    D = np.stack([np.asarray(by_sku[s][-n:], float) for s in skus])
    stats = build_stats({s: D[i] for i, s in enumerate(skus)})
    index = SegmentIndex(stats)
    print(f"  {len(skus)} SKUs -> {index.n_segments} segments -> "
          f"{index.n_segments * P.N_PARAMS} CMA-ES dimensions")
    print(f"  segment sizes: {index.describe()}")

    data = SkuData(
        sku_ids=skus, demand=D,
        unit_cost=np.array([cost[s] for s in skus]),
        unit_price=np.array([price[s] for s in skus]),
        lt_dc_mean=np.array([ltm[s] for s in skus]),
        lt_dc_std=np.array([lts[s] for s in skus]),
        lt_store_mean=np.full(len(skus), 2.0), lt_store_std=np.full(len(skus), 0.5),
        segment_idx=index.index_array(skus))
    cfg = NetworkConfig(horizon=150, warmup=30, service_floor=0.95)
    train_hi = max(1, int(n * 0.6) - 150)
    res = fit_policy(cfg, data, index.segments,
                     FitConfig(max_generations=generations, seed=42,
                               train_window=(0, train_hi),
                               test_window=(int(n * 0.65), max(int(n * 0.65) + 1, n - 151)),
                               horizon=150))
    POLICY_PATH.write_text(json.dumps({
        "version": f"demo-fit-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}",
        "params": {s: res.raw_theta[i].tolist() for i, s in enumerate(res.segments)},
        "fit": {k: v for k, v in res.to_dict().items() if k != "history"},
    }, indent=1))

    ai, cl, nv = res.test_metrics, res.baseline_metrics["classical_ss"], res.baseline_metrics["naive_7day"]
    print(f"\n  fit: {res.generations} generations, {res.evaluations} evals, "
          f"{res.wall_seconds:.1f}s ({res.stop_reason})")
    print(f"  train fitness {res.history[0]:,.0f} -> {res.train_fitness:,.0f}\n")
    print("  OUT-OF-SAMPLE (held-out demand window)")
    print(f"  {'policy':<22}{'total cost':>12}{'fill rate':>11}{'worst SKU':>11}{'avg inv':>10}")
    print("  " + "-" * 66)
    for label, m in (("SmartStock (CMA-ES)", ai), ("classical (s,S)", cl), ("naive 7-day", nv)):
        print(f"  {label:<22}{m['total_cost']:>12,.0f}{m['fill_rate']:>11.2%}"
              f"{m['worst_sku_service']:>11.2%}{m['avg_inventory_units']:>10,.0f}")
    print(f"\n  vs classical: cost {(1 - ai['total_cost'] / max(cl['total_cost'], 1e-9)) * 100:+.1f}%, "
          f"fill {(ai['fill_rate'] - cl['fill_rate']) * 100:+.2f}pp")
    conn.close()
    return res.to_dict()


# ────────────────────────── replenishment run ──────────────────────────

def load_policy() -> PolicyStore:
    store = PolicyStore()
    if POLICY_PATH.exists():
        blob = json.loads(POLICY_PATH.read_text())
        store.load(blob["params"], blob.get("version"))
    return store


def run_replenishment(store: Optional[PolicyStore] = None) -> str:
    conn = connect()
    store = store or load_policy()
    items = build_items(conn)
    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    resp = generate(ReplenishmentRequest(
        run_id=run_id, as_of_date=date.today().isoformat(), items=items), store)
    ms = int((time.perf_counter() - t0) * 1000)

    prod = {r["sku"]: r["id"] for r in conn.execute("SELECT id, sku FROM products")}
    loc = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM locations")}
    conn.execute("INSERT INTO replenishment_runs(id,run_date,status,policy_version,engine_version,"
                 "items_sent,lines_recommended,total_value,duration_ms,stats,created_at)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (run_id, date.today().isoformat(), "succeeded", resp.policy_version,
                  resp.engine_version, len(items), resp.stats["lines_recommended"],
                  resp.stats["total_value"], ms, json.dumps(resp.stats),
                  datetime.now(timezone.utc).isoformat()))
    seen = set()
    for dpo in resp.draft_purchase_orders:
        for ln in dpo.lines:
            key = (prod.get(ln.sku_id), loc.get(ln.node_id))
            if key in seen or None in key:
                continue
            seen.add(key)
            conn.execute(
                "INSERT INTO recommendations(id,run_id,product_id,location_id,supplier_id,"
                "recommended_qty,unconstrained_qty,unit_cost,line_value,urgency,confidence,"
                "status,rationale,warnings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), run_id, key[0], key[1], dpo.supplier_id,
                 ln.recommended_qty, ln.unconstrained_qty, ln.unit_cost, ln.line_value,
                 ln.urgency.value if hasattr(ln.urgency, "value") else ln.urgency,
                 ln.confidence, "pending",
                 json.dumps(ln.rationale.model_dump() if ln.rationale else {}),
                 json.dumps(ln.warnings)))
    conn.commit()
    s = resp.stats
    print(f"  run {run_id[:8]}: {s['items_received']} states -> {s['lines_recommended']} lines "
          f"across {s['draft_po_count']} draft POs, ${s['total_value']:,.0f}, "
          f"{s['critical_lines']} critical, {ms}ms")
    conn.close()
    return run_id


# ────────────────────────────── HTTP ──────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "SmartStockDemo/2.0"
    store: PolicyStore = PolicyStore()

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("DEMO_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: Any, ctype: str = "application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self._send(204, b"")

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                html = (ROOT / "apps" / "console" / "index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if u.path == "/healthz":
                return self._send(200, {"status": "ok", "engine_version": ENGINE_VERSION,
                                        "policy_version": self.store.policy_version,
                                        "n_segments": len(self.store.segments)})
            if u.path == "/api/v1/dashboard":
                return self._send(200, api_dashboard())
            if u.path == "/api/v1/procurement/recommendations":
                return self._send(200, api_queue(q))
            if u.path == "/api/v1/procurement/runs":
                return self._send(200, api_runs())
            if u.path == "/api/v1/procurement/purchase-orders":
                return self._send(200, api_pos())
            if u.path == "/api/v1/procurement/variance":
                return self._send(200, api_variance())
            if u.path == "/api/v1/inventory":
                return self._send(200, api_inventory(q))
            if u.path == "/v1/policy":
                return self._send(200, {"policy_version": self.store.policy_version,
                                        "segments": self.store.segments,
                                        "parameters": self.store.describe(),
                                        "param_names": list(P.PARAM_NAMES)})
            return self._send(404, {"error": "not_found", "path": u.path})
        except Exception as exc:
            import traceback; traceback.print_exc()
            return self._send(500, {"error": "internal_error", "detail": str(exc)})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": "bad_json", "detail": str(exc)})
        try:
            if u.path == "/api/v1/procurement/recommendations/decide":
                return self._send(200, api_decide(body))
            if u.path == "/api/v1/replenishment/run":
                rid = run_replenishment(self.store)
                return self._send(200, {"run_id": rid})
            if u.path == "/v1/recommendations:generate":
                req = ReplenishmentRequest(**body)
                return self._send(200, generate(req, self.store).model_dump())
            return self._send(404, {"error": "not_found", "path": u.path})
        except Exception as exc:
            import traceback; traceback.print_exc()
            return self._send(500, {"error": "internal_error", "detail": str(exc)})


# ───────────────────────── API implementations ─────────────────────────

RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}


def api_dashboard() -> dict:
    c = connect()
    try:
        sv = c.execute("SELECT COALESCE(SUM(i.on_hand*p.unit_cost),0) v FROM inventory_levels i "
                       "JOIN products p ON p.id=i.product_id").fetchone()["v"]
        out = c.execute("SELECT COUNT(*) n FROM inventory_levels WHERE on_hand<=0").fetchone()["n"]
        pend = c.execute("SELECT COUNT(*) n, COALESCE(SUM(line_value),0) v FROM recommendations "
                         "WHERE status='pending'").fetchone()
        crit = c.execute("SELECT COUNT(*) n FROM recommendations WHERE status='pending' "
                         "AND urgency='critical'").fetchone()["n"]
        pos = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_value),0) v FROM purchase_orders").fetchone()
        run = c.execute("SELECT * FROM replenishment_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        return {
            "inventory": {"stock_value": round(sv, 2), "skus_out_of_stock": out},
            "procurement": {"pending_recommendations": pend["n"],
                            "pending_value": round(pend["v"], 2),
                            "critical_recommendations": crit,
                            "open_purchase_orders": pos["n"],
                            "open_po_value": round(pos["v"], 2)},
            "last_run": None if not run else {
                "id": run["id"], "run_date": run["run_date"], "status": run["status"],
                "policy_version": run["policy_version"], "engine_version": run["engine_version"],
                "lines_recommended": run["lines_recommended"], "duration_ms": run["duration_ms"],
                "stats": json.loads(run["stats"] or "{}")},
        }
    finally:
        c.close()


def api_queue(q: Dict[str, str]) -> dict:
    c = connect()
    try:
        run = c.execute("SELECT id FROM replenishment_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if not run:
            return {"run_id": None, "total": 0, "items": [], "summary": {}}
        status = q.get("status", "pending")
        sql = ("SELECT r.*, p.sku, p.name pname, l.code lcode, l.name lname, s.name sname "
               "FROM recommendations r JOIN products p ON p.id=r.product_id "
               "JOIN locations l ON l.id=r.location_id LEFT JOIN suppliers s ON s.id=r.supplier_id "
               "WHERE r.run_id=?")
        args: List[Any] = [run["id"]]
        if status != "all":
            sql += " AND r.status=?"; args.append(status)
        if q.get("urgency"):
            sql += " AND r.urgency=?"; args.append(q["urgency"])
        rows = c.execute(sql, args).fetchall()
        items = sorted(
            ({"id": r["id"], "sku": r["sku"], "product_name": r["pname"],
              "location_code": r["lcode"], "location_name": r["lname"],
              "supplier_id": r["supplier_id"], "supplier_name": r["sname"],
              "recommended_qty": r["recommended_qty"],
              "unconstrained_qty": r["unconstrained_qty"], "unit_cost": r["unit_cost"],
              "line_value": r["line_value"], "urgency": r["urgency"],
              "confidence": r["confidence"], "status": r["status"],
              "rationale": json.loads(r["rationale"] or "{}"),
              "warnings": json.loads(r["warnings"] or "[]")} for r in rows),
            key=lambda x: (RANK.get(x["urgency"], 9), -x["line_value"]))
        summary: Dict[str, Dict[str, float]] = {}
        for r in rows:
            if r["status"] != "pending":
                continue
            b = summary.setdefault(r["urgency"], {"count": 0, "value": 0.0})
            b["count"] += 1; b["value"] = round(b["value"] + r["line_value"], 2)
        return {"run_id": run["id"], "total": len(items), "limit": len(items),
                "offset": 0, "summary": summary, "items": items}
    finally:
        c.close()


def api_runs() -> list:
    c = connect()
    try:
        return [dict(r) for r in c.execute(
            "SELECT id,run_date,status,policy_version,items_sent,lines_recommended,"
            "total_value,duration_ms FROM replenishment_runs ORDER BY created_at DESC LIMIT 30")]
    finally:
        c.close()


def api_pos() -> list:
    c = connect()
    try:
        return [dict(r) for r in c.execute(
            "SELECT po.*, s.name supplier_name, COUNT(l.id) line_count "
            "FROM purchase_orders po LEFT JOIN suppliers s ON s.id=po.supplier_id "
            "LEFT JOIN purchase_order_lines l ON l.purchase_order_id=po.id "
            "GROUP BY po.id ORDER BY po.created_at DESC LIMIT 100")]
    finally:
        c.close()


def api_variance() -> dict:
    c = connect()
    try:
        by = {r["status"]: {"count": r["n"], "value": round(r["v"] or 0, 2)} for r in c.execute(
            "SELECT status, COUNT(*) n, SUM(line_value) v FROM recommendations "
            "WHERE decided_at IS NOT NULL GROUP BY status")}
        mods = c.execute("SELECT recommended_qty a, final_qty b FROM recommendations "
                         "WHERE status='modified' AND recommended_qty>0").fetchall()
        d = [(r["b"] - r["a"]) / r["a"] for r in mods if r["b"] is not None]
        return {"by_status": by, "modification_count": len(d),
                "mean_relative_override": round(sum(d) / len(d), 4) if d else 0.0,
                "override_bias": ("humans order MORE than the model" if d and sum(d) > 0
                                  else "humans order LESS than the model" if d else "no overrides yet")}
    finally:
        c.close()


def api_inventory(q: Dict[str, str]) -> dict:
    c = connect()
    try:
        sql = ("SELECT p.sku, p.name, l.code lcode, i.on_hand, i.on_order, i.backorder, "
               "p.unit_cost FROM inventory_levels i JOIN products p ON p.id=i.product_id "
               "JOIN locations l ON l.id=i.location_id")
        args: List[Any] = []
        if q.get("location_code"):
            sql += " WHERE l.code=?"; args.append(q["location_code"])
        sql += " ORDER BY p.sku LIMIT 500"
        rows = [dict(r) for r in c.execute(sql, args)]
        for r in rows:
            r["inventory_position"] = round(r["on_hand"] + r["on_order"] - r["backorder"], 2)
            r["stock_value"] = round(r["on_hand"] * r["unit_cost"], 2)
        return {"total": len(rows), "items": rows}
    finally:
        c.close()


def api_decide(body: Dict[str, Any]) -> dict:
    """Mirrors services/erp-api procurement.decide, including PO grouping."""
    decisions = body.get("decisions") or []
    actor = body.get("actor") or "demo-user"
    if not decisions:
        return {"approved": 0, "rejected": 0, "modified": 0,
                "purchase_orders_created": [], "errors": [{"id": "-", "error": "no decisions"}]}
    c = connect()
    try:
        n_app = n_rej = n_mod = 0
        errors: List[dict] = []
        approved: List[sqlite3.Row] = []
        now = datetime.now(timezone.utc).isoformat()
        for d in decisions:
            rid = d.get("recommendation_id")
            row = c.execute("SELECT * FROM recommendations WHERE id=?", (rid,)).fetchone()
            if not row:
                errors.append({"id": str(rid), "error": "not found"}); continue
            if row["status"] != "pending":
                errors.append({"id": str(rid), "error": f"already {row['status']}"}); continue
            action = d.get("action")
            if action == "reject":
                c.execute("UPDATE recommendations SET status='rejected',final_qty=0,decided_by=?,"
                          "decided_at=?,decision_note=? WHERE id=?",
                          (actor, now, d.get("note"), rid)); n_rej += 1
            else:
                qty = d.get("final_qty") if action == "modify" else row["recommended_qty"]
                if qty is None:
                    errors.append({"id": str(rid), "error": "modify requires final_qty"}); continue
                qty = float(qty)
                if qty <= 0:
                    c.execute("UPDATE recommendations SET status='rejected',final_qty=0,"
                              "decided_by=?,decided_at=? WHERE id=?", (actor, now, rid))
                    n_rej += 1; continue
                st = "modified" if action == "modify" else "approved"
                c.execute("UPDATE recommendations SET status=?,final_qty=?,decided_by=?,"
                          "decided_at=?,decision_note=? WHERE id=?",
                          (st, qty, actor, now, d.get("note"), rid))
                n_mod += action == "modify"; n_app += action == "approve"
                approved.append((c.execute("SELECT * FROM recommendations WHERE id=?", (rid,)).fetchone(),
                                 qty, d.get("note")))
            c.execute("INSERT INTO audit_log(entity_type,entity_id,action,actor,before,after,occurred_at)"
                      " VALUES(?,?,?,?,?,?,?)",
                      ("recommendation", str(rid), str(action), actor,
                       json.dumps({"status": "pending", "qty": row["recommended_qty"]}),
                       json.dumps({"action": action}), now))

        created: List[str] = []
        groups: Dict[tuple, list] = {}
        for row, qty, note in approved:
            groups.setdefault((row["supplier_id"], row["location_id"]), []).append((row, qty, note))
        seq = c.execute("SELECT COUNT(*) n FROM purchase_orders").fetchone()["n"]
        for (sid, lid), lines in groups.items():
            if not sid:
                continue
            seq += 1
            po_id = str(uuid.uuid4())
            po_no = f"PO-{date.today():%Y%m}-{seq:05d}"
            total = 0.0
            c.execute("INSERT INTO purchase_orders(id,po_number,supplier_id,location_id,status,"
                      "total_value,source,approved_by,approved_at,created_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (po_id, po_no, sid, lid, "approved", 0, "smartstock", actor, now, now))
            for i, (row, qty, note) in enumerate(lines, 1):
                val = qty * (row["unit_cost"] or 0)
                total += val
                c.execute("INSERT INTO purchase_order_lines(id,purchase_order_id,product_id,line_no,"
                          "ai_recommended_qty,ordered_qty,unit_cost,line_value,override_reason,"
                          "ai_rationale) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (str(uuid.uuid4()), po_id, row["product_id"], i,
                           row["recommended_qty"], qty, row["unit_cost"], val,
                           note if qty != row["recommended_qty"] else None, row["rationale"]))
            c.execute("UPDATE purchase_orders SET total_value=? WHERE id=?", (round(total, 2), po_id))
            created.append(po_no)
        c.commit()
        return {"approved": n_app, "rejected": n_rej, "modified": n_mod,
                "purchase_orders_created": created, "errors": errors}
    finally:
        c.close()


# ────────────────────────────── main ──────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="SmartStock ERP demo")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reseed", action="store_true")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--fit", action="store_true", help="refit the policy and exit")
    ap.add_argument("--run", action="store_true", help="run replenishment and exit")
    ap.add_argument("--serve", action="store_true", help="serve without seeding or fitting")
    ap.add_argument("--generations", type=int, default=45)
    args = ap.parse_args()

    print("=" * 74)
    print(f" SmartStock ERP demo — engine {ENGINE_VERSION}")
    print("=" * 74)

    if args.serve:
        pass
    else:
        print("\n[1/3] SEED")
        seed(force=args.reseed)
        if args.seed_only:
            return
        if args.fit or not POLICY_PATH.exists():
            print("\n[2/3] FIT POLICY (CMA-ES)")
            fit(args.generations)
        else:
            print(f"\n[2/3] FIT POLICY — reusing {POLICY_PATH.name} (use --fit to refit)")
        if args.fit:
            return
        print("\n[3/3] REPLENISHMENT RUN")
        run_replenishment()
        if args.run:
            return

    Handler.store = load_policy()
    if not DB_PATH.exists():
        raise SystemExit("no demo.db — run without --serve first")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("\n" + "=" * 74)
    print(f"  Approval console : http://{args.host}:{args.port}/")
    print(f"  ERP API          : http://{args.host}:{args.port}/api/v1/dashboard")
    print(f"  SmartStock API   : POST http://{args.host}:{args.port}/v1/recommendations:generate")
    print(f"  Policy           : http://{args.host}:{args.port}/v1/policy")
    print("=" * 74 + "\n  Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
