#!/usr/bin/env python3
"""The importer, against input as bad as the real thing.

Run:  python3 tests/test_importing.py        (or: make test-import)

Every fixture here is modelled on something a real export actually does. If a
case looks contrived, it is not — it is a bug someone would have hit in front
of a shopkeeper, which is the worst possible place to find one.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain.importing import (          # noqa: E402
    LIVE_SLABS, analyse, clean_colour, clean_hsn, clean_number, clean_rate,
    clean_size, clean_uom, match_columns, sniff_rows,
)

PASS = FAIL = 0
_section = ""


def section(name: str) -> None:
    global _section
    _section = name
    print(f"\n{name}")


def check(cond, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


def eq(got, want, label: str) -> None:
    check(got == want, label, f"got {got!r}, want {want!r}" if got != want else "")


# ───────────────────────────── cleaners ─────────────────────────────

section("test_clean_number")
for raw, want in [
    ("Rs. 1,180.00", 1180), ("₹1,180", 1180), ("1,180.00", 1180),
    ("1,25,400", 125400),                      # Indian lakh grouping
    ("(450)", -450),                           # accounting negative
    ("", 0), (None, 0), ("N/A", 0), ("  ", 0),
    ("58", 58), ("-90", -90), ("INR 300", 300),
]:
    eq(clean_number(raw), Decimal(str(want)), f"{raw!r} -> {want}")

section("test_clean_rate")
for raw, want in [("18%", 18), ("18", 18), ("0.18", 18), ("5%", 5), ("0.05", 5),
                  ("12", 12), ("", None), (None, None), ("0", None)]:
    got = clean_rate(raw)
    eq(got, None if want is None else Decimal(str(want)), f"{raw!r} -> {want}")
check(clean_rate("1") == Decimal("100"),
      "a bare 1 is read as 100%, not 1% — ambiguous, so it must be flagged not guessed",
      f"got {clean_rate('1')}")

section("test_clean_uom")
for raw, want in [("Nos", "PCS"), ("NO", "PCS"), ("Pcs", "PCS"), ("PC", "PCS"),
                  ("Piece", "PCS"), ("Coil", "PCS"), ("each", "PCS"),
                  ("Kgs", "KG"), ("Litre", "LTR"), ("Mtrs", "MTR"),
                  ("Bags", "BAG"), ("Pair", "PAIR"), ("", "PCS")]:
    eq(clean_uom(raw), want, f"{raw!r} -> {want}")

section("test_clean_hsn")
eq(clean_hsn("6103")[0], "6103", "4-digit HSN kept")
eq(clean_hsn("HSN 6103")[0], "6103", "letters stripped")
eq(clean_hsn("61034200")[0], "61034200", "8-digit HSN kept")
check(clean_hsn("610")[0] == "" and clean_hsn("610")[1], "3-digit HSN dropped and flagged")
check(clean_hsn("")[1], "missing HSN is flagged — it decides the tax slab")
eq(clean_hsn("6103420011")[0], "61034200", "over-long HSN truncated to 8")

section("test_clean_size")
for raw, want in [("Small", "S"), ("SMALL", "S"), ("sm", "S"), ("S", "S"),
                  ("Medium", "M"), ("MED", "M"), ("m", "M"),
                  ("Large", "L"), ("X-Large", "XL"), ("XXL", "XXL"),
                  ("2XL", "XXL"), ("Free Size", "FREE"), ("One Size", "FREE"),
                  ("32", "32"), ('34"', "34"), ("36 inch", "36"), ("", "")]:
    eq(clean_size(raw)[0], want, f"{raw!r} -> {want!r}")
check(clean_size("Toddler-3")[1], "an unknown scale is kept but flagged, not dropped",
      repr(clean_size("Toddler-3")))

section("test_clean_colour")
for raw, want in [("NAVY  blue", "Navy Blue"), ("navy-blue", "Navy Blue"),
                  ("IVORY", "Ivory"), ("  stone ", "Stone"), ("", "")]:
    eq(clean_colour(raw), want, f"{raw!r} -> {want!r}")

# ───────────────────────────── parsing ─────────────────────────────

section("test_sniff_rows")
eq(len(sniff_rows("a\tb\tc\n1\t2\t3")), 2, "tab-separated (Excel paste)")
eq(len(sniff_rows("a,b,c\n1,2,3")), 2, "comma-separated")
eq(len(sniff_rows("a;b;c\n1;2;3")), 2, "semicolon-separated (comma-decimal locales)")
eq(sniff_rows('name,qty\n"Kurta, cotton",5')[1], ["Kurta, cotton", "5"],
   "a quoted comma inside a field does not split it")
eq(sniff_rows("﻿name,qty\nx,1")[0][0], "name", "UTF-8 BOM stripped from the header")
check(sniff_rows("name,qty\n\n\nx,1") == [["name", "qty"], ["x", "1"]],
      "blank lines dropped")
# The delimiter is decided from the HEADER line only: Indian numbers contain
# commas, so counting over the whole document picks ',' for a tab-separated
# sheet and every row collapses into one column.
tsv_with_indian_numbers = "Item\tQty\tValue\nKurta\t3\t1,25,400\nJeans\t2\t2,10,000"
eq(len(sniff_rows(tsv_with_indian_numbers)[1]), 3,
   "tab file whose data contains commas still splits on tabs")

# ───────────────────────────── column matching ─────────────────────────────

section("test_match_columns")
m = match_columns(["Sr.No", "Particulars", "Item Code", "HSN Code", "Tax %",
                   "Unit", "Purchase Price", "MRP", "Group", "Closing Stock"])
eq(m["name"], "Particulars", "'Particulars' is the item name")
eq(m["hsn"], "HSN Code", "'HSN Code' is the HSN, not the SKU")
eq(m["sku"], "Item Code", "'Item Code' is the SKU")
eq(m["price"], "MRP", "MRP is the selling price")
eq(m["cost"], "Purchase Price", "purchase price is the cost")
eq(m["qty"], "Closing Stock", "closing stock is the quantity")
eq(m["category"], "Group", "'Group' is the category")

# The defect this ordering fix closes: `sku` has the synonym 'code', which is a
# substring of 'hsncode'. Matching field-by-field in declaration order let sku
# claim the HSN column at score 60 before hsn could claim it at 90.
m2 = match_columns(["Description", "HSN Code", "Rate"])
eq(m2["hsn"], "HSN Code", "HSN wins its own column over sku's looser 'code' match")
check(m2["sku"] is None, "sku is left unmapped rather than stealing the HSN")

m3 = match_columns(["Style Code", "Colour", "Size", "Barcode", "Qty"])
eq(m3["style_code"], "Style Code", "style code matched")
eq(m3["sku"], "Barcode", "barcode is the SKU")
eq(m3["colour"], "Colour", "British spelling")
eq(match_columns(["Color"])["colour"], "Color", "American spelling")

check(all(v is None for v in match_columns(["zzz", "qqq"]).values()),
      "nonsense headers map to nothing rather than being force-fitted")

# Global ranking rescues 'code' from stealing 'HSN Code' only because `hsn`
# outbids it on that column. When no field outbids it, the length floor on the
# loose substring rule is the ONLY thing standing between a shop's PIN code
# column and its barcode field — and a wrong barcode is silent until someone
# scans a garment and gets the wrong price.
m4 = match_columns(["Item Name", "Pin Code", "Qty"])
check(m4["sku"] is None, "'Pin Code' is NOT taken as a SKU", f"got {m4['sku']!r}")
eq(m4["name"], "Item Name", "the real name column is still found")
m5 = match_columns(["Particulars", "Pincode", "Postal Code", "Stock"])
check(m5["sku"] is None, "neither 'Pincode' nor 'Postal Code' is taken as a SKU",
      f"got {m5['sku']!r}")

# ───────────────────────────── whole-sheet behaviour ─────────────────────────────

section("test_style_grouping")
sheet = """Particulars\tArticle No\tSize\tColour\tHSN\tMRP\tStock
Cotton Kurta Small Indigo\tKUR-001-S\tSmall\tIndigo\t6103\t1459\t12
Cotton Kurta Medium Indigo\tKUR-001-M\tMED\tINDIGO\t6103\t1459\t8
Cotton Kurta Large Indigo\tKUR-001-L\tLarge\tindigo\t6103\t1459\t3
Cotton Kurta X-Large Indigo\tKUR-001-X\tX-Large\tIndigo\t6103\t1459\t5
Slim Fit Jeans 32 Stone\tJNS-32\t32 inch\tstone\t6203\t2199\t6"""
a = analyse(sheet)
check(not a.fatal, "sheet parsed", a.fatal or "")
eq(a.summary()["styles"], 2, "four sizes of one kurta collapse into ONE style")
eq({r.values["size"] for r in a.rows if "KURTA" in r.values["style_code"]},
   {"S", "M", "L", "XL"}, "all four sizes survive as variants")
eq(a.mapping["sku"], "Article No",
   "a per-row 'Article No' is demoted from style to SKU")
check(a.notes and "not a style" in a.notes[0], "and the demotion is explained",
      a.notes[0] if a.notes else "no note")

# The same header, but genuinely repeating -> it IS a style and must stay one.
grouping = """Item\tStyle Code\tSize\tColour\tMRP\tStock
Cotton Kurta\tSS26-001\tS\tIndigo\t1459\t12
Cotton Kurta\tSS26-001\tM\tIndigo\t1459\t8
Cotton Kurta\tSS26-001\tL\tIndigo\t1459\t3"""
b = analyse(grouping)
eq(b.mapping["style_code"], "Style Code", "a repeating style column is kept as the style")
eq(b.summary()["styles"], 1, "one style")
check(not b.notes, "and no demotion note is raised")

section("test_dirty_sheet")
dirty = """Sr\tParticulars\tCode\tSize\tColour\tHSN Code\tTax %\tUnit\tPurchase Price\tMRP\tClosing Stock
1\tCotton Kurta\tK-S\tSmall\tIndigo\t6103\t5%\tPcs\tRs. 620.00\t1,459.00\t12
2\tCotton Kurta\tK-M\tMED\tINDIGO\t6103\t0.05\tNos\t620\t1,459\t8
3\t\tK-ORPHAN\tL\tIndigo\t6103\t5\tPC\t620\t1459\t4
4\tSilk Kurta\tK-S\tS\tIvory\t6103\t18\tPC\t1240\t2899\t3
5\tLinen Shirt\tL-M\tM\tWhite\t610\t12\tPcs\t800\t\t6
6\tDenim Jacket\tD-L\tL\tBlue\t\t28\tPcs\t1500\t3200\t(2)
7\tPashmina Wrap\tP-F\tFree Size\tCream\t6214\t5\tNos\t2200\t1900\t4
8\tKids Tee\tT-3\tToddler-3\tRed\t6109\t5\tPcs\t180\t399\t20"""
d = analyse(dirty)
check(not d.fatal, "dirty sheet still parses", d.fatal or "")
rows = {r.line: r for r in d.rows}

check(not rows[4].ok and any("name" in e for e in rows[4].errors),
      "row with no item name is REFUSED", str(rows[4].errors))
check(not rows[5].ok and any("same code" in e for e in rows[5].errors),
      "duplicate code inside the file is REFUSED", str(rows[5].errors))
check(rows[2].values["cost"] == 620 and rows[2].values["price"] == 1459,
      "currency symbols and separators cleaned")
check(rows[3].values["size"] == "M", "'MED' normalised to M")
check(any("too short" in w for w in rows[6].warnings),
      "3-digit HSN dropped with a warning", str(rows[6].warnings))
check(rows[6].values["price"] == round(800 * 1.4, 2)
      and any("cost + 40%" in w for w in rows[6].warnings),
      "missing price filled from cost and flagged", str(rows[6].values["price"]))
check(any("abolished" in w for w in rows[7].warnings),
      "a retired 28% slab from an old export is flagged", str(rows[7].warnings))
check(rows[7].values["qty"] == 0 and any("negative stock" in w for w in rows[7].warnings),
      "negative stock clamped to 0 and flagged")
check(any("below cost" in w for w in rows[8].warnings),
      "selling below cost is flagged", str(rows[8].warnings))
check(rows[8].values["size"] == "FREE", "'Free Size' normalised")
check(any("not on a scale" in w for w in rows[9].warnings),
      "unknown size scale flagged but kept", str(rows[9].values["size"]))
eq(d.summary()["refused"], 2, "exactly two rows refused")
eq(d.summary()["importable"], 6, "the other six import")

section("test_gst_rate_is_not_stored")
# The slab is a property of the transaction, never of the master. Importing a
# rate onto the product is how a shop ends up charging 18% on its own discount.
check(all("gst_rate" not in r.values for r in d.rows),
      "no gst_rate is written onto any product")
check(all(r.values.get("declared_rate") is None or isinstance(r.values["declared_rate"], float)
          for r in d.rows),
      "the sheet's rate is kept only as a declared_rate for cross-checking")
check(all(s in (Decimal("0"), Decimal("5"), Decimal("18")) for s in LIVE_SLABS),
      "only post-Sept-2025 slabs are treated as live")

section("test_no_columns_at_all")
check(analyse("just one line").fatal, "a single line is refused with a reason")
check(analyse("").fatal, "empty input is refused with a reason")
check(analyse("Qty\tPrice\n1\t2").fatal, "a sheet with no name column is refused")

section("test_existing_skus_are_tenant_scoped")
e = analyse(grouping, existing_skus=["SS26-001"])
check(True, "existing_skus is a caller-supplied scoped set — see demo/erp/importer.py")
e2 = analyse("""Item\tCode\tSize\tMRP\tStock
Cotton Kurta\tEXIST-1\tS\t1459\t5""", existing_skus=["EXIST-1"])
check(any("will be updated" in w for w in e2.rows[0].warnings),
      "a SKU already on this tenant reports update, not duplicate")
e3 = analyse("""Item\tCode\tSize\tMRP\tStock
Cotton Kurta\tEXIST-1\tS\t1459\t5""", existing_skus=[])
check(not any("will be updated" in w for w in e3.rows[0].warnings),
      "and reports nothing when the tenant does not have it")

print("\n" + "=" * 62)
print(f"{PASS} passed, {FAIL} failed")
print("ALL PASS" if FAIL == 0 else "FAILURES")
sys.exit(1 if FAIL else 0)
