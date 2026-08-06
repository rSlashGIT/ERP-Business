"""GST engine tests. Pure domain — runs with no framework, no database."""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.domain.gst import (  # noqa: E402
    APPAREL_THRESHOLD, GST_LOW, GST_STD, InvoiceTotals, LineInput,
    amount_in_words, compute_invoice, compute_line, rate_for_line, q,
)

FAILURES = []
D = Decimal


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


def test_apparel_slab_turns_at_2500():
    check("garment at Rs 2,499 -> 5%", rate_for_line(D("2499"), "6205") == GST_LOW)
    check("garment exactly at Rs 2,500 -> 5% (threshold inclusive)",
          rate_for_line(D("2500"), "6205") == GST_LOW)
    check("garment at Rs 2,500.01 -> 18%", rate_for_line(D("2500.01"), "6205") == GST_STD)
    check("garment at Rs 4,000 -> 18%", rate_for_line(D("4000"), "6205") == GST_STD)
    for hsn in ("6109", "6203", "6304", "6403"):
        check(f"HSN {hsn} treated as apparel", rate_for_line(D("999"), hsn) == GST_LOW)


def test_non_apparel_defaults_to_standard():
    check("unknown HSN falls back to 18% (safe direction)",
          rate_for_line(D("100"), "8544") == GST_STD)
    check("no HSN at all falls back to 18%", rate_for_line(D("100"), None) == GST_STD)
    check("explicit rate always wins",
          rate_for_line(D("100"), "6205", explicit_rate=D("0")) == D("0"))


def test_intrastate_splits_in_half_and_reconciles():
    r = compute_line(LineInput("p", D("3"), D("500"), hsn_code="6205"), interstate=False)
    check("taxable = qty x price", r.taxable_value == D("1500.00"), str(r.taxable_value))
    check("per-piece value drives the slab", r.taxable_per_piece == D("500.00"))
    check("5% slab applied", r.gst_rate == GST_LOW)
    check("cgst + sgst == total tax exactly",
          r.cgst + r.sgst == q(r.taxable_value * r.gst_rate / 100),
          f"{r.cgst}+{r.sgst}")
    check("no igst on an intra-state line", r.igst == 0)
    check("line total = taxable + tax", r.line_total == r.taxable_value + r.cgst + r.sgst)


def test_odd_amount_halves_without_losing_a_paise():
    """cgst = sgst = tax/2 loses a paise on odd tax; the remainder goes to sgst."""
    r = compute_line(LineInput("p", D("1"), D("333.33"), hsn_code="6205"), interstate=False)
    tax = q(r.taxable_value * r.gst_rate / 100)
    check("halves reconcile to the exact tax on an odd amount",
          r.cgst + r.sgst == tax, f"{r.cgst}+{r.sgst} vs {tax}")


def test_interstate_uses_igst_only():
    r = compute_line(LineInput("p", D("2"), D("3000"), hsn_code="6203"), interstate=True)
    check("18% slab above the threshold", r.gst_rate == GST_STD)
    check("igst carries the whole tax", r.igst == q(r.taxable_value * D("18") / 100))
    check("no cgst/sgst on an inter-state line", r.cgst == 0 and r.sgst == 0)


def test_discount_can_move_a_line_across_the_slab():
    """The commercial reason the rate cannot live on the product master."""
    full = compute_line(LineInput("p", D("1"), D("2800"), hsn_code="6205"), interstate=False)
    disc = compute_line(LineInput("p", D("1"), D("2800"), discount_pct=D("20"),
                                  hsn_code="6205"), interstate=False)
    check("Rs 2,800 undiscounted -> 18%", full.gst_rate == GST_STD)
    check("same garment at 20% off -> Rs 2,240 -> 5%", disc.gst_rate == GST_LOW,
          f"{disc.taxable_per_piece} -> {disc.gst_rate}%")
    check("discount amount is recorded", disc.discount_amount == D("560.00"))


def test_mixed_slab_invoice():
    inv = compute_invoice([
        LineInput("a", D("2"), D("1499"), hsn_code="6205", description="Kurta"),
        LineInput("b", D("1"), D("2799"), hsn_code="6203", description="Jeans"),
    ], seller_state="29", buyer_state="29")
    rates = {l.gst_rate for l in inv.lines}
    check("one invoice legitimately holds both slabs", rates == {GST_LOW, GST_STD}, str(rates))
    check("lines sum to the taxable total",
          sum(l.taxable_value for l in inv.lines) == inv.taxable_total)
    check("tax total = cgst + sgst + igst",
          inv.tax_total == inv.cgst_total + inv.sgst_total + inv.igst_total)
    check("grand total = taxable + tax + round off",
          inv.grand_total == q(inv.taxable_total + inv.tax_total + inv.round_off),
          f"{inv.grand_total}")
    check("grand total is whole rupees", inv.grand_total == inv.grand_total.quantize(D("1")))
    check("round off is under 50 paise", abs(inv.round_off) <= D("0.50"), str(inv.round_off))
    summary = inv.rate_summary()
    check("rate summary groups by slab", set(summary) == {GST_LOW, GST_STD})
    check("rate summary taxable reconciles",
          sum(v["taxable"] for v in summary.values()) == inv.taxable_total)


def test_place_of_supply_switches_the_split():
    lines = [LineInput("a", D("1"), D("1000"), hsn_code="6205")]
    same = compute_invoice(lines, seller_state="29", buyer_state="29")
    other = compute_invoice(lines, seller_state="29", buyer_state="27")
    check("same state -> CGST + SGST, no IGST",
          same.cgst_total > 0 and same.sgst_total > 0 and same.igst_total == 0)
    check("different state -> IGST only",
          other.igst_total > 0 and other.cgst_total == 0 and other.sgst_total == 0)
    check("total tax is identical either way", same.tax_total == other.tax_total)
    check("grand total is identical either way", same.grand_total == other.grand_total)
    check("unknown buyer state is treated as intra-state (safe default)",
          compute_invoice(lines, "29", None).is_interstate is False)


def test_zero_and_edge_quantities():
    inv = compute_invoice([], seller_state="29", buyer_state="29")
    check("empty invoice totals to zero", inv.grand_total == 0 and inv.taxable_total == 0)
    r = compute_line(LineInput("p", D("1"), D("0"), hsn_code="6205"), interstate=False)
    check("zero-price line yields zero tax", r.line_total == 0 and r.gst_rate == GST_LOW)
    r2 = compute_line(LineInput("p", D("100"), D("15"), hsn_code="6205"), interstate=False)
    check("bulk of cheap pieces stays in the 5% slab (per PIECE, not per line)",
          r2.gst_rate == GST_LOW and r2.taxable_value == D("1500.00"),
          f"per_piece={r2.taxable_per_piece}")


def test_amount_in_words():
    cases = [
        (0, "Zero Rupees Only"),
        (1, "One Rupees Only"),
        (250, "Two Hundred and Fifty Rupees Only"),
        (1499, "One Thousand Four Hundred and Ninety Nine Rupees Only"),
        (100000, "One Lakh Rupees Only"),
        (2500000, "Twenty Five Lakh Rupees Only"),
    ]
    for amt, expected in cases:
        got = amount_in_words(amt)
        check(f"words({amt}) reads correctly", got == expected, f"got '{got}'")
    check("paise are spelled out",
          "Fifty Paise" in amount_in_words(D("10.50")), amount_in_words(D("10.50")))
    check("crore scale works", amount_in_words(12345678).startswith("One Crore"),
          amount_in_words(12345678))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nGST engine tests ({len(tests)} groups)")
    print("=" * 62)
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "=" * 62)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
