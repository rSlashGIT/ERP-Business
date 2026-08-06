#!/usr/bin/env python3
"""
Runnable ERP demo — the thing you show a shopkeeper.

    python3 demo/erp_server.py
    → http://127.0.0.1:8500

Needs only Python 3.10+. No pip install, no npm, no database server, no
internet. That is deliberate: it runs on a shop's laptop, on a client's
machine, or on a plane.

WHAT IT IS
----------
A working multi-tenant apparel ERP: billing with live GST, inventory by
size/colour variant, customers, receivables ageing, AI replenishment approval,
and reports. Two businesses are seeded — Kurta House (Bengaluru) and Denim
Depot (Mumbai) — and you can switch between them in the UI to show that
neither can see the other's data.

RELATIONSHIP TO PRODUCTION
--------------------------
The GST engine (services/erp-api/app/domain/gst.py) is the SAME module the
FastAPI routes use — tax rules are never reimplemented. Persistence and
transport differ: production is PostgreSQL + SQLAlchemy + FastAPI, this is
sqlite + http.server, because neither is installable in the dev sandbox.
See AGENTS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "erp-api"))
sys.path.insert(0, str(ROOT / "demo"))

from erp import api as ERP          # noqa: E402
from erp.billing import BillingError, post_invoice, record_payment  # noqa: E402
from erp.importer import ImportError_, analyse_for_tenant  # noqa: E402
from erp.importer import commit as import_commit          # noqa: E402
from erp.inventory import InventoryError, get_stocktakes, get_transfers, post_stocktake, post_transfer # noqa: E402
from erp.gst_export import (available_months, gstr1_csv, gstr1_summary,  # noqa: E402
                            gstr3b_csv, gstr3b_summary)
from erp.labels import render_sheet  # noqa: E402
from erp.payables import PayablesError, post_supplier_bill, record_supplier_payment, payables, supplier_bills # noqa: E402
from erp.prices import apply_price, price_advice, price_detail  # noqa: E402
from erp.receiving import (ReceivingError, open_purchase_orders,  # noqa: E402
                           purchase_order_detail, receipts, receive)
from erp.returns import (ReturnError, create_credit_note,  # noqa: E402
                         credit_notes, returnable)
from erp.seed import seed           # noqa: E402

DB_PATH = Path(os.getenv("ERP_DEMO_DB", ROOT / "demo" / "erp_demo.db"))
UI = ROOT / "apps" / "console" / "erp.html"


def connect() -> sqlite3.Connection:
    """Open the database, picking a journal mode that actually works here.

    WAL is the fast choice, but SQLite documents it as unsupported on network
    filesystems — and a shop will absolutely put this folder on a mapped drive
    or inside OneDrive or Dropbox. There the WAL pragma SUCCEEDS and then every
    later write dies with "disk I/O error", so the app looks broken on every
    screen at once with no clue why. That is exactly what happened here.

    So a mode is not trusted because the pragma returned. It is trusted because
    a real write, a real read-back and a real commit all worked.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    for mode in ("WAL", "TRUNCATE", "DELETE"):
        try:
            conn.execute(f"PRAGMA journal_mode={mode}")
            conn.execute("CREATE TABLE IF NOT EXISTS _probe(x INTEGER)")
            conn.execute("INSERT INTO _probe(x) VALUES(1)")
            conn.commit()
            conn.execute("SELECT x FROM _probe LIMIT 1").fetchone()
            conn.execute("DROP TABLE _probe")
            conn.commit()
            if mode != "WAL":
                print(f"  (using {mode} journal — this looks like a network drive)")
            return conn
        except sqlite3.OperationalError:
            try:
                conn.rollback()
            except Exception:
                pass
            continue
    raise SystemExit(
        f"\n  Cannot write to {DB_PATH}\n"
        "  Move this folder onto a local drive — not a network drive, and not\n"
        "  inside OneDrive or Dropbox — then start it again.\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "ERPDemo/1.0"
    conn: sqlite3.Connection

    def log_message(self, fmt, *args):
        if os.getenv("ERP_VERBOSE"):
            super().log_message(fmt, *args)

    # ── tenant resolution ──
    def tenant(self) -> str:
        """Tenant comes from a header, never from a query parameter.

        In production this is a claim inside a signed JWT (see
        app/security/core.py). The demo uses a header so the UI can switch
        businesses, but the principle is identical: NO query or body value ever
        selects a tenant, so a crafted URL cannot reach another business.
        """
        t = self.headers.get("X-Tenant") or "kurta-house"
        row = self.conn.execute("SELECT id FROM tenants WHERE id=?", (t,)).fetchone()
        if row is None:
            raise ValueError(f"unknown tenant '{t}'")
        return row[0]

    def send_json(self, code: int, body):
        raw = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tenant")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        for h, v in (("Access-Control-Allow-Origin", "*"),
                     ("Access-Control-Allow-Headers", "Content-Type, X-Tenant"),
                     ("Access-Control-Allow-Methods", "GET,POST,OPTIONS")):
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: unquote(v[0]) for k, v in parse_qs(u.query).items()}
        p = u.path
        try:
            if p in ("/", "/index.html"):
                html = UI.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if p == "/api/v1/labels/print":
                # Serves a PAGE, not JSON: the UI opens it in a tab and the
                # shop prints from the browser it already has.
                t = self.tenant()
                ids = [x for x in (q.get("product_ids", "").split(",")) if x]
                page = render_sheet(
                    self.conn, t, product_ids=ids, po_id=q.get("po_id") or None,
                    sheet=q.get("sheet", "l7159"), copies=int(q.get("copies", 1) or 1))
                raw = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if p in ("/api/v1/tax/gstr1.csv", "/api/v1/tax/gstr3b.csv"):
                t = self.tenant()
                month = q.get("month", "")
                if not month:
                    return self.send_json(400, {"error": "month is required, e.g. 2026-07"})
                body = (gstr1_csv if p.endswith("gstr1.csv") else gstr3b_csv)(
                    self.conn, t, month)
                name = ("GSTR1" if p.endswith("gstr1.csv") else "GSTR3B") + f"-{month}.csv"
                raw = body.encode("utf-8-sig")     # BOM so Excel opens it correctly
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if p == "/api/v1/tenants":
                return self.send_json(200, {"items": ERP._rows(
                    self.conn, "SELECT id,name,gstin,state_code,address,phone FROM tenants")})

            t = self.tenant()
            routes = {
                "/api/v1/dashboard": lambda: ERP.dashboard(self.conn, t),
                "/api/v1/styles": lambda: ERP.styles(self.conn, t, q.get("search", ""),
                                                     q.get("category", "")),
                "/api/v1/inventory": lambda: ERP.inventory(self.conn, t, q.get("search", ""),
                                                           q.get("low_only", ""),
                                                           q.get("location", "")),
                "/api/v1/products/search": lambda: {"items": ERP.search_products(
                    self.conn, t, q.get("q", ""))},
                "/api/v1/customers": lambda: ERP.customers(self.conn, t, q.get("search", "")),
                "/api/v1/sales/invoices": lambda: ERP.invoices(self.conn, t,
                                                               q.get("status", ""),
                                                               q.get("search", "")),
                "/api/v1/sales/receivables": lambda: ERP.receivables(self.conn, t),
                "/api/v1/procurement/recommendations": lambda: ERP.recommendations(
                    self.conn, t, q.get("status", "pending")),
                "/api/v1/procurement/purchase-orders": lambda: ERP.purchase_orders(self.conn, t),
                "/api/v1/procurement/payables": lambda: payables(self.conn, t),
                "/api/v1/procurement/bills": lambda: supplier_bills(self.conn, t, q.get("status", "")),
                "/api/v1/locations": lambda: {"items": ERP._rows(
                    self.conn, "SELECT id,code,name,type FROM locations WHERE tenant_id=?", (t,))},
                "/api/v1/reports": lambda: ERP.reports(self.conn, t, q.get("kind", ""),
                                                       int(q.get("days", 90))),
                "/api/v1/prices": lambda: price_advice(
                    self.conn, t, int(q.get("days_left", 90))),
                "/api/v1/receiving/open-pos": lambda: open_purchase_orders(self.conn, t),
                "/api/v1/receiving/receipts": lambda: receipts(self.conn, t),
                "/api/v1/returns": lambda: credit_notes(self.conn, t),
                "/api/v1/tax/months": lambda: {"items": available_months(self.conn, t)},
                "/api/v1/tax/summary": lambda: {
                    "gstr1": gstr1_summary(self.conn, t, q.get("month", "")),
                    "gstr3b": gstr3b_summary(self.conn, t, q.get("month", ""))},
                "/api/v1/inventory/stocktakes": lambda: get_stocktakes(self.conn, t),
                "/api/v1/inventory/transfers": lambda: get_transfers(self.conn, t),
            }
            if p in routes:
                return self.send_json(200, routes[p]())
            if p.startswith("/api/v1/sales/invoices/"):
                inv = ERP.invoice_detail(self.conn, t, p.rsplit("/", 1)[-1])
                return self.send_json(200 if inv else 404,
                                      inv or {"error": "invoice not found"})
            if p.startswith("/api/v1/receiving/po/"):
                d = purchase_order_detail(self.conn, t, p.rsplit("/", 1)[-1])
                return self.send_json(200 if d else 404, d or {"error": "unknown order"})
            if p.startswith("/api/v1/returns/returnable/"):
                d = returnable(self.conn, t, p.rsplit("/", 1)[-1])
                return self.send_json(200 if d else 404,
                                      d or {"error": "that invoice cannot be returned"})
            if p.startswith("/api/v1/prices/"):
                d = price_detail(self.conn, t, p.rsplit("/", 1)[-1],
                                 int(q.get("days_left", 90)))
                return self.send_json(200 if d else 404,
                                      d or {"error": "unknown style"})
            if p.startswith("/api/v1/barcode/"):
                v = ERP.lookup_barcode(self.conn, t, p.rsplit("/", 1)[-1])
                return self.send_json(200 if v else 404,
                                      v or {"error": "no variant with that barcode"})
            return self.send_json(404, {"error": "not_found", "path": p})
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self.send_json(500, {"error": "internal_error", "detail": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            # A chunked POST reads as zero bytes here and would otherwise be
            # reported as "your sheet is empty" — blame the transport, not the
            # user's data.
            if not n and self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                return self.send_json(411, {"error":
                    "this request was sent without a Content-Length header, "
                    "so the server could not read it"})
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self.send_json(400, {"error": "bad_json", "detail": str(e)})
        try:
            t = self.tenant()
            if u.path == "/api/v1/sales/invoices":
                inv = post_invoice(
                    self.conn, t, body["customer_id"], body["location_id"],
                    body.get("lines", []), created_by=body.get("created_by", "counter"),
                    notes=body.get("notes", ""), auto_pay=bool(body.get("auto_pay")))
                return self.send_json(201, inv)
            if u.path == "/api/v1/sales/payments":
                return self.send_json(201, record_payment(
                    self.conn, t, body["customer_id"], float(body["amount"]),
                    body.get("method", "cash"), body.get("reference", ""),
                    body.get("received_by", "counter")))
            if u.path == "/api/v1/procurement/bills":
                bill = post_supplier_bill(
                    self.conn, t, body["supplier_id"], body["location_id"],
                    body.get("lines", []),
                    supplier_invoice_number=body.get("supplier_invoice_number", ""),
                    goods_receipt_id=body.get("goods_receipt_id"),
                    created_by=body.get("created_by", "backoffice"),
                    notes=body.get("notes", ""), auto_pay=bool(body.get("auto_pay")))
                return self.send_json(201, bill)
            if u.path == "/api/v1/procurement/payments":
                return self.send_json(201, record_supplier_payment(
                    self.conn, t, body["supplier_id"], float(body["amount"]),
                    body.get("method", "bank"), body.get("reference", ""),
                    body.get("paid_by", "backoffice")))
            if u.path == "/api/v1/customers":
                import uuid as _u
                cid = str(_u.uuid4())
                code = body.get("code") or f"C-{_u.uuid4().hex[:6].upper()}"
                self.conn.execute(
                    "INSERT INTO customers(id,tenant_id,code,name,phone,gstin,state_code,"
                    "credit_limit,credit_days) VALUES(?,?,?,?,?,?,?,?,?)",
                    (cid, t, code, body["name"], body.get("phone"), body.get("gstin"),
                     body.get("state_code"), float(body.get("credit_limit") or 0),
                     int(body.get("credit_days") or 0)))
                self.conn.commit()
                return self.send_json(201, {"id": cid, "code": code, "name": body["name"]})
            if u.path == "/api/v1/procurement/recommendations/decide":
                return self.send_json(200, self._decide(t, body))
            # Analyse writes NOTHING. The shopkeeper sees every problem in
            # their own data before agreeing to load any of it.
            if u.path == "/api/v1/import/analyse":
                a = analyse_for_tenant(self.conn, t, body.get("text", ""),
                                       body.get("mapping"))
                return self.send_json(200, a.as_dict())
            if u.path == "/api/v1/receiving/receive":
                return self.send_json(201, receive(
                    self.conn, t, body.get("purchase_order_id"), body.get("lines", []),
                    location_id=body.get("location_id"),
                    supplier_invoice=body.get("supplier_invoice", ""),
                    received_by=body.get("received_by", "store"),
                    notes=body.get("notes", "")))
            if u.path == "/api/v1/returns/create":
                return self.send_json(201, create_credit_note(
                    self.conn, t, body["invoice_id"], body.get("lines", []),
                    reason=body.get("reason", ""),
                    refund_mode=body.get("refund_mode", "credit"),
                    created_by=body.get("created_by", "counter")))
            if u.path == "/api/v1/prices/apply":
                return self.send_json(200, apply_price(
                    self.conn, t, body["style_id"], float(body["taxable"])))
            if u.path == "/api/v1/import/commit":
                return self.send_json(201, import_commit(
                    self.conn, t, body.get("text", ""), body.get("mapping"),
                    body.get("location_code")))
            if u.path == "/api/v1/inventory/stocktakes":
                return self.send_json(201, post_stocktake(
                    self.conn, t, body["location_id"], body.get("lines", []),
                    notes=body.get("notes", "")))
            if u.path == "/api/v1/inventory/transfers":
                return self.send_json(201, post_transfer(
                    self.conn, t, body["from_location_id"], body["to_location_id"],
                    body.get("lines", []), notes=body.get("notes", "")))
            return self.send_json(404, {"error": "not_found", "path": u.path})
        except (BillingError, ImportError_, ReceivingError, ReturnError, PayablesError, InventoryError) as e:
            return self.send_json(422, {"error": str(e)})
        except KeyError as e:
            return self.send_json(422, {"error": f"missing field {e}"})
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self.send_json(500, {"error": "internal_error", "detail": str(e)})

    def _decide(self, t: str, body: dict) -> dict:
        """Approve / modify / reject AI recommendations, emitting grouped POs."""
        import uuid as _u
        from datetime import date as _d, datetime as _dt
        decisions = body.get("decisions") or []
        actor = body.get("actor", "buyer")
        approved, n_app, n_mod, n_rej, errors = [], 0, 0, 0, []
        for d in decisions:
            r = self.conn.execute(
                "SELECT * FROM recommendations WHERE id=? AND tenant_id=?",
                (d.get("id"), t)).fetchone()
            if r is None:
                errors.append({"id": d.get("id"), "error": "not found"}); continue
            cols = [c[0] for c in self.conn.execute(
                "SELECT * FROM recommendations LIMIT 0").description]
            row = dict(zip(cols, r))
            if row["status"] != "pending":
                errors.append({"id": row["id"], "error": f"already {row['status']}"}); continue
            act = d.get("action")
            if act == "reject":
                self.conn.execute("UPDATE recommendations SET status='rejected',final_qty=0"
                                  " WHERE id=?", (row["id"],)); n_rej += 1
            else:
                qty = float(d.get("final_qty") if act == "modify" else row["recommended_qty"])
                if qty <= 0:
                    self.conn.execute("UPDATE recommendations SET status='rejected',final_qty=0"
                                      " WHERE id=?", (row["id"],)); n_rej += 1; continue
                self.conn.execute("UPDATE recommendations SET status=?,final_qty=? WHERE id=?",
                                  ("modified" if act == "modify" else "approved", qty, row["id"]))
                n_mod += act == "modify"; n_app += act == "approve"
                approved.append((row, qty))
        created = []
        groups: dict = {}
        for row, qty in approved:
            groups.setdefault((row["supplier_id"], row["location_id"]), []).append((row, qty))
        seq = self.conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE tenant_id=?",
                                (t,)).fetchone()[0]
        for (sup, loc), items in groups.items():
            seq += 1
            po_id = str(_u.uuid4())
            num = f"PO/{_d.today():%Y%m}/{seq:04d}"
            total = 0.0
            self.conn.execute(
                "INSERT INTO purchase_orders(id,tenant_id,po_number,supplier_id,location_id,"
                "status,total_value,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (po_id, t, num, sup, loc, "approved", 0, actor,
                 _dt.now().isoformat(timespec="seconds")))
            for i, (row, qty) in enumerate(items, 1):
                val = qty * (row["unit_cost"] or 0)
                total += val
                self.conn.execute(
                    "INSERT INTO purchase_order_lines(id,tenant_id,purchase_order_id,"
                    "product_id,line_no,ai_recommended_qty,ordered_qty,unit_cost,line_value)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(_u.uuid4()), t, po_id, row["product_id"], i,
                     row["recommended_qty"], qty, row["unit_cost"], val))
                # Ordered stock is inbound, not on hand.
                self.conn.execute(
                    "UPDATE inventory_levels SET on_order=on_order+? WHERE tenant_id=?"
                    " AND product_id=? AND location_id=?",
                    (qty, t, row["product_id"], row["location_id"]))
            self.conn.execute("UPDATE purchase_orders SET total_value=? WHERE id=?",
                              (round(total, 2), po_id))
            created.append(num)
        self.conn.commit()
        return {"approved": n_app, "modified": n_mod, "rejected": n_rej,
                "purchase_orders_created": created, "errors": errors}


def main() -> int:
    # Windows consoles default to cp1252, and this file prints box-drawing and
    # rupee characters. Without this the server dies with UnicodeEncodeError
    # before it ever binds a port — on Windows only, which is exactly where a
    # shopkeeper will run it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Runnable multi-tenant apparel ERP demo")
    ap.add_argument("--port", type=int, default=8500)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reseed", action="store_true", help="rebuild the demo database")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--open", action="store_true", help="open the browser automatically")
    args = ap.parse_args()

    fresh = args.reseed or not DB_PATH.exists()
    if args.reseed and DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect()
    if fresh:
        print("Seeding a year of trading, about ten seconds ...", flush=True)
        # Seeding posts roughly 4,000 invoices and post_invoice commits each
        # one. On a local disk that is a few seconds; on a network drive or a
        # mounted share every commit fsyncs and the same seed takes minutes.
        # The demo database is regenerable by definition, so durability while
        # seeding buys nothing. Normal safety is restored immediately after.
        conn.execute("PRAGMA synchronous=OFF")
        st = seed(conn)
        conn.execute("PRAGMA synchronous=FULL")
        print(f"  {st['tenants']} businesses | {st['styles']} styles | "
              f"{st['variants']} size/colour variants | {st['customers']} customers | "
              f"{st['invoices']} invoices with live GST")
    if args.seed_only:
        return 0
    if not UI.exists():
        print(f"UI not found at {UI}", file=sys.stderr)
        return 1

    Handler.conn = conn
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f"\n  Could not start on port {args.port}: {e}")
        print(f"  Something else is probably using it. Try:")
        print(f"      python demo/erp_server.py --port {args.port + 1}\n")
        return 1

    url = f"http://{args.host}:{args.port}"
    bar = "=" * 66
    print(f"\n{bar}\n  APPAREL ERP\n{bar}")
    print(f"  Open   {url}")
    print("  Two businesses are loaded. Switch between them in the top-right -")
    print("  same barcodes, same style codes, completely separate data.")
    print(f"{bar}\n  Leave this window open. Press Ctrl-C to stop.\n")

    if args.open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
