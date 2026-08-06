"""Printable garment tags with real EAN-13 barcodes.

    python3 -c "from demo.erp.labels import ean13; print(ean13('890123456789'))"

WHY THIS IS HAND-ROLLED
-----------------------
`pip install` is blocked in this sandbox, so there is no reportlab, no
python-barcode, no PIL. That turns out to be fine: EAN-13 is a fully specified
symbology that fits in a page of code, and an SVG barcode is sharper on a laser
printer than a rasterised one anyway. Output is plain HTML + CSS with `@page`
rules, so the shop prints it from the browser they already have.

THE PART THAT MATTERS: THE CHECK DIGIT
--------------------------------------
A barcode with a wrong check digit is not a barcode. The scanner at the till
simply refuses it, and the shopkeeper discovers this after printing four
hundred tags. The seed data here contains exactly that hazard — one deliberate
real barcode plus a pile of `890` + random digits, most of which fail the
checksum.

So `ean13()` never prints what it was given without checking. A 12-digit code
gets its check digit computed; a 13-digit code is validated and CORRECTED if
wrong, and the correction is reported so the label sheet can say which tags had
to be fixed. Silently printing an unscannable tag would be the worst outcome
of the three.
"""
from __future__ import annotations

import html
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────── EAN-13 symbology ───────────────────────────

# Each digit has three encodings. Which one is used in the left-hand group is
# what carries the FIRST digit — it is never drawn as bars itself.
_L = ("0001101", "0011001", "0010011", "0111101", "0100011",
      "0110001", "0101111", "0111011", "0110111", "0001011")
_G = ("0100111", "0110011", "0011011", "0100001", "0011101",
      "0111001", "0000101", "0010001", "0001001", "0010111")
_R = ("1110010", "1100110", "1101100", "1000010", "1011100",
      "1001110", "1010000", "1000100", "1001000", "1110100")

#: Which of L/G each of the six left-hand digits uses, keyed by the first digit.
_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
           "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")

_GUARD_SIDE = "101"
_GUARD_MIDDLE = "01010"


def check_digit(first12: str) -> str:
    """The 13th digit. Weights alternate 1,3 from the left."""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def ean13(code: Optional[str]) -> Tuple[Optional[str], str]:
    """Return (valid 13-digit code, note).

    note is "" when the input was already a good EAN-13, otherwise it explains
    what had to change — so the caller can show the shopkeeper which tags were
    not printable as stored.
    """
    digits = re.sub(r"\D", "", str(code or ""))
    if not digits:
        return None, "no barcode on this item"
    if len(digits) == 12:
        return digits + check_digit(digits), "check digit added"
    if len(digits) == 13:
        want = check_digit(digits[:12])
        if digits[12] == want:
            return digits, ""
        return digits[:12] + want, f"check digit was {digits[12]}, corrected to {want}"
    return None, f"{len(digits)} digits — an EAN-13 needs 12 or 13"


def _modules(code13: str) -> str:
    """The full bar pattern as a string of 0/1, 95 modules wide."""
    parity = _PARITY[int(code13[0])]
    left = "".join((_L if parity[i] == "L" else _G)[int(d)]
                   for i, d in enumerate(code13[1:7]))
    right = "".join(_R[int(d)] for d in code13[7:13])
    return _GUARD_SIDE + left + _GUARD_MIDDLE + right + _GUARD_SIDE


def barcode_svg(code13: str, width: float = 46.0, height: float = 15.0) -> str:
    """An EAN-13 as inline SVG, sized in millimetres.

    Guard bars are drawn taller than data bars, which is not decoration — the
    extended guards are what let a scanner find the symbol's edges.
    """
    mods = _modules(code13)
    n = len(mods)                                   # always 95
    unit = width / n
    guard_positions = set(range(0, 3)) | set(range(45, 50)) | set(range(92, 95))
    bar_h = height - 3.2                            # room for the digits
    bars = []
    for i, m in enumerate(mods):
        if m != "1":
            continue
        h = height - 1.0 if i in guard_positions else bar_h
        bars.append(
            f'<rect x="{i * unit:.4f}" y="0" width="{unit:.4f}" height="{h:.3f}"/>')
    fs = max(2.2, height * 0.19)
    # The human-readable line: first digit outside the symbol, then two groups.
    txt = (f'<text x="{-unit * 1.5:.3f}" y="{height:.2f}" font-size="{fs:.2f}"'
           f' text-anchor="middle">{code13[0]}</text>'
           f'<text x="{unit * 25:.3f}" y="{height:.2f}" font-size="{fs:.2f}"'
           f' text-anchor="middle">{code13[1:7]}</text>'
           f'<text x="{unit * 70:.3f}" y="{height:.2f}" font-size="{fs:.2f}"'
           f' text-anchor="middle">{code13[7:]}</text>')
    return (f'<svg class="bc" viewBox="{-unit * 4:.3f} 0 {width + unit * 6:.3f} {height}"'
            f' width="{width + 3:.1f}mm" height="{height:.1f}mm"'
            f' xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges"'
            f' role="img" aria-label="barcode {code13}">'
            f'<g fill="#000">{"".join(bars)}{txt}</g></svg>')


# ─────────────────────────── the label sheet ───────────────────────────

#: Avery-style A4 grids. Millimetres, because that is how label stock is sold.
SHEETS = {
    "l7159": {"label": "Avery L7159 · 24 per sheet (63.5 x 33.9 mm)",
              "cols": 3, "rows": 8, "w": 63.5, "h": 33.9,
              "top": 13.0, "left": 7.75, "gap_x": 2.5, "gap_y": 0.0},
    "l7160": {"label": "Avery L7160 · 21 per sheet (63.5 x 38.1 mm)",
              "cols": 3, "rows": 7, "w": 63.5, "h": 38.1,
              "top": 15.1, "left": 7.75, "gap_x": 2.5, "gap_y": 0.0},
    "l7651": {"label": "Avery L7651 · 65 per sheet (38.1 x 21.2 mm)",
              "cols": 5, "rows": 13, "w": 38.1, "h": 21.2,
              "top": 10.7, "left": 4.75, "gap_x": 2.5, "gap_y": 0.0},
}
DEFAULT_SHEET = "l7159"


def _fetch(conn: sqlite3.Connection, tenant_id: str,
           product_ids: List[str], po_id: Optional[str]) -> List[Dict[str, Any]]:
    """Products to print, tenant-scoped. A PO prints one tag per piece ordered."""
    if po_id:
        rows = conn.execute(
            "SELECT p.id, p.name, p.sku, p.size, p.colour, p.barcode, p.unit_price,"
            " ps.name style_name, CAST(pl.ordered_qty AS INTEGER) qty"
            " FROM purchase_order_lines pl"
            " JOIN products p ON p.id=pl.product_id AND p.tenant_id=pl.tenant_id"
            " LEFT JOIN product_styles ps ON ps.id=p.style_id AND ps.tenant_id=p.tenant_id"
            " WHERE pl.tenant_id=? AND pl.purchase_order_id=?"
            " ORDER BY pl.line_no", (tenant_id, po_id)).fetchall()
    elif product_ids:
        marks = ",".join("?" * len(product_ids))
        rows = conn.execute(
            "SELECT p.id, p.name, p.sku, p.size, p.colour, p.barcode, p.unit_price,"
            " ps.name style_name, 1 qty"
            " FROM products p"
            " LEFT JOIN product_styles ps ON ps.id=p.style_id AND ps.tenant_id=p.tenant_id"
            f" WHERE p.tenant_id=? AND p.id IN ({marks}) AND p.is_active=1"
            " ORDER BY COALESCE(p.size_seq,99999), p.sku",
            (tenant_id, *product_ids)).fetchall()
    else:
        rows = []
    cols = ["id", "name", "sku", "size", "colour", "barcode", "unit_price",
            "style_name", "qty"]
    return [dict(zip(cols, r)) for r in rows]


def render_sheet(conn: sqlite3.Connection, tenant_id: str, *,
                 product_ids: Optional[List[str]] = None,
                 po_id: Optional[str] = None,
                 sheet: str = DEFAULT_SHEET,
                 copies: int = 1) -> str:
    """A print-ready HTML page. Returned as a string; the caller serves it."""
    spec = SHEETS.get(sheet, SHEETS[DEFAULT_SHEET])
    tenant = conn.execute("SELECT name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    shop = tenant[0] if tenant else ""
    items = _fetch(conn, tenant_id, product_ids or [], po_id)

    tags: List[Dict[str, Any]] = []
    notes: Dict[str, str] = {}
    for it in items:
        code, note = ean13(it["barcode"])
        if note:
            notes[it["sku"]] = note
        n = max(1, int(it.get("qty") or 1)) * max(1, int(copies))
        for _ in range(n):
            tags.append({**it, "code13": code})

    e = html.escape
    per_page = spec["cols"] * spec["rows"]
    pages = [tags[i:i + per_page] for i in range(0, len(tags), per_page)] or [[]]

    def cell(t: Dict[str, Any]) -> str:
        bits = " · ".join(x for x in (t.get("size"), t.get("colour")) if x)
        bc = (barcode_svg(t["code13"], width=spec["w"] * 0.72, height=spec["h"] * 0.42)
              if t["code13"] else
              f'<div class="nobc">{e(t["sku"])}<br><small>no barcode</small></div>')
        return (f'<div class="lb">'
                f'<div class="nm">{e(t.get("style_name") or t["name"])}</div>'
                f'<div class="sz">{e(bits)}</div>'
                f'<div class="bcw">{bc}</div>'
                f'<div class="pr">&#8377;{float(t["unit_price"] or 0):,.0f}</div>'
                f'</div>')

    body = "".join(
        f'<section class="sheet">{"".join(cell(t) for t in page)}</section>'
        for page in pages)

    warn = ""
    if notes:
        warn = ('<div class="warn no-print"><b>Barcodes that were not printable as '
                'stored:</b><ul>'
                + "".join(f"<li><code>{e(k)}</code> — {e(v)}</li>"
                          for k, v in sorted(notes.items()))
                + '</ul><p>Corrected codes have been printed. A barcode with the wrong '
                  'check digit will not scan at the till, so update these on the item '
                  'master too.</p></div>')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Labels — {e(shop)}</title>
<style>
  @page {{ size: A4; margin: 0; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font: 11px/1.25 Inter,-apple-system,"Segoe UI",Roboto,sans-serif; color:#111;
          background:#f4f2ee; }}
  .bar {{ padding:14px 18px; background:#1B1C3A; color:#fff; display:flex;
          align-items:center; gap:14px; }}
  .bar b {{ font-size:15px }} .bar span {{ opacity:.75; font-size:12px }}
  .bar button {{ margin-left:auto; padding:8px 16px; border:0; border-radius:8px;
                 background:#fff; color:#1B1C3A; font-weight:650; cursor:pointer }}
  .warn {{ margin:14px 18px; padding:12px 14px; background:#FEF2F2; color:#B91C1C;
           border:1px solid #FECACA; border-radius:9px; font-size:12px }}
  .warn ul {{ margin:6px 0 6px 18px }} .warn p {{ margin-top:6px }}
  .sheet {{ width:210mm; height:297mm; background:#fff; margin:14px auto;
            padding:{spec['top']}mm 0 0 {spec['left']}mm;
            display:grid; align-content:start;
            grid-template-columns: repeat({spec['cols']}, {spec['w']}mm);
            grid-auto-rows: {spec['h']}mm;
            column-gap:{spec['gap_x']}mm; row-gap:{spec['gap_y']}mm;
            box-shadow:0 2px 12px rgba(0,0,0,.12); }}
  .lb {{ width:{spec['w']}mm; height:{spec['h']}mm; padding:1.6mm 2mm;
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; text-align:center; overflow:hidden; }}
  .nm {{ font-weight:650; font-size:8.5px; line-height:1.15; max-height:2.4em;
         overflow:hidden }}
  .sz {{ font-size:7.5px; color:#444; margin-top:.4mm }}
  .bcw {{ margin:.6mm 0 }}
  .bc {{ display:block }}
  .pr {{ font-weight:700; font-size:11px; letter-spacing:-.2px }}
  .nobc {{ font:7px/1.3 monospace; color:#777; padding:2mm 0 }}
  @media print {{
    body {{ background:#fff }}
    .no-print {{ display:none !important }}
    .sheet {{ margin:0; box-shadow:none; page-break-after:always }}
    .sheet:last-child {{ page-break-after:auto }}
  }}
</style></head><body>
<div class="bar no-print">
  <b>{e(shop)} — {len(tags)} label{'' if len(tags) == 1 else 's'}</b>
  <span>{e(spec['label'])} · {len(pages)} page{'' if len(pages) == 1 else 's'}</span>
  <button onclick="window.print()">Print</button>
</div>
{warn}
{body}
</body></html>"""
