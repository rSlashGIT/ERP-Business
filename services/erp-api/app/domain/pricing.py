"""Price intelligence — what should this garment sell for?

SmartStock forecasts DEMAND. This turns that into a PRICE, using the shop's own
sales history and the one piece of arithmetic every Indian apparel retailer is
getting wrong.

THE THREE THINGS THIS ANSWERS
-----------------------------
1. **What price makes the most money?** Estimate how sensitive this style's
   demand is to price from the shop's own discounting, then search the price
   grid for the peak of margin x volume.

2. **Is this price sitting in the dead zone?**  <- the one that sells the product
   GST on garments is 5% up to Rs 2,500 a piece and 18% above it. That is a
   step, not a slope. Just over the line the customer pays Rs 325 more while
   the shop earns Rs 1 more. There is a band of shelf prices that is strictly
   worse for BOTH sides than pricing at the boundary, and shops sit in it
   constantly because they price on the tag, not on the taxable value.

3. **Will this clear before the season ends, and if not, what markdown?**
   Apparel dies of leftover stock. Compare holding the price against cutting
   it: more units at a thinner margin usually beats a pile of dead stock at
   salvage value, but not always, and the crossover is computable.

Framework-free, like `gst.py` and `importing.py` — the FastAPI routes and the
demo server both import this, so the advice can never differ between them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .gst import APPAREL_THRESHOLD, GST_LOW, GST_STD, rate_for_line

# ───────────────────────── elasticity ─────────────────────────

# Apparel price elasticity from the retail literature sits around -1.8: a 10%
# price cut lifts units about 18%. Used as the prior a shop's own data is
# shrunk towards, so a style with three sales does not get a wild estimate.
PRIOR_ELASTICITY = -1.8
SHRINK_STRENGTH = 8.0        # observations needed before the shop's own data dominates
MIN_POINTS = 3               # below this we do not even try to fit
ELASTICITY_FLOOR = -4.0       # beyond this the fit is noise, not economics
ELASTICITY_CEIL = -0.2
#: Minimum within-period log price spread before a fit is even attempted.
#: 0.10 in logs is roughly a 10% price range. Below that the slope is noise —
#: see the M5 finding in estimate_elasticity().
MIN_LOG_SPREAD = 0.10


@dataclass
class Elasticity:
    value: float                 # negative: units fall as price rises
    n_points: int                # distinct price points observed
    confidence: str              # low | medium | high
    source: str                  # measured | blended | prior

    @property
    def pct_volume_per_10pct_cut(self) -> float:
        return abs(self.value) * 10.0

    def as_dict(self) -> dict:
        return {"value": round(self.value, 3), "n_points": self.n_points,
                "confidence": self.confidence, "source": self.source,
                "volume_gain_per_10pct_cut": round(self.pct_volume_per_10pct_cut, 1)}


def estimate_elasticity(observations: Sequence[Sequence]) -> Elasticity:
    """Fit ln(units) = a + b*ln(price) by least squares; b is the elasticity.

    `observations` are (price_per_piece, units, period) — period optional.

    WHY THE PERIOD MATTERS
    ----------------------
    Fitting price against volume across a whole year gives the WRONG SIGN, and
    confidently. Indian apparel sells at full price during Navratri and Diwali,
    when footfall is at its peak, and discounts hardest in January and July,
    when nobody is shopping. Regress the raw pairs and the festive months tell
    you that raising prices sells more. Measured on a year of real trading here,
    that is exactly what happened — every style fitted a positive slope and the
    estimator fell back to the prior, so the shop's own data was doing nothing.

    The fix is a within-period fixed effect: compare each price to the AVERAGE
    PRICE IN ITS OWN MONTH, and each volume to that month's average volume. The
    seasonal demand shock is common to every observation in a month, so it
    cancels, and what is left is the shop's genuine response to price.

    The slope is then shrunk towards the category prior by evidence weight
    w = n/(n+k), because reporting a confident elasticity off three sales is how
    you talk a shopkeeper into a markdown that loses them money.
    """
    pts = []
    for o in observations:
        p, q = float(o[0]), float(o[1])
        period = o[2] if len(o) > 2 else "all"
        if p > 0 and q > 0:
            pts.append((p, q, period))
    if not pts:
        return Elasticity(PRIOR_ELASTICITY, 0, "low", "prior")

    # Collapse duplicate prices WITHIN a period: ten invoices at one price in
    # one month is one price point of evidence, not ten.
    grouped: Dict[Tuple[Any, float], float] = {}
    for p, q, period in pts:
        grouped[(period, round(p, 2))] = grouped.get((period, round(p, 2)), 0.0) + q

    by_period: Dict[Any, List[Tuple[float, float]]] = {}
    for (period, p), q in grouped.items():
        by_period.setdefault(period, []).append((p, q))

    # Within-period demeaning in log space.
    xs: List[float] = []
    ys: List[float] = []
    for rows in by_period.values():
        if len(rows) < 2:
            continue                      # a lone point carries no within-period signal
        lp = [math.log(p) for p, _ in rows]
        lq = [math.log(q) for _, q in rows]
        mp, mq = sum(lp) / len(lp), sum(lq) / len(lq)
        xs += [v - mp for v in lp]
        ys += [v - mq for v in lq]

    n = len(xs)
    n_points = len(grouped)
    if n < MIN_POINTS:
        return Elasticity(PRIOR_ELASTICITY, n_points, "low", "prior")

    # HAS THE PRICE ACTUALLY MOVED ENOUGH TO LEARN FROM?
    #
    # This is the finding that explains the whole M5 result. Fitted elasticity
    # beat the textbook prior on only 3 of 30 SKUs there, and no amount of
    # pooling, day-of-week controls or outlier trimming moved it — because the
    # median M5 SKU's price moved 9.3% across 1,895 days, several never changed
    # price at all, and daily demand has a coefficient of variation near 1.0.
    # You cannot recover a price response from a 9% signal buried in 94% noise.
    # It was never a modelling failure; the information is not in the data.
    #
    # A shop that never discounts is in exactly this position, so say so rather
    # than fitting a confident-looking line through nothing.
    spread = (max(xs) - min(xs)) if xs else 0.0        # in log space
    if spread < MIN_LOG_SPREAD:
        return Elasticity(PRIOR_ELASTICITY, n_points, "low", "insufficient-variation")

    sxx = sum(x * x for x in xs)
    if sxx <= 1e-9:
        return Elasticity(PRIOR_ELASTICITY, n_points, "low", "prior")
    raw = sum(x * y for x, y in zip(xs, ys)) / sxx

    # Still positive after removing the seasonal effect? Then something other
    # than price is driving it and we have no business guessing what.
    if raw >= 0:
        return Elasticity(PRIOR_ELASTICITY, n_points, "low", "prior")

    w = n / (n + SHRINK_STRENGTH)
    blended = w * raw + (1 - w) * PRIOR_ELASTICITY
    blended = max(ELASTICITY_FLOOR, min(ELASTICITY_CEIL, blended))

    # Confidence is deliberately hard to earn. Backtested on M5 (Walmart, real
    # promotional price moves, 30 SKUs, 30% held out) the fitted elasticity
    # called the DIRECTION of the demand response right 67% of the time — real
    # signal — but beat the textbook prior on only 3 SKUs in 30. So the sign is
    # trustworthy and the magnitude is not, and the label has to say so:
    # "high" is reserved for enough spread that a rupee figure means something.
    spread = max(x for x in xs) - min(x for x in xs) if xs else 0.0
    if n >= 30 and spread >= 0.25:            # ~25% price range, well sampled
        conf = "high"
    elif n >= 12 and spread >= 0.12:
        conf = "medium"
    else:
        conf = "low"
    return Elasticity(blended, n_points, conf, "measured" if w > 0.6 else "blended")


# ───────────────────────── the GST slab cliff ─────────────────────────

def shelf_price(taxable: float) -> float:
    """What the customer actually pays, GST included, for an apparel line."""
    rate = rate_for_line(Decimal(str(taxable)), hsn_code="6103")
    return float(Decimal(str(taxable)) * (1 + rate / 100))


#: Taxable value at the top of the 5% slab, and the shelf price there.
CLIFF_TAXABLE = float(APPAREL_THRESHOLD)                 # 2500.00
CLIFF_SHELF = round(CLIFF_TAXABLE * 1.05, 2)             # 2625.00
#: The cheapest shelf price reachable once you are in the 18% slab.
CLIFF_SHELF_ABOVE = round(CLIFF_TAXABLE * 1.18, 2)       # 2950.00


@dataclass
class CliffWarning:
    """A shelf price sitting just over the GST step."""
    current_shelf: float
    current_taxable: float
    better_shelf: float
    better_taxable: float
    customer_saves: float        # what the tag falls by
    shop_per_piece: float        # what the shop keeps per piece: +gain / -give-up
    tax_saved: float             # tax that stops being collected on each piece

    def as_dict(self) -> dict:
        return {k: round(v, 2) for k, v in self.__dict__.items()}


def check_cliff(taxable: float) -> Optional[CliffWarning]:
    """Is this price inside the GST dead zone?

    Garments are 5% up to Rs 2,500 taxable and 18% above. So:

        taxable 2,500.00  ->  customer pays 2,625.00   (5%)
        taxable 2,500.01  ->  customer pays 2,950.01  (18%)

    No shelf price between Rs 2,625 and Rs 2,950 exists on the far side of that
    step. A shop pricing a kurta at a shelf price in that band is charging the
    customer MORE than Rs 2,625 while keeping LESS than Rs 2,500 — the whole
    difference goes to tax. Dropping to the boundary makes the customer pay
    less AND the shop keep more. It is free money in both directions, and it is
    invisible unless you are looking at taxable values rather than tags.
    """
    if taxable <= CLIFF_TAXABLE:
        return None
    # Above taxable 2,950 the shop keeps more than the boundary would give AND
    # the tag has cleared the dead band — a genuinely premium garment. Leave it.
    if taxable >= CLIFF_TAXABLE * 1.18:
        return None
    current_shelf = round(taxable * 1.18, 2)
    return CliffWarning(
        current_shelf=current_shelf,
        current_taxable=round(taxable, 2),
        better_shelf=CLIFF_SHELF,
        better_taxable=CLIFF_TAXABLE,
        customer_saves=round(current_shelf - CLIFF_SHELF, 2),
        # Negative when the shop gives up margin per piece. It nearly always
        # does — the case is that the tag falls several times further than the
        # margin does, because the difference was going to tax, not to anyone.
        shop_per_piece=round(CLIFF_TAXABLE - taxable, 2),
        tax_saved=round((current_shelf - taxable) - (CLIFF_SHELF - CLIFF_TAXABLE), 2),
    )


# ───────────────────────── optimal price ─────────────────────────

@dataclass
class PricePoint:
    taxable: float
    shelf: float
    gst_rate: float
    units: float
    revenue: float          # taxable revenue to the shop
    profit: float
    is_cliff_edge: bool = False

    def as_dict(self) -> dict:
        return {"taxable": round(self.taxable, 2), "shelf": round(self.shelf, 2),
                "gst_rate": float(self.gst_rate), "units": round(self.units, 1),
                "revenue": round(self.revenue, 2), "profit": round(self.profit, 2),
                "is_cliff_edge": self.is_cliff_edge}


def demand_at(base_units: float, base_price: float, price: float, elasticity: float) -> float:
    """Constant-elasticity demand: q = q0 * (p/p0)^e.

    Modelled on the SHELF price, not the taxable value, because the customer
    reacts to the number on the tag — which is exactly why the GST step matters
    so much and why modelling on taxable value would hide it entirely.
    """
    if base_price <= 0 or price <= 0:
        return 0.0
    return max(0.0, base_units * (price / base_price) ** elasticity)


#: How far beyond the prices a shop has actually charged we are willing to go.
#: A constant-elasticity curve fitted over a 20% discount range says nothing
#: reliable about a 45% cut — extrapolating it produced a recommendation to
#: drop a Rs 2,700 kurta to Rs 1,676 and a straight-faced claim of Rs 337,620
#: a year from one style. Advice a shopkeeper can tell is nonsense costs you
#: the whole account, so the search is confined to a trust region.
EXTRAPOLATION_MARGIN = 0.12


def optimise_price(
    *,
    cost: float,
    current_taxable: float,
    base_units: float,
    elasticity: float,
    min_margin_pct: float = 5.0,
    span: float = 0.45,
    steps: int = 90,
    observed_taxable: Sequence[float] = (),
) -> Tuple[PricePoint, PricePoint, List[PricePoint]]:
    """Search the price grid for peak profit. Returns (current, best, curve).

    The grid always includes the GST boundary exactly, because the optimum for
    an apparel style priced anywhere near Rs 2,500 is very often the boundary
    itself and a coarse grid would step straight over it.

    `observed_taxable` are prices this style has actually sold at. When they
    exist the search is clamped to that range +/- 12%, so the recommendation is
    always interpolation between things the shop has really done.
    """
    if current_taxable <= 0:
        current_taxable = max(cost * 1.4, 1.0)
    base_shelf = shelf_price(current_taxable)
    floor = max(cost * (1 + min_margin_pct / 100), 1.0)

    lo, hi = current_taxable * (1 - span), current_taxable * (1 + span)
    seen = [p for p in observed_taxable if p > 0]
    if seen:
        lo = max(lo, min(seen) * (1 - EXTRAPOLATION_MARGIN))
        hi = min(hi, max(seen) * (1 + EXTRAPOLATION_MARGIN))
        lo, hi = min(lo, current_taxable), max(hi, current_taxable)
    # INELASTIC DEMAND HAS NO INTERIOR OPTIMUM.
    # With |e| < 1 a constant-elasticity curve says revenue rises forever as you
    # raise the price, so the search pins to whatever ceiling it is given and
    # reports the gain as fact. On a year of real trading here that produced
    # "raise the tag and earn Rs 628,913 a year" for every single style — which
    # is an artefact of the functional form, not a finding. Measurement noise
    # also attenuates the fitted slope towards zero, so a weak reading is
    # exactly when to trust the model least.
    #
    # So when demand measures inelastic we allow only a small, testable step,
    # and the caller labels it a test rather than a recommendation.
    inelastic = elasticity > -1.0
    if inelastic:
        hi = min(hi, current_taxable * 1.05)
        lo = max(lo, current_taxable * 0.95)

    if hi <= lo:
        lo, hi = current_taxable * 0.95, current_taxable * 1.05

    grid = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]
    if lo <= CLIFF_TAXABLE <= hi:
        grid += [CLIFF_TAXABLE, CLIFF_TAXABLE - 0.01]
    grid = sorted({round(max(floor, g), 2) for g in grid})

    curve: List[PricePoint] = []
    for taxable in grid:
        shelf = shelf_price(taxable)
        units = demand_at(base_units, base_shelf, shelf, elasticity)
        rate = float(rate_for_line(Decimal(str(taxable)), hsn_code="6103"))
        curve.append(PricePoint(
            taxable=taxable, shelf=shelf, gst_rate=rate, units=units,
            revenue=taxable * units, profit=(taxable - cost) * units,
            is_cliff_edge=abs(taxable - CLIFF_TAXABLE) < 0.02))

    best = max(curve, key=lambda p: p.profit)
    cur_units = base_units
    cur = PricePoint(
        taxable=current_taxable, shelf=base_shelf,
        gst_rate=float(rate_for_line(Decimal(str(current_taxable)), hsn_code="6103")),
        units=cur_units, revenue=current_taxable * cur_units,
        profit=(current_taxable - cost) * cur_units)
    return cur, best, curve


# ───────────────────────── markdown / clearance ─────────────────────────

#: What leftover apparel actually fetches at end of season — a jobber's price.
SALVAGE_FRACTION = 0.30


@dataclass
class MarkdownPlan:
    recommended_taxable: float
    recommended_shelf: float
    discount_pct: float
    units_expected: float
    units_left: float
    profit_if_held: float
    profit_if_marked_down: float
    gain: float
    urgency: str                # none | watch | act | urgent
    reason: str

    def as_dict(self) -> dict:
        d = {k: (round(v, 2) if isinstance(v, float) else v)
             for k, v in self.__dict__.items()}
        return d


def plan_markdown(
    *,
    cost: float,
    current_taxable: float,
    on_hand: float,
    daily_units: float,
    days_left: int,
    elasticity: float,
    salvage_fraction: float = SALVAGE_FRACTION,
) -> MarkdownPlan:
    """Hold the price, or cut it? Compare the money, not the instinct.

    Holding earns full margin on whatever sells and salvage on the rest.
    Cutting earns a thinner margin on more units. Which wins depends on how
    much stock is stranded and how price-sensitive the style is — and shops
    consistently mark down too late, when the remaining days can no longer
    absorb the extra volume.
    """
    on_hand = max(0.0, on_hand)
    days_left = max(0, int(days_left))
    base_shelf = shelf_price(current_taxable)
    salvage = cost * salvage_fraction

    def outcome(taxable: float) -> Tuple[float, float, float]:
        shelf = shelf_price(taxable)
        rate = demand_at(daily_units, base_shelf, shelf, elasticity)
        sold = min(on_hand, rate * days_left)
        left = on_hand - sold
        profit = sold * (taxable - cost) + left * (salvage - cost)
        return profit, sold, left

    hold_profit, hold_sold, hold_left = outcome(current_taxable)

    best_taxable, best_profit, best_sold, best_left = current_taxable, hold_profit, hold_sold, hold_left
    for pct in range(5, 61, 5):
        t = round(current_taxable * (1 - pct / 100), 2)
        if t <= cost:                    # never recommend selling below cost
            break
        p, s, l = outcome(t)
        if p > best_profit + 0.01:
            best_taxable, best_profit, best_sold, best_left = t, p, s, l

    discount = (1 - best_taxable / current_taxable) * 100 if current_taxable else 0.0
    gain = best_profit - hold_profit
    sell_through = (hold_sold / on_hand) if on_hand else 1.0

    if on_hand <= 0:
        urgency, reason = "none", "Nothing left to clear."
    elif discount < 1:
        urgency = "none"
        reason = (f"On track — about {hold_sold:.0f} of {on_hand:.0f} pieces will sell "
                  f"in {days_left} days at today's price. Hold.")
    elif sell_through >= 0.9:
        urgency = "watch"
        reason = (f"Will very nearly clear anyway. A {discount:.0f}% cut earns about "
                  f"{gain:,.0f} more, but you can wait.")
    elif days_left <= 30:
        urgency = "urgent"
        reason = (f"Only {days_left} days left and {hold_left:.0f} of {on_hand:.0f} pieces "
                  f"would be stranded at salvage. Cut {discount:.0f}% now.")
    else:
        urgency = "act"
        reason = (f"At today's price {hold_left:.0f} of {on_hand:.0f} pieces are left over. "
                  f"A {discount:.0f}% cut clears {best_sold:.0f} and earns about "
                  f"{gain:,.0f} more.")

    return MarkdownPlan(
        recommended_taxable=best_taxable, recommended_shelf=shelf_price(best_taxable),
        discount_pct=discount, units_expected=best_sold, units_left=best_left,
        profit_if_held=hold_profit, profit_if_marked_down=best_profit,
        gain=gain, urgency=urgency, reason=reason)


# ───────────────────────── the whole answer for one style ─────────────────────────

@dataclass
class PriceAdvice:
    style_code: str
    style_name: str
    cost: float
    current_taxable: float
    current_shelf: float
    current_gst: float
    on_hand: float
    daily_units: float
    elasticity: Elasticity
    best: PricePoint
    current: PricePoint
    cliff: Optional[CliffWarning]
    markdown: MarkdownPlan
    curve: List[PricePoint] = field(default_factory=list)

    @property
    def annual_gain(self) -> float:
        """Extra profit per year from moving to the recommended price."""
        return (self.best.profit - self.current.profit) * 365

    @property
    def basis(self) -> str:
        """How much this advice can actually be trusted. Three tiers, because
        they are epistemically different and a shopkeeper deserves to know which
        one they are looking at.

            arithmetic  provable on a calculator — the GST slab boundary, a
                        price below cost. Cannot be wrong.
            observed    measured from this shop's own sell-through and stock —
                        the clearance maths. Needs only that discounts sell
                        more, which is not in doubt.
            estimated   depends on a fitted price elasticity. VALIDATED ON
                        NEITHER public dataset: M5 offers just 13 usable
                        price-change events across 30 SKUs and 1,895 days
                        (8/13 called right, p=0.29 — chance), and BigMart's
                        cross-sectional price spread is confounded by product
                        quality (48% of 1,693 held-out pairs — chance). So this
                        tier is presented as an experiment to run, never as a
                        prediction, and never with a rupee figure attached.
        """
        if self.cliff or (self.cost and self.current_taxable < self.cost):
            return "arithmetic"
        if self.markdown.urgency in ("act", "urgent"):
            return "observed"
        return "estimated"

    @property
    def headline(self) -> str:
        """One sentence, and it must never over-claim.

        Ordered by how certain the advice is, not by how large it sounds:
        the GST boundary and a below-cost price are arithmetic; the clearance
        maths is measured from this shop's own sell-through; everything that
        rests on a fitted elasticity is offered as an experiment, because that
        is all the evidence supports (see `basis`).
        """
        if self.cost and self.current_taxable < self.cost:
            return (f"You are selling this below what it cost you — Rs "
                    f"{self.cost:,.0f} to buy, Rs {self.current_taxable:,.0f} taxable "
                    f"on the tag. Fix this one first.")

        if self.cliff:
            c = self.cliff
            return (f"Move the tag to Rs {c.better_shelf:,.0f}. The customer pays Rs "
                    f"{c.customer_saves:,.0f} less for the same garment, and Rs "
                    f"{c.tax_saved:,.0f} of that was going to tax rather than to you. "
                    f"Nothing should be tagged between Rs {CLIFF_SHELF:,.0f} and "
                    f"Rs {CLIFF_SHELF_ABOVE:,.0f}.")

        if self.markdown.urgency in ("act", "urgent"):
            return self.markdown.reason

        if self.elasticity.source == "insufficient-variation":
            return ("This style has only ever sold at one price, so there is nothing "
                    "to learn from yet. Run one sale on it and the advice sharpens.")

        move = self.best.taxable - self.current_taxable
        if abs(move) < max(1.0, self.current_taxable * 0.02):
            return "Priced about right. Nothing to do."

        direction = "raising" if move > 0 else "cutting"
        return (f"Worth TRYING at Rs {self.best.shelf:,.0f} — your own sales lean towards "
                f"{direction} it. Treat that as an experiment to run for a month, not a "
                f"promise: price response is genuinely hard to measure and we will not "
                f"put a rupee figure on it until yours is measurable.")

    @property
    def cliff_note(self) -> Optional[str]:
        """Shown beside the headline, never instead of it."""
        if not self.cliff:
            return None
        c = self.cliff
        return (f"This garment is priced just over the GST line. At Rs {c.current_shelf:,.0f} "
                f"the customer pays Rs {c.customer_saves:,.0f} more than they would at "
                f"Rs {c.better_shelf:,.0f} — and Rs {c.tax_saved:,.0f} of that goes to tax, "
                f"not to you. No garment should ever be tagged between Rs "
                f"{CLIFF_SHELF:,.0f} and Rs {CLIFF_SHELF_ABOVE:,.0f}.")

    def as_dict(self) -> dict:
        return {
            "style_code": self.style_code, "style_name": self.style_name,
            "cost": round(self.cost, 2),
            "current_taxable": round(self.current_taxable, 2),
            "current_shelf": round(self.current_shelf, 2),
            "current_gst": float(self.current_gst),
            "on_hand": self.on_hand, "daily_units": round(self.daily_units, 3),
            "elasticity": self.elasticity.as_dict(),
            "current": self.current.as_dict(), "best": self.best.as_dict(),
            "cliff": self.cliff.as_dict() if self.cliff else None,
            "markdown": self.markdown.as_dict(),
            "annual_gain": round(self.annual_gain, 2),
            "basis": self.basis,
            # Only an arithmetic finding earns a rupee promise.
            "show_money": self.basis == "arithmetic",
            "is_test": self.basis == "estimated",
            "headline": self.headline,
            "curve": [p.as_dict() for p in self.curve],
        }


def advise(
    *,
    style_code: str,
    style_name: str,
    cost: float,
    current_taxable: float,
    on_hand: float,
    observations: Sequence[Tuple[float, float]],
    daily_units: float,
    days_left: int = 90,
) -> PriceAdvice:
    """Everything the Price screen needs for one style, in one call."""
    el = estimate_elasticity(observations)
    cur, best, curve = optimise_price(
        cost=cost, current_taxable=current_taxable,
        base_units=max(daily_units, 1e-6), elasticity=el.value,
        observed_taxable=[float(o[0]) for o in observations])
    return PriceAdvice(
        style_code=style_code, style_name=style_name, cost=cost,
        current_taxable=current_taxable, current_shelf=shelf_price(current_taxable),
        current_gst=float(rate_for_line(Decimal(str(current_taxable)), hsn_code="6103")),
        on_hand=on_hand, daily_units=daily_units, elasticity=el,
        best=best, current=cur, cliff=check_cliff(current_taxable),
        markdown=plan_markdown(
            cost=cost, current_taxable=current_taxable, on_hand=on_hand,
            daily_units=daily_units, days_left=days_left, elasticity=el.value),
        curve=curve)
