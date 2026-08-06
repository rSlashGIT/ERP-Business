"""Reading a shop's existing stock list.

This is the module the whole sale depends on. A shopkeeper with fifteen years
inside an old system does not care how correct our GST is if their data cannot
come with them. So the import path gets treated as a product surface, not a
utility: it guesses the columns, cleans the values, and shows every problem
row-by-row BEFORE anything is written.

Framework-free on purpose, exactly like `gst.py` — imported by both the demo
server and the FastAPI routes, because an importer that behaves differently in
the demo than in production is a demo that lies.

WHAT REAL EXPORTS LOOK LIKE
---------------------------
Nothing arrives clean. Observed in the wild, all handled here:

    Rs. 1,180.00 / ₹1,180 / 1,180.00 / (450)   currency, separators, negatives
    18% / 18 / 0.18                            three ways of writing a rate
    Nos / PC / Pcs / Piece / Coil              a dozen spellings of "each"
    Medium / MED / M / m                       size written every possible way
    Navy Blue / NAVY / navy-blue               colour ditto
    blank names, duplicate SKUs, short HSN     rows that must be refused

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not import a GST rate onto the product master. In this ERP the slab is
a property of the TRANSACTION — a garment at Rs 2,800 is 18%, and the same
garment discounted to Rs 2,240 is 5%. Storing a rate on the master is how you
end up charging the wrong tax on your own promotions. We import the HSN, warn
when it is missing, and let `gst.py` derive the rate per line at billing time.
A rate column in the sheet is used only to sanity-check the HSN.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ─────────────────────────── column matching ───────────────────────────


def norm(s: Any) -> str:
    """Squash a header to comparable form: 'Item Code' -> 'itemcode'."""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool = False
    synonyms: Tuple[str, ...] = ()


# Ordered: earlier fields claim a column first, so 'Item Name' cannot be stolen
# by the looser 'name' match on a 'Style Name' column further down.
FIELDS: Tuple[Field, ...] = (
    Field("style_code", "Style code", False,
          ("style", "stylecode", "styleno", "design", "designno", "designcode",
           "articleno", "article", "art", "catalogno")),
    Field("name", "Item name", True,
          ("item", "itemname", "product", "productname", "description", "particulars",
           "goods", "material", "stockitem", "nameofitem", "garment", "stylename")),
    Field("sku", "SKU / barcode", False,
          ("sku", "code", "itemcode", "partno", "partnumber", "alias", "productcode",
           "barcode", "ean", "upc", "scancode")),
    Field("size", "Size", False,
          ("size", "sizes", "sz", "waist", "chest", "fit")),
    Field("colour", "Colour", False,
          ("colour", "color", "shade", "colourname", "colorname")),
    Field("brand", "Brand", False, ("brand", "make", "label", "company")),
    Field("category", "Category", False,
          ("category", "group", "stockgroup", "type", "segment", "department", "dept")),
    Field("hsn", "HSN", False, ("hsn", "hsncode", "sac", "hsnsac", "hsnsaccode")),
    Field("gst_rate", "GST rate % (checked, not stored)", False,
          ("gst", "gstrate", "tax", "taxrate", "rateoftax", "taxpercent", "gstpercentage")),
    Field("uom", "Unit", False, ("unit", "uom", "units", "measure", "uqc", "packing")),
    Field("cost", "Purchase rate", False,
          ("purchase", "purchaserate", "cost", "costprice", "buyrate", "buying",
           "landedcost", "purchaseprice", "wsp", "pp")),
    Field("price", "Selling rate", False,
          ("mrp", "sale", "salerate", "sellingprice", "rate", "price", "sellingrate",
           "salesprice", "retail", "sp")),
    Field("reorder", "Reorder level", False,
          ("reorder", "reorderlevel", "minstock", "minimum", "minqty", "safetystock")),
    Field("qty", "Stock on hand", False,
          ("stock", "qty", "quantity", "openingstock", "closingstock", "balance",
           "instock", "openingqty", "stockinhand", "onhand", "pieces", "pcs")),
)

REQUIRED = tuple(f.key for f in FIELDS if f.required)


def score_match(field_: Field, header: str) -> int:
    """Confidence that `header` feeds `field_`, highest first:

        100  the header IS the field name or label
         90  the header is a known synonym
         60  a synonym is a substring either way ('itemdescription' ~ 'description')
         40  the field key appears inside the header

    Below 40 we return 0 rather than guess. A wrong mapping that looks
    confident is worse than an unmapped column the user can see and fix.
    """
    n = norm(header)
    if not n:
        return 0
    if n in (norm(field_.key), norm(field_.label)):
        return 100
    if n in field_.synonyms:
        return 90
    # 'code' would otherwise match hsncode, barcode, pincode and stylecode
    # alike, so a loose substring hit needs a longer synonym to count.
    if any(n in s or s in n for s in field_.synonyms if len(s) >= 5 and len(n) >= 5):
        return 60
    if norm(field_.key) and norm(field_.key) in n:
        return 40
    return 0


def match_columns(headers: Sequence[str]) -> Dict[str, Optional[str]]:
    """Assign columns to fields by GLOBAL best score, not field declaration order.

    Doing it in field order let an early field claim a column an later field
    matched better: `sku` (synonym 'code', score 60 on 'HSN Code') ran before
    `hsn` (synonym 'hsncode', score 90 on the same column) and stole it, so
    every row imported its HSN as the barcode and then reported "no HSN".
    Ranking every (field, column) pair together and assigning the strongest
    first fixes that for any pair of fields, not just this one.
    """
    pairs = sorted(
        ((score_match(f, h), f.key, h) for f in FIELDS for h in headers),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    mapping: Dict[str, Optional[str]] = {f.key: None for f in FIELDS}
    used: set = set()
    for score, key, header in pairs:
        if score < 40 or mapping[key] is not None or header in used:
            continue
        mapping[key] = header
        used.add(header)
    return mapping


# ─────────────────────────── value cleaning ───────────────────────────

_NUM_JUNK = re.compile(r"[₹$,\s%]|rs\.?|inr", re.I)
_PARENS = re.compile(r"^\(([\d.]+)\)$")


def clean_number(v: Any) -> Decimal:
    """'Rs. 1,180.00' -> 1180, '(450)' -> -450, '' -> 0, 'N/A' -> 0.

    Accounting parentheses mean negative. Anything unparseable becomes 0 and
    the caller decides whether that is a warning; raising here would abort a
    2,000-row import over one typo.
    """
    if v is None:
        return Decimal("0")
    s = str(v).strip()
    if not s:
        return Decimal("0")
    m = _PARENS.match(s)
    if m:
        s = "-" + m.group(1)
    s = _NUM_JUNK.sub("", s)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal("0")


# Post-22-Sept-2025 slabs. 12% and 28% were abolished; a sheet exported before
# that date still contains them, so they map to the nearest surviving slab and
# the row is flagged rather than silently changed.
LIVE_SLABS = (Decimal("0"), Decimal("5"), Decimal("18"))
RETIRED_SLABS = {Decimal("12"): Decimal("5"), Decimal("28"): Decimal("18")}


def clean_rate(v: Any) -> Optional[Decimal]:
    """'18%' / '18' / '0.18' all mean eighteen percent. None when absent."""
    if v is None or str(v).strip() == "":
        return None
    n = clean_number(v)
    if n <= 0:
        return None
    if n <= 1:                      # 0.18 style
        n = n * 100
    return n


UOM_CANON = {
    "NOS": "PCS", "NO": "PCS", "PC": "PCS", "PCS": "PCS", "PIECE": "PCS",
    "PIECES": "PCS", "UNIT": "PCS", "UNITS": "PCS", "EACH": "PCS", "EA": "PCS",
    "COIL": "PCS", "NUMBER": "PCS",
    "KGS": "KG", "KG": "KG", "KILO": "KG", "KILOGRAM": "KG",
    "LITRE": "LTR", "LITER": "LTR", "LTRS": "LTR", "LTR": "LTR", "L": "LTR",
    "MTRS": "MTR", "METER": "MTR", "METRE": "MTR", "MTR": "MTR", "M": "MTR",
    "BAGS": "BAG", "BAG": "BAG", "BOXES": "BOX", "BOX": "BOX",
    "SETS": "SET", "SET": "SET", "PAIR": "PAIR", "PRS": "PAIR", "PAIRS": "PAIR",
    "DOZ": "DOZ", "DOZEN": "DOZ", "CTN": "CTN", "CARTON": "CTN",
}


def clean_uom(v: Any) -> str:
    key = re.sub(r"[^A-Z]", "", str(v or "").upper())
    return UOM_CANON.get(key, key[:5] or "PCS")


def clean_hsn(v: Any) -> Tuple[str, Optional[str]]:
    """Digits only. Returns (hsn, warning). Valid HSN is 4, 6 or 8 digits."""
    digits = re.sub(r"\D", "", str(v or ""))
    if not digits:
        return "", "no HSN — needed on a GST invoice, and it sets the tax slab"
    if len(digits) < 4:
        return "", f"HSN '{digits}' is too short to be valid, dropped"
    if len(digits) > 8:
        return digits[:8], f"HSN truncated to 8 digits ({digits} -> {digits[:8]})"
    return digits, None


# ─────────────────────────── apparel specifics ───────────────────────────

SIZE_WORDS = {
    "XXSMALL": "XXS", "EXTRAEXTRASMALL": "XXS",
    "XSMALL": "XS", "EXTRASMALL": "XS",
    "SMALL": "S", "SM": "S", "S": "S",
    "MEDIUM": "M", "MED": "M", "MD": "M", "M": "M",
    "LARGE": "L", "LG": "L", "LRG": "L", "L": "L",
    "XLARGE": "XL", "EXTRALARGE": "XL", "XL": "XL",
    "XXLARGE": "XXL", "XXL": "XXL", "2XL": "XXL",
    "XXXL": "3XL", "3XL": "3XL", "XXXLARGE": "3XL",
    "4XL": "4XL", "5XL": "5XL",
    "FREE": "FREE", "FREESIZE": "FREE", "ONESIZE": "FREE", "OS": "FREE",
    "STANDARD": "FREE", "ALL": "FREE",
}


def clean_size(v: Any) -> Tuple[str, Optional[str]]:
    """'Medium' / 'MED' / 'm' -> 'M'.  '40 inch' / '40"' -> '40'.

    Returns (label, warning). A size we cannot place on a known scale is kept
    verbatim rather than dropped — it still sorts last via size_seq — but the
    row is flagged, because an unrecognised scale usually means the column was
    mapped to the wrong thing.
    """
    raw = str(v or "").strip()
    if not raw:
        return "", None
    key = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if key in SIZE_WORDS:
        return SIZE_WORDS[key], None
    digits = re.sub(r"\D", "", key)
    if digits and digits == key.rstrip("INCH").rstrip('"'):
        return digits, None
    if digits and not re.sub(r"[0-9]", "", key).strip("INCHCM\""):
        return digits, None
    return raw[:20], f"size '{raw}' is not on a scale we recognise — it will sort last"


def clean_colour(v: Any) -> str:
    """Title-case and collapse whitespace: 'NAVY  blue' -> 'Navy Blue'."""
    s = re.sub(r"[\s_\-]+", " ", str(v or "").strip())
    return " ".join(w.capitalize() for w in s.split())[:40]


#: Three-letter colour abbreviations that turn up inside apparel SKUs.
COLOUR_ABBR = {
    "BLK": "Black", "BLU": "Blue", "WHT": "White", "WHI": "White", "RED": "Red",
    "GRN": "Green", "GRY": "Grey", "GRE": "Grey", "NVY": "Navy", "NAV": "Navy",
    "BRN": "Brown", "BEI": "Beige", "CRM": "Cream", "IVY": "Ivory", "MAR": "Maroon",
    "PNK": "Pink", "PUR": "Purple", "YEL": "Yellow", "ORG": "Orange", "OLV": "Olive",
    "TAN": "Tan", "GLD": "Gold", "SLV": "Silver", "TEA": "Teal", "MUS": "Mustard",
    "IND": "Indigo", "STN": "Stone", "SGE": "Sage", "PCH": "Peach", "MNT": "Mint",
}


def split_sku(sku: str) -> Tuple[str, str, str]:
    """'SHIRT-M-BLU' -> ('SHIRT', 'M', 'Blue'). Returns ('', '', '') if unsure.

    Apparel codes overwhelmingly encode style, size and colour in one string,
    and plenty of shops keep no separate size column at all — the code IS the
    variant. Without this, a real export imports as N one-variant styles and
    the size grid, which is the screen the demo rests on, is empty.

    Deliberately conservative: a segment only counts as a size if it is a known
    size token or a plain waist number, and only counts as a colour if it is a
    known abbreviation or a real colour word. Guessing wrong here silently
    mangles a shop's catalogue, so ambiguity returns nothing and the caller
    falls back to treating the whole code as opaque.
    """
    parts = [p for p in re.split(r"[-_/ ]+", str(sku or "").strip().upper()) if p]
    if len(parts) < 2:
        return "", "", ""

    size = colour = ""
    size_at = colour_at = -1
    for i, p in enumerate(parts[1:], start=1):     # never the first segment
        if not size and (p in SIZE_WORDS or (p.isdigit() and 20 <= int(p) <= 60)):
            size, size_at = (SIZE_WORDS.get(p, p), i)
            continue
        if not colour:
            if p in COLOUR_ABBR:
                colour, colour_at = COLOUR_ABBR[p], i
            elif len(p) > 3 and p.isalpha() and clean_colour(p) in set(COLOUR_ABBR.values()):
                colour, colour_at = clean_colour(p), i
    if not size and not colour:
        return "", "", ""
    style = "-".join(parts[:min(x for x in (size_at, colour_at) if x >= 0)])
    return style or parts[0], size, colour


# ─────────────────────────── parsing a sheet ───────────────────────────


def sniff_rows(text: str) -> List[List[str]]:
    """Parse pasted text or a CSV/TSV file into rows.

    Excel copy-paste is tab-separated; a saved file is usually comma-separated;
    some Indian exports use semicolons because the locale uses commas inside
    numbers. Pick whichever the HEADER line has most of — counting over the
    whole document gets fooled by '1,25,400' in the data.
    """
    text = text.lstrip("﻿").strip()
    if not text:
        return []
    header = text.splitlines()[0]
    delim = max(("\t", ",", ";", "|"), key=lambda d: header.count(d))
    if header.count(delim) == 0:
        delim = ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)
            if any(str(c).strip() for c in r)]
    return [[str(c).strip() for c in r] for r in rows]


# ─────────────────────────── validation ───────────────────────────


@dataclass
class Row:
    line: int                                   # 1-based line in the user's file
    values: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)     # row is refused
    warnings: List[str] = field(default_factory=list)   # row is imported, flagged

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"line": self.line, "ok": self.ok, "errors": self.errors,
                "warnings": self.warnings, **self.values}


@dataclass
class Analysis:
    headers: List[str] = field(default_factory=list)
    mapping: Dict[str, Optional[str]] = field(default_factory=dict)
    rows: List[Row] = field(default_factory=list)
    fatal: Optional[str] = None
    notes: List[str] = field(default_factory=list)   # decisions the user should see

    @property
    def importable(self) -> List[Row]:
        return [r for r in self.rows if r.ok]

    def summary(self) -> dict:
        styles = {r.values["style_code"] for r in self.importable}
        return {
            "total": len(self.rows),
            "importable": len(self.importable),
            "refused": len(self.rows) - len(self.importable),
            "flagged": sum(1 for r in self.importable if r.warnings),
            "styles": len(styles),
            "units": float(sum(Decimal(str(r.values["qty"])) for r in self.importable)),
            "stock_value": float(sum(Decimal(str(r.values["qty"])) * Decimal(str(r.values["cost"]))
                                     for r in self.importable)),
        }

    def as_dict(self) -> dict:
        return {"headers": self.headers, "mapping": self.mapping, "fatal": self.fatal,
                "notes": self.notes,
                "fields": [{"key": f.key, "label": f.label, "required": f.required}
                           for f in FIELDS],
                "summary": None if self.fatal else self.summary(),
                "rows": [r.as_dict() for r in self.rows]}


def _slug(s: str, n: int = 24) -> str:
    out = re.sub(r"[^A-Z0-9]+", "-", str(s).upper()).strip("-")
    return out[:n] or "ITEM"


def _style_column_groups(values: Sequence[str]) -> bool:
    """Does this column actually GROUP rows, or is it one code per row?

    'Article No' is the standard apparel header for a style, and shops also use
    it for the variant code — KUR-001-S, KUR-001-M, KUR-001-L. Mapping the
    second kind as a style gives every garment its own style and destroys the
    size grid, which is the screen the whole demo rests on. The header cannot
    tell them apart; the data can. A real style column repeats.
    """
    filled = [v for v in values if str(v).strip()]
    if len(filled) < 3:
        return True                       # too little evidence to demote
    return len(set(filled)) < len(filled) * 0.9


# Every spelling that maps to a canonical size, so the style can be stripped out
# of an item name regardless of which spelling the name happens to use.
SIZE_ALIASES: Dict[str, Tuple[str, ...]] = {}
for _spelling, _canon in SIZE_WORDS.items():
    SIZE_ALIASES.setdefault(_canon, ())
    SIZE_ALIASES[_canon] += (_spelling,)


def _derive_style(name: str, raw_size: str, size: str, colour: str) -> str:
    """'Cotton Kurta Small Indigo' + (Small, S, Indigo) -> 'Cotton Kurta'.

    Strip EVERY known spelling of the size, not just the one in the size cell.
    A sheet routinely says `MED` in the size column while the item name spells
    it `Medium`; stripping only `MED` and `M` left 'Cotton Kurta Medium', which
    became a separate style from 'Cotton Kurta' — one style per size, and the
    size grid the demo rests on collapses to a list of singletons.
    """
    out = name
    tokens = {colour, raw_size, size} | set(SIZE_ALIASES.get(size, ()))
    for t in sorted((str(t).strip() for t in tokens if str(t).strip()),
                    key=len, reverse=True):
        out = re.sub(r"[\s\-/,(]+%s\b[\s\-/,)]*" % re.escape(t), " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" -/,")


def analyse(text_or_rows, mapping: Optional[Dict[str, Optional[str]]] = None,
            *, existing_skus: Iterable[str] = ()) -> Analysis:
    """Read, map, clean and validate — WITHOUT writing anything.

    `existing_skus` are the SKUs already on this tenant, so a re-import reports
    "will be updated" instead of "duplicate". Pass a tenant-scoped set; passing
    every tenant's SKUs would leak one shop's catalogue into another's preview.
    """
    rows = sniff_rows(text_or_rows) if isinstance(text_or_rows, str) else \
        [[str(c).strip() for c in r] for r in text_or_rows]

    a = Analysis()
    if len(rows) < 2:
        a.fatal = "Need a header row and at least one row of stock."
        return a

    a.headers = rows[0]
    a.mapping = mapping if mapping is not None else match_columns(a.headers)
    missing = [f.label for f in FIELDS if f.required and not a.mapping.get(f.key)]
    if missing:
        a.fatal = f"Could not find a column for: {', '.join(missing)}. Match it by hand."
        return a

    idx = {h: i for i, h in enumerate(a.headers)}

    def cell(raw: List[str], key: str) -> str:
        col = a.mapping.get(key)
        if col is None or col not in idx:
            return ""
        i = idx[col]
        return raw[i] if i < len(raw) else ""

    # A style column that never repeats is a variant code wearing a style's
    # header. Demote it to SKU when SKU is free, and fall back to deriving the
    # style from the item name.
    if a.mapping.get("style_code"):
        col = a.mapping["style_code"]
        i = idx.get(col)
        vals = [r[i] if i is not None and i < len(r) else "" for r in rows[1:]]
        if not _style_column_groups(vals):
            a.mapping["style_code"] = None
            if not a.mapping.get("sku"):
                a.mapping["sku"] = col
                a.notes.append(
                    f"'{col}' has a different value on every row, so it is a "
                    f"per-item code, not a style — used as the SKU. Styles are "
                    f"grouped from the item name.")
            else:
                a.notes.append(
                    f"'{col}' has a different value on every row, so it cannot be "
                    f"a style — styles are grouped from the item name instead.")

    seen_sku: Dict[str, int] = {}
    seen_variant: Dict[Tuple[str, str, str], int] = {}
    existing = {str(s).upper() for s in existing_skus}

    for n, raw in enumerate(rows[1:], start=2):
        r = Row(line=n)
        v = r.values

        v["name"] = cell(raw, "name").strip()
        if not v["name"]:
            r.errors.append("no item name")

        raw_size = cell(raw, "size").strip()
        size, w = clean_size(raw_size)
        v["size"] = size
        if w:
            r.warnings.append(w)
        v["colour"] = clean_colour(cell(raw, "colour"))

        # No size column? The code very often carries it: SHIRT-M-BLU.
        sku_style = ""
        if not size or not v["colour"]:
            s_style, s_size, s_colour = split_sku(cell(raw, "sku"))
            if s_size and not size:
                size = v["size"] = s_size
                raw_size = s_size
                sku_style = s_style
            if s_colour and not v["colour"]:
                v["colour"] = s_colour
                sku_style = sku_style or s_style

        # A style groups variants. Without a style column, the item name minus
        # its size and colour IS the style — 'Cotton Kurta / M / Red' and
        # '.../ L / Red' must land under one style or the size grid is
        # meaningless and every garment looks like a separate product.
        base = _derive_style(v["name"], raw_size, size, v["colour"]) or v["name"]
        # A style parsed out of the code beats one guessed from the name: the
        # code is what the shop actually keys on.
        style_code = cell(raw, "style_code").strip() or sku_style or _slug(base)
        v["style_code"] = style_code
        v["style_name"] = base

        v["brand"] = cell(raw, "brand").strip()[:40]
        v["category"] = cell(raw, "category").strip()[:40]

        hsn, w = clean_hsn(cell(raw, "hsn"))
        v["hsn"] = hsn
        if w:
            r.warnings.append(w)

        # The rate is NOT stored — it is only used to catch a mismatched HSN.
        rate = clean_rate(cell(raw, "gst_rate"))
        v["declared_rate"] = float(rate) if rate is not None else None
        if rate is not None:
            if rate in RETIRED_SLABS:
                r.warnings.append(
                    f"sheet says {rate:g}% — that slab was abolished in Sept 2025; "
                    f"tax will be worked out per bill from the price")
            elif rate not in LIVE_SLABS:
                r.warnings.append(f"{rate:g}% is not a current GST slab — ignored")

        v["uom"] = clean_uom(cell(raw, "uom"))
        v["cost"] = float(clean_number(cell(raw, "cost")))
        v["price"] = float(clean_number(cell(raw, "price")))
        if v["price"] < 0 or v["cost"] < 0:
            r.errors.append("negative price")
        if not v["price"] and v["cost"]:
            v["price"] = round(v["cost"] * 1.4, 2)
            r.warnings.append("no selling price — set at cost + 40%, correct it before billing")
        if v["price"] and v["cost"] and v["price"] < v["cost"]:
            r.warnings.append("selling price is below cost")
        if not v["price"]:
            r.warnings.append("no price — this cannot be billed until you set one")

        qty = clean_number(cell(raw, "qty"))
        if qty < 0:
            r.warnings.append(f"negative stock ({qty:g}) treated as 0")
            qty = Decimal("0")
        v["qty"] = float(qty)
        v["reorder"] = float(clean_number(cell(raw, "reorder")))

        sku = cell(raw, "sku").strip().upper()
        if not sku:
            sku = "-".join(p for p in (_slug(v["style_code"], 16),
                                       _slug(size, 6), _slug(v["colour"], 6)) if p)
            r.warnings.append(f"no code in the sheet — generated {sku}")
        v["sku"] = sku[:48]

        if v["sku"] in seen_sku:
            r.errors.append(f"same code as line {seen_sku[v['sku']]} in this file")
        else:
            seen_sku[v["sku"]] = n
        if v["sku"] in existing:
            r.warnings.append("already in the system — will be updated, not duplicated")

        vk = (v["style_code"], size, v["colour"])
        if vk in seen_variant:
            r.errors.append(
                f"same style/size/colour as line {seen_variant[vk]} — one row per variant")
        else:
            seen_variant[vk] = n

        a.rows.append(r)

    return a
