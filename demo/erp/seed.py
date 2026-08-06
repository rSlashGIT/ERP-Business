"""Seed two apparel businesses with realistic Indian retail data.

Two tenants deliberately share a style code, a barcode, a location code and a
PO number, because real retailers do. The demo proves neither can see the
other's rows.
"""
from __future__ import annotations

import random
import sqlite3
import uuid
from datetime import date, timedelta

from .schema import SCHEMA
from .sizes import size_seq

# Deliberately colliding natural keys
SHARED_STYLE_CODE = "SS26-001"
SHARED_BARCODE = "8901234567890"
SHARED_LOCATION = "STORE-01"

TENANTS = [
    {
        "id": "kurta-house", "name": "Kurta House",
        "legal_name": "Kurta House Retail Pvt Ltd", "gstin": "29AAJCK2290M1ZY",
        "state_code": "29", "address": "42, Commercial Street, Bengaluru 560001",
        "phone": "+91 98450 11111", "email": "billing@kurtahouse.in",
        "styles": [
            ("SS26-001", "Cotton Kurta", "Rangeela", "Ethnic Wear", "6205", 1499,
             ["S", "M", "L", "XL", "XXL"],
             ["Ivory", "Indigo", "Sage"]),
            ("SS26-014", "Silk Blend Kurta", "Rangeela", "Ethnic Wear", "6205", 2899,
             ["M", "L", "XL"], ["Maroon", "Gold"]),
            ("SS26-032", "Chikankari Dupatta", "Rangeela", "Accessories", "6214", 899,
             ["FREE"], ["White", "Peach", "Mint"]),
            ("SS26-047", "Nehru Jacket", "Rangeela", "Ethnic Wear", "6203", 3499,
             ["38", "40", "42"], ["Black", "Navy"]),
        ],
    },
    {
        "id": "denim-depot", "name": "Denim Depot",
        "legal_name": "Denim Depot Trading Co", "gstin": "27AAPCS2214L1Z2",
        "state_code": "27", "address": "18, Linking Road, Mumbai 400050",
        "phone": "+91 98200 22222", "email": "accounts@denimdepot.in",
        "styles": [
            ("SS26-001", "Slim Fit Jeans", "RiveRaw", "Denim", "6203", 2799,
             ["30", "32", "34", "36"],
             ["Stone", "Indigo", "Black"]),
            ("SS26-021", "Denim Jacket", "RiveRaw", "Outerwear", "6201", 3999,
             ["M", "L", "XL"], ["Light Wash", "Dark Wash"]),
            ("SS26-055", "Cotton Tee", "RiveRaw", "T-Shirts", "6109", 799,
             ["S", "M", "L", "XL"], ["White", "Black", "Olive"]),
        ],
    },
]

CUSTOMERS = {
    "kurta-house": [
        ("CASH", "Walk-in Customer", None, None, "29", 0, 0, 1),
        ("C-1001", "Meera Traders", "+91 98860 33001", "29AAGCS4471M1ZQ", "29", 200000, 30, 0),
        ("C-1002", "Anjali Boutique", "+91 98860 33002", "29AICPA8823Q1ZS", "29", 150000, 21, 0),
        ("C-1003", "Deccan Ethnic Mart", "+91 98490 33003", "36AAKCD7712F1ZN", "36", 300000, 45, 0),
        ("C-1004", "Priya Sharma", "+91 98860 33004", None, "29", 0, 0, 0),
    ],
    "denim-depot": [
        ("CASH", "Walk-in Customer", None, None, "27", 0, 0, 1),
        ("C-2001", "Urban Threads", "+91 98200 44001", "27AAACA3945N1ZF", "27", 400000, 30, 0),
        ("C-2002", "Style Bazaar", "+91 98200 44002", None, "27", 100000, 15, 0),
        ("C-2003", "Coastal Denim Co", "+91 90000 44003", "29AAECB4471T1ZV", "29", 250000, 30, 0),
    ],
}

SUPPLIERS = {
    "kurta-house": [("SUP-001", "Jaipur Textiles", 12), ("SUP-002", "Lucknow Chikan Works", 21)],
    "denim-depot": [("SUP-001", "Ahmedabad Denim Mills", 9), ("SUP-002", "Tirupur Knits", 6)],
}


def _uid() -> str:
    return str(uuid.uuid4())


def seed(conn: sqlite3.Connection, rng_seed: int = 20260804) -> dict:
    rng = random.Random(rng_seed)
    conn.executescript(SCHEMA)
    today = date.today()
    stats = {"tenants": 0, "styles": 0, "variants": 0, "customers": 0, "invoices": 0}

    for t in TENANTS:
        conn.execute(
            "INSERT OR REPLACE INTO tenants(id,name,legal_name,gstin,state_code,address,"
            "phone,email) VALUES(?,?,?,?,?,?,?,?)",
            (t["id"], t["name"], t["legal_name"], t["gstin"], t["state_code"],
             t["address"], t["phone"], t["email"]))
        stats["tenants"] += 1

        loc_id = _uid()
        conn.execute("INSERT INTO locations(id,tenant_id,code,name,type) VALUES(?,?,?,?,?)",
                     (loc_id, t["id"], SHARED_LOCATION, f"{t['name']} — Main Store", "store"))
        wh_id = _uid()
        conn.execute("INSERT INTO locations(id,tenant_id,code,name,type) VALUES(?,?,?,?,?)",
                     (wh_id, t["id"], "WH-01", f"{t['name']} — Warehouse", "warehouse"))

        sup_ids = []
        for code, name, lead in SUPPLIERS[t["id"]]:
            sid = _uid(); sup_ids.append(sid)
            conn.execute("INSERT INTO suppliers(id,tenant_id,code,name,contract_lead_days)"
                         " VALUES(?,?,?,?,?)", (sid, t["id"], code, name, lead))

        cust_ids = {}
        for code, name, phone, gstin, state, limit, days, walkin in CUSTOMERS[t["id"]]:
            cid = _uid(); cust_ids[code] = cid
            conn.execute(
                "INSERT INTO customers(id,tenant_id,code,name,phone,gstin,state_code,"
                "credit_limit,credit_days,is_walkin) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, t["id"], code, name, phone, gstin, state, limit, days, walkin))
            stats["customers"] += 1

        variants = []
        first_variant_done = False
        for scode, sname, brand, cat, hsn, base_price, sizes, colours in t["styles"]:
            style_id = _uid()
            conn.execute(
                "INSERT INTO product_styles(id,tenant_id,style_code,name,brand,category,"
                "season,hsn_code) VALUES(?,?,?,?,?,?,?,?)",
                (style_id, t["id"], scode, sname, brand, cat, "SS26", hsn))
            stats["styles"] += 1
            for size in sizes:
                seq = size_seq(size)
                for colour in colours:
                    pid = _uid()
                    # size premium: larger sizes cost slightly more, which is
                    # what pushes some variants across the GST threshold
                    # small size premium, so some variants cross the GST threshold
                    premium = ((seq or 40) - 40) * 4 if (seq or 0) < 500 else 0
                    price = round(base_price + premium, 2)
                    price = max(price, 99.0)
                    sku = f"{scode}-{size}-{colour[:3].upper()}"
                    bc = (SHARED_BARCODE if not first_variant_done
                          else f"890{rng.randint(1000000000, 9999999999)}")
                    first_variant_done = True
                    conn.execute(
                        "INSERT INTO products(id,tenant_id,sku,name,style_id,size,size_seq,"
                        "colour,barcode,hsn_code,unit_cost,unit_price) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, t["id"], sku, f"{sname} · {size} · {colour}", style_id,
                         size, seq, colour, bc, hsn, round(price * 0.52, 2), price))
                    variants.append((pid, price, hsn))
                    stats["variants"] += 1

                    for lid, share in ((loc_id, 0.7), (wh_id, 0.3)):
                        qty = max(0, int(rng.gauss(26, 14) * share))
                        rop = max(4, int(10 * share))
                        conn.execute(
                            "INSERT INTO inventory_levels(id,tenant_id,product_id,location_id,"
                            "on_hand,reorder_point,order_up_to,safety_stock) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (_uid(), t["id"], pid, lid, qty, rop, rop * 4, rop))

        # ── history: a full year of trading ──
        #
        # 365 days, not 45, and with REAL discount variation, because the price
        # engine estimates each style's elasticity from what the shop actually
        # charged. Forty-five days at a flat 0/5/10% gives two or three price
        # points per style, which is not a demand curve — it is a rumour. The
        # sale periods below are what make the elasticity measurable, and they
        # are what a real shop's ledger looks like anyway.
        from .billing import post_invoice
        codes = [c for c in cust_ids if c != "CASH"]

        def discount_on(day: date) -> int:
            """Indian apparel retail runs on predictable sale seasons."""
            m, dom = day.month, day.day
            if m == 1 and dom <= 20:            # new year clearance
                return rng.choice([30, 35, 40, 40, 45])
            if m == 7:                          # end of season sale
                return rng.choice([25, 30, 30, 35, 40])
            if m in (9, 10):                    # festive: full price, it sells anyway
                return rng.choice([0, 0, 0, 0, 5])
            if m == 12 and dom >= 20:           # christmas / year end
                return rng.choice([15, 20, 25])
            return rng.choice([0, 0, 0, 0, 5, 5, 10, 15])

        def footfall(day: date, disc: int) -> int:
            """More people walk in during a sale and during the festive season."""
            base = 2.2
            if day.month in (9, 10):
                base = 3.6                       # Navratri / Diwali
            if day.weekday() >= 5:
                base *= 1.5                      # weekend
            base *= 1 + disc / 60.0              # a sale pulls a crowd
            return max(0, int(rng.gauss(base, 1.1)))

        for d in range(365, 0, -1):
            when = today - timedelta(days=d)
            disc = discount_on(when)
            for _ in range(footfall(when, disc)):
                ccode = "CASH" if rng.random() < 0.45 else rng.choice(codes)
                picks = rng.sample(variants, rng.randint(1, 4))
                # A sale moves more pieces per bill as well as more bills.
                qmax = 3 if disc < 20 else 5
                lines = [{"product_id": p, "quantity": rng.randint(1, qmax),
                          "discount_pct": max(0, disc + rng.choice([-5, 0, 0, 0, 5]))}
                         for p, _pr, _h in picks]
                try:
                    post_invoice(conn, t["id"], cust_ids[ccode], loc_id, lines,
                                 invoice_date=when.isoformat(), created_by="seed",
                                 auto_pay=(ccode == "CASH"),
                                 allow_negative_stock=True, commit=False)
                    stats["invoices"] += 1
                except Exception:
                    pass

        conn.commit()          # one commit for the whole year, not four thousand

        # A year of selling would have emptied the shelves; restock to a
        # plausible present-day position so the opening screens make sense.
        for pid, _pr, _h in variants:
            conn.execute(
                "UPDATE inventory_levels SET on_hand=? WHERE tenant_id=? AND product_id=?",
                (rng.randint(0, 26), t["id"], pid))
        conn.commit()

        # ── AI replenishment recommendations for what is short ──
        rows = conn.execute(
            "SELECT i.product_id, i.location_id, i.on_hand, i.reorder_point, i.order_up_to,"
            " p.unit_cost, p.name FROM inventory_levels i JOIN products p ON p.id=i.product_id"
            " WHERE i.tenant_id=? AND i.on_hand <= i.reorder_point", (t["id"],)).fetchall()
        for r in rows[:40]:
            qty = max(1, int((r[4] or 20) - r[2]))
            cover = r[2] / max(0.6, r[3] / 7)
            urgency = ("critical" if r[2] <= 0 else "high" if cover < 4
                       else "medium" if cover < 8 else "low")
            conn.execute(
                "INSERT INTO recommendations(id,tenant_id,product_id,location_id,supplier_id,"
                "recommended_qty,unit_cost,line_value,urgency,confidence,status,rationale,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), t["id"], r[0], r[1], sup_ids[0], qty, r[5], round(qty * r[5], 2),
                 urgency, round(rng.uniform(0.55, 0.93), 2), "pending",
                 f'{{"reorder_point": {r[3]}, "on_hand": {r[2]}, '
                 f'"days_cover": {cover:.1f}, "order_up_to": {r[4]}}}',
                 today.isoformat()))

        # ── purchase orders already placed and still in transit ──
        # Without these the Receiving screen opens empty, and "we have not
        # taken delivery of anything yet" is not a state a real shop is ever
        # in. One PO part-delivered, one fully outstanding.
        from .billing import next_number
        pend = conn.execute(
            "SELECT product_id, location_id, recommended_qty, unit_cost"
            " FROM recommendations WHERE tenant_id=? ORDER BY line_value DESC LIMIT 9",
            (t["id"],)).fetchall()
        for k in range(0, min(9, len(pend)), 3):
            chunk = pend[k:k + 3]
            if not chunk:
                break
            po_id = _uid()
            num = next_number(conn, t["id"], "purchase_orders", "po_number", "PO")
            placed = today - timedelta(days=rng.randint(3, 20))
            conn.execute(
                "INSERT INTO purchase_orders(id,tenant_id,po_number,supplier_id,"
                "location_id,status,total_value,approved_by,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (po_id, t["id"], num, sup_ids[k % len(sup_ids)], chunk[0][1],
                 "approved", 0.0, "buyer", placed.isoformat()))
            total = 0.0
            for j, row in enumerate(chunk, start=1):
                qty = max(2, int(row[2]))
                val = round(qty * row[3], 2)
                total += val
                conn.execute(
                    "INSERT INTO purchase_order_lines(id,tenant_id,purchase_order_id,"
                    "product_id,line_no,ai_recommended_qty,ordered_qty,unit_cost,"
                    "line_value,received_qty) VALUES(?,?,?,?,?,?,?,?,?,0)",
                    (_uid(), t["id"], po_id, row[0], j, row[2], qty, row[3], val))
                conn.execute(
                    "UPDATE inventory_levels SET on_order=on_order+? WHERE tenant_id=?"
                    " AND product_id=? AND location_id=?",
                    (qty, t["id"], row[0], row[1]))
            conn.execute("UPDATE purchase_orders SET total_value=? WHERE id=?",
                         (round(total, 2), po_id))

        # ── historical supplier bills & payables ──
        from .payables import post_supplier_bill, record_supplier_payment
        for k, (scode, sname, _lead) in enumerate(SUPPLIERS[t["id"]]):
            sid = sup_ids[k]
            # Create a couple of historical bills
            if not variants: continue
            v1 = variants[k % len(variants)]
            v2 = variants[(k + 1) % len(variants)]
            
            lines1 = [{"product_id": v1[0], "quantity": rng.randint(20, 100), "unit_price": v1[1] * 0.52}]
            bill1 = post_supplier_bill(
                conn, t["id"], sid, loc_id, lines1,
                supplier_invoice_number=f"INV-{rng.randint(1000, 9999)}",
                bill_date=(today - timedelta(days=rng.randint(40, 60))).isoformat(),
                commit=False
            )
            
            lines2 = [{"product_id": v2[0], "quantity": rng.randint(20, 100), "unit_price": v2[1] * 0.52}]
            bill2 = post_supplier_bill(
                conn, t["id"], sid, loc_id, lines2,
                supplier_invoice_number=f"INV-{rng.randint(1000, 9999)}",
                bill_date=(today - timedelta(days=rng.randint(5, 20))).isoformat(),
                commit=False
            )
            
            # Settle the older bill fully, leave the newer one open
            record_supplier_payment(conn, t["id"], sid, float(bill1["grand_total"]), paid_by="seed")

    conn.commit()
    return stats
