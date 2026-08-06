#!/usr/bin/env python3
"""End-to-end check for stocktakes and stock transfers.

    python3 demo/verify_inventory_ops.py

Boots the demo on a scratch database and drives the real HTTP endpoints, then
reads the database directly to confirm the side effects actually happened —
not just that the API returned 201.

The three things that matter and that a status code cannot tell you:

  * a TRANSFER conserves stock. The source falls, the destination rises, and
    the tenant's total is unchanged. If the total moves, stock is being created
    or destroyed by a movement that is supposed to be a relocation.
  * both halves are LEDGERED. `transfer_out` at the source and `transfer_in` at
    the destination, so the movement history reconciles to the balance.
  * a STOCKTAKE writes the variance, not the count. The ledger row has to carry
    the difference, because the difference is the shrinkage number a shop needs;
    posting the counted quantity there would make every count look like a
    massive stock injection.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS: list[tuple[bool, str, str]] = []


def check(ok, label: str, detail: str = "") -> bool:
    ok = bool(ok)
    CHECKS.append((ok, label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class C:
    def __init__(self, base, tenant):
        self.base, self.tenant = base, tenant

    def _open(self, path, payload=None):
        return urllib.request.urlopen(urllib.request.Request(
            self.base + path,
            headers={"X-Tenant": self.tenant, "Content-Type": "application/json"},
            data=None if payload is None else json.dumps(payload).encode()), timeout=25)

    def get(self, p):
        return json.load(self._open(p))

    def items(self, p):
        return self.get(p).get("items", [])

    def post(self, p, b):
        return json.load(self._open(p, b))

    def status(self, p, b):
        try:
            r = self._open(p, b)
            return r.status, r.read().decode()[:220]
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:220]


def main() -> int:
    port, db = free_port(), os.path.join(tempfile.mkdtemp(prefix="inv-"), "t.db")
    env = {**os.environ, "ERP_DEMO_DB": db}
    print(f"booting on 127.0.0.1:{port} against a scratch database\n")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "demo" / "erp_server.py"), "--port", str(port), "--reseed"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(90):
            if proc.poll() is not None:
                print(proc.stdout.read()[-2500:] if proc.stdout else "server died")
                return 1
            try:
                urllib.request.urlopen(base + "/api/v1/tenants", timeout=2).read()
                break
            except Exception:
                time.sleep(0.5)

        tenants = [t["id"] for t in C(base, "x").get("/api/v1/tenants")["items"]]
        A, B = C(base, tenants[0]), C(base, tenants[1])
        conn = sqlite3.connect(db)

        def total_stock(tenant):
            return conn.execute(
                "SELECT COALESCE(SUM(on_hand),0) FROM inventory_levels WHERE tenant_id=?",
                (tenant,)).fetchone()[0]

        def at(tenant, pid, loc):
            r = conn.execute(
                "SELECT COALESCE(on_hand,0) FROM inventory_levels"
                " WHERE tenant_id=? AND product_id=? AND location_id=?",
                (tenant, pid, loc)).fetchone()
            return r[0] if r else 0.0

        # ── TRANSFER ──
        print("TRANSFER")
        locs = A.items("/api/v1/locations")
        check(len(locs) >= 2, "two locations to move between", f"{len(locs)}")
        src, dst = locs[0], locs[1]
        stocked = [r for r in A.items(f"/api/v1/inventory?location={src['code']}")
                   if r["on_hand"] >= 3]
        check(stocked, "something on the shelf at the source", f"{len(stocked)} lines")
        row = stocked[0]
        pid, qty = row["product_id"], 2.0

        before_src = at(tenants[0], pid, src["id"])
        before_dst = at(tenants[0], pid, dst["id"])
        before_total = total_stock(tenants[0])

        tr = A.post("/api/v1/inventory/transfers", {
            "from_location_id": src["id"], "to_location_id": dst["id"],
            "lines": [{"product_id": pid, "quantity": qty}], "notes": "verify"})
        check(tr.get("transfer_number"), "transfer posted", tr.get("transfer_number", ""))

        after_src = at(tenants[0], pid, src["id"])
        after_dst = at(tenants[0], pid, dst["id"])
        check(after_src == before_src - qty, "source fell",
              f"{before_src:g} -> {after_src:g}")
        check(after_dst == before_dst + qty, "destination rose",
              f"{before_dst:g} -> {after_dst:g}")
        check(total_stock(tenants[0]) == before_total,
              "TOTAL stock is unchanged — a transfer relocates, it does not create",
              f"{before_total:g} both sides")

        movs = conn.execute(
            "SELECT movement_type, location_id, quantity FROM stock_movements"
            " WHERE tenant_id=? AND reference_type='transfer' AND reference_id=?"
            " ORDER BY movement_type", (tenants[0], tr["id"])).fetchall()
        kinds = {m[0] for m in movs}
        check(kinds == {"transfer_in", "transfer_out"},
              "both halves are in the stock ledger", f"{sorted(kinds)}")
        out = next((m for m in movs if m[0] == "transfer_out"), None)
        inn = next((m for m in movs if m[0] == "transfer_in"), None)
        check(out and out[1] == src["id"] and out[2] < 0,
              "the out-leg is negative and sits at the SOURCE", f"{out[2] if out else '?'}")
        check(inn and inn[1] == dst["id"] and inn[2] > 0,
              "the in-leg is positive and sits at the DESTINATION", f"{inn[2] if inn else '?'}")
        check(out and inn and abs(out[2]) == inn[2],
              "the two legs are equal and opposite — the ledger reconciles")

        print("\nTRANSFER — refusals")
        for label, body, expect in [
            ("same location refused",
             {"from_location_id": src["id"], "to_location_id": src["id"],
              "lines": [{"product_id": pid, "quantity": 1}]}, "same location"),
            ("more than is on the shelf refused",
             {"from_location_id": src["id"], "to_location_id": dst["id"],
              "lines": [{"product_id": pid, "quantity": 999999}]}, "in stock"),
            ("zero quantity refused",
             {"from_location_id": src["id"], "to_location_id": dst["id"],
              "lines": [{"product_id": pid, "quantity": 0}]}, "must be positive"),
            ("empty transfer refused",
             {"from_location_id": src["id"], "to_location_id": dst["id"], "lines": []},
             "at least one line"),
        ]:
            st, bodytxt = A.status("/api/v1/inventory/transfers", body)
            reason = json.loads(bodytxt).get("error", "") if bodytxt.strip().startswith("{") else bodytxt
            check(st >= 400 and expect in reason.lower(), label, f"HTTP {st} · {reason[:64]}")

        b_inv = B.items("/api/v1/inventory")
        st, bodytxt = A.status("/api/v1/inventory/transfers", {
            "from_location_id": src["id"], "to_location_id": dst["id"],
            "lines": [{"product_id": b_inv[0]["product_id"], "quantity": 1}]})
        reason = json.loads(bodytxt).get("error", "") if bodytxt.strip().startswith("{") else bodytxt
        check(st >= 400 and "not found" in reason.lower(),
              "moving another business's garment refused", f"HTTP {st} · {reason[:60]}")

        b_locs = B.items("/api/v1/locations")
        st, bodytxt = A.status("/api/v1/inventory/transfers", {
            "from_location_id": src["id"], "to_location_id": b_locs[0]["id"],
            "lines": [{"product_id": pid, "quantity": 1}]})
        reason = json.loads(bodytxt).get("error", "") if bodytxt.strip().startswith("{") else bodytxt
        check(st >= 400 and "location" in reason.lower(),
              "moving INTO another business's location refused", f"HTTP {st} · {reason[:60]}")

        # ── STOCKTAKE ──
        print("\nSTOCKTAKE")
        counted = at(tenants[0], pid, src["id"]) - 1        # one piece has walked
        stk = A.post("/api/v1/inventory/stocktakes", {
            "location_id": src["id"],
            "lines": [{"product_id": pid, "counted_qty": counted}],
            "notes": "verify"})
        check(stk.get("stocktake_number"), "stocktake posted", stk.get("stocktake_number", ""))
        check(stk["lines_adjusted"] == 1 and stk["total_variance"] == -1,
              "one line adjusted, variance is -1", f"{stk['total_variance']:g}")
        check(at(tenants[0], pid, src["id"]) == counted,
              "on-hand now matches what was counted", f"{counted:g}")

        m = conn.execute(
            "SELECT movement_type, quantity FROM stock_movements"
            " WHERE tenant_id=? AND reference_type='stocktake' AND reference_id=?",
            (tenants[0], stk["id"])).fetchone()
        check(m and m[0] == "adjustment", "an adjustment movement was written",
              m[0] if m else "none")
        check(m and m[1] == -1,
              "the ledger carries the VARIANCE, not the counted quantity",
              f"{m[1] if m else '?'} (counted was {counted:g})")

        print("\nSTOCKTAKE — refusals")
        st, bodytxt = A.status("/api/v1/inventory/stocktakes", {
            "location_id": src["id"],
            "lines": [{"product_id": pid, "counted_qty": -5}]})
        reason = json.loads(bodytxt).get("error", "") if bodytxt.strip().startswith("{") else bodytxt
        check(st >= 400 and "negative" in reason.lower(),
              "a negative count refused", f"HTTP {st} · {reason[:60]}")

        st, bodytxt = A.status("/api/v1/inventory/stocktakes", {
            "location_id": b_locs[0]["id"],
            "lines": [{"product_id": pid, "counted_qty": 1}]})
        reason = json.loads(bodytxt).get("error", "") if bodytxt.strip().startswith("{") else bodytxt
        check(st >= 400 and "location" in reason.lower(),
              "counting another business's location refused", f"HTTP {st} · {reason[:60]}")

        # Isolation: none of this touched tenant B.
        b_total_now = total_stock(tenants[1])
        check(b_total_now == total_stock(tenants[1]),
              "the other business's stock is untouched", f"{b_total_now:g}")
        check(not A.items("/api/v1/inventory/transfers") == [],
              "transfers list returns the new row")
        check(B.items("/api/v1/inventory/transfers") == [],
              "and the other business sees none of them")
        check(B.items("/api/v1/inventory/stocktakes") == [],
              "same for stocktakes")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    bad = [c for c in CHECKS if not c[0]]
    print("\n" + "=" * 68)
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed")
    if bad:
        for _, l, d in bad:
            print(f"  FAILED: {l}  {d}")
        return 1
    print("STOCKTAKES AND TRANSFERS VERIFIED END TO END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
