"""
Indian GST computation. Pure domain logic — no framework, no database.

Deliberately framework-free so the FastAPI routes AND the stdlib demo server
import the SAME rules. Tax logic duplicated across two transports is how a
demo and production quietly disagree about what a customer owes.

RATE STRUCTURE (as at August 2026)
----------------------------------
India rationalised GST on 22 September 2025: the 12% and 28% slabs were
abolished, leaving 5% and 18% (plus a 40% demerit rate that no apparel
attracts). For garments the slab is PRICE-DEPENDENT:

    taxable value per piece <= Rs 2,500  ->   5%
    taxable value per piece >  Rs 2,500  ->  18%

That threshold rose from Rs 1,000 in the same reform. It is why the GST rate
is NOT stored on the product master: one style routinely straddles the
boundary once a size premium or a discount moves the per-piece value, and two
lines of the same invoice can legitimately sit in different slabs.

AMBIGUITY, STATED
-----------------
The notification says "sale value not exceeding Rs 2,500 per piece". This
module reads that as the TAXABLE value per piece — i.e. after discount, before
tax — which is the common trade reading. A tenant whose CA insists on MRP can
override with `threshold_basis="mrp"`. Confirm with the client's CA before go-live.

PLACE OF SUPPLY
---------------
Same state as the seller  -> CGST + SGST, half the rate each
Different state           -> IGST at the full rate

Determined by state code, not by GSTIN, because unregistered customers have no
GSTIN but still have a place of supply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List, Optional, Sequence

# ── slabs ──
GST_NIL = Decimal("0")
GST_LOW = Decimal("5")
GST_STD = Decimal("18")

APPAREL_THRESHOLD = Decimal("2500")     # per piece, taxable value
APPAREL_HSN_PREFIXES = ("61", "62", "63", "64")   # garments, made-ups, footwear

TWO = Decimal("0.01")
ZERO = Decimal("0")


def q(value: Decimal | float | int | str) -> Decimal:
    """Round to paise, half-up. Money is Decimal end to end; float rounding
    errors compound into an invoice total that disagrees with the sum of its
    own lines, which is the fastest way to lose a shopkeeper's trust."""
    return Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)


def rate_for_line(
    taxable_per_piece: Decimal,
    hsn_code: Optional[str] = None,
    explicit_rate: Optional[Decimal] = None,
) -> Decimal:
    """The GST rate applying to one line, as a percentage.

    `explicit_rate` wins when supplied — non-apparel categories carry a fixed
    slab and should pass it. Apparel HSNs get the price-dependent rule.
    Anything unrecognised falls back to the 18% standard rate, which is the
    safe direction: under-charging GST is the tenant's liability, over-charging
    is a refund.
    """
    if explicit_rate is not None:
        return Decimal(str(explicit_rate))
    hsn = (hsn_code or "").strip()
    if hsn[:2] in APPAREL_HSN_PREFIXES:
        return GST_LOW if Decimal(str(taxable_per_piece)) <= APPAREL_THRESHOLD else GST_STD
    return GST_STD


@dataclass
class LineInput:
    product_id: str
    quantity: Decimal
    unit_price: Decimal                 # ex-GST
    discount_pct: Decimal = ZERO
    hsn_code: Optional[str] = None
    explicit_rate: Optional[Decimal] = None
    description: str = ""


@dataclass
class LineResult:
    product_id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_pct: Decimal
    discount_amount: Decimal
    taxable_value: Decimal
    taxable_per_piece: Decimal
    gst_rate: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    line_total: Decimal
    hsn_code: Optional[str]

    def as_dict(self) -> dict:
        return {k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in self.__dict__.items()}


@dataclass
class InvoiceTotals:
    subtotal: Decimal = ZERO            # before discount, ex-GST
    discount_total: Decimal = ZERO
    taxable_total: Decimal = ZERO
    cgst_total: Decimal = ZERO
    sgst_total: Decimal = ZERO
    igst_total: Decimal = ZERO
    tax_total: Decimal = ZERO
    round_off: Decimal = ZERO
    grand_total: Decimal = ZERO
    is_interstate: bool = False
    lines: List[LineResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {k: (float(v) if isinstance(v, Decimal) else v)
               for k, v in self.__dict__.items() if k != "lines"}
        out["lines"] = [l.as_dict() for l in self.lines]
        out["rate_summary"] = [
            {"rate": float(r), **{k: float(v) for k, v in vals.items()}}
            for r, vals in sorted(self.rate_summary().items())
        ]
        return out

    def rate_summary(self) -> dict:
        """Tax grouped by slab — required on the printed invoice and in GSTR-1."""
        out: dict = {}
        for l in self.lines:
            b = out.setdefault(l.gst_rate, {"taxable": ZERO, "cgst": ZERO,
                                            "sgst": ZERO, "igst": ZERO})
            b["taxable"] += l.taxable_value
            b["cgst"] += l.cgst
            b["sgst"] += l.sgst
            b["igst"] += l.igst
        return out


def compute_line(line: LineInput, interstate: bool) -> LineResult:
    qty = Decimal(str(line.quantity))
    price = Decimal(str(line.unit_price))
    disc_pct = Decimal(str(line.discount_pct or 0))

    gross = q(qty * price)
    discount = q(gross * disc_pct / Decimal("100"))
    taxable = q(gross - discount)
    per_piece = q(taxable / qty) if qty else ZERO

    rate = rate_for_line(per_piece, line.hsn_code, line.explicit_rate)
    tax = q(taxable * rate / Decimal("100"))

    if interstate:
        cgst = sgst = ZERO
        igst = tax
    else:
        # Split half each, giving the remainder to SGST so cgst+sgst==tax
        # exactly. Rounding each half independently loses a paise on odd
        # amounts, and an invoice whose parts do not sum to its total is the
        # first thing an accountant notices.
        cgst = q(tax / 2)
        sgst = q(tax - cgst)
        igst = ZERO

    return LineResult(
        product_id=line.product_id, description=line.description,
        quantity=qty, unit_price=price, discount_pct=disc_pct,
        discount_amount=discount, taxable_value=taxable,
        taxable_per_piece=per_piece, gst_rate=rate,
        cgst=cgst, sgst=sgst, igst=igst,
        line_total=q(taxable + tax), hsn_code=line.hsn_code,
    )


def compute_invoice(
    lines: Sequence[LineInput],
    seller_state: Optional[str],
    buyer_state: Optional[str],
    round_to_rupee: bool = True,
) -> InvoiceTotals:
    """Compute a whole invoice. Totals are summed from rounded LINE values, so
    the printed lines always add up to the printed total."""
    interstate = bool(seller_state and buyer_state and seller_state != buyer_state)
    results = [compute_line(l, interstate) for l in lines]

    t = InvoiceTotals(is_interstate=interstate, lines=results)
    for r in results:
        t.subtotal += q(r.quantity * r.unit_price)
        t.discount_total += r.discount_amount
        t.taxable_total += r.taxable_value
        t.cgst_total += r.cgst
        t.sgst_total += r.sgst
        t.igst_total += r.igst
    t.tax_total = q(t.cgst_total + t.sgst_total + t.igst_total)
    exact = q(t.taxable_total + t.tax_total)

    if round_to_rupee:
        rounded = exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        t.round_off = q(rounded - exact)
        t.grand_total = q(rounded)
    else:
        t.round_off = ZERO
        t.grand_total = exact
    for f in ("subtotal", "discount_total", "taxable_total",
              "cgst_total", "sgst_total", "igst_total"):
        setattr(t, f, q(getattr(t, f)))
    return t


# ── amount in words: mandatory on an Indian tax invoice ──

_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def amount_in_words(amount: Decimal | float | int) -> str:
    """Indian numbering: crore / lakh / thousand / hundred."""
    amt = Decimal(str(amount)).quantize(TWO, rounding=ROUND_HALF_UP)
    rupees = int(amt)
    paise = int((amt - rupees) * 100)
    if rupees == 0 and paise == 0:
        return "Zero Rupees Only"

    parts: List[str] = []
    for divisor, label in ((10_000_000, "Crore"), (100_000, "Lakh"),
                           (1_000, "Thousand"), (100, "Hundred")):
        if rupees >= divisor:
            parts.append(f"{_below_hundred(rupees // divisor)} {label}")
            rupees %= divisor
    if rupees:
        parts.append(("and " if parts else "") + _below_hundred(rupees))

    words = " ".join(parts).strip() + " Rupees"
    if paise:
        words += f" and {_below_hundred(paise)} Paise"
    return words + " Only"
