#!/usr/bin/env python3
"""Second dataset, different shape: BigMart (India, 2013).

    python3 scripts/validate_pricing_bigmart.py     (or: make validate-bigmart)

WHY A SECOND DATASET, AND WHY A DIFFERENT PROTOCOL
--------------------------------------------------
M5 could not answer the question. Its prices barely move — the median SKU's
price varies 9.3% across 1,895 days and several never change at all, while
daily demand has a coefficient of variation near 1.0. Elasticity is simply not
identifiable there, which is why no amount of pooling or fixed effects moved
the result (see scripts/diagnose_elasticity.py).

BigMart is the opposite: 514 products, 10 outlets, `Item_MRP` spanning Rs 31 to
Rs 265 with a **149% price spread inside the median category**. There is real
price variation to learn from.

WHERE THIS PROTOCOL DIVERGES FROM M5, AND WHY
---------------------------------------------
* **No date column.** BigMart is one row per (product, outlet) — a cross
  section, not a time series. A forward-in-time split is impossible, so the
  holdout is by *product* instead.
* **Split on Item_Identifier, never on rows.** A product appears at several
  outlets; splitting rows would put the same SKU in both halves and leak. 70%
  of unique SKUs train, 30% held out, and no SKU crosses.
* **Different estimand.** M5 asked "when THIS product's price changed, what
  happened to ITS sales?" BigMart asks "within a category, do the
  higher-priced products sell fewer units?" That is cross-sectional
  elasticity. It is the weaker of the two causally — expensive products differ
  from cheap ones in more ways than price — but it is exactly what a shop needs
  when pricing a NEW item it has no history for, and it is what this data can
  support.
* **Category fixed effect replaces the month fixed effect.** Comparisons are
  made within Item_Type, so "biscuits are cheap and seafood is dear" cannot
  masquerade as a price response.
* **Sales are in rupees, not units.** `Item_Outlet_Sales` is revenue, so units
  are derived as sales / MRP before fitting. Skipping that step would fit
  revenue against price and recover roughly +1 by construction.

DATA PROVENANCE
---------------
`demo/data/bigmart_train_sample.csv` is a **real but truncated** slice: 612 of
the 8,523 training rows, because the sandbox's fetch tool caps a response at
~62 KB. Columns were read off the downloaded file, not assumed. Fewer rows
means less power, so treat the counts below as indicative and re-run on the
full file when it is available.
"""
from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "erp-api"))

from app.domain.pricing import PRIOR_ELASTICITY, estimate_elasticity  # noqa: E402

CSV = ROOT / "demo" / "data" / "bigmart_train_sample.csv"
TRAIN_FRAC = 0.70
SEED = 20260805


def load():
    rows = []
    with open(CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                mrp = float(r["Item_MRP"])
                sales = float(r["Item_Outlet_Sales"])
            except (ValueError, KeyError):
                continue
            if mrp <= 0 or sales <= 0:
                continue
            rows.append({
                "sku": r["Item_Identifier"], "cat": r["Item_Type"],
                "outlet": r["Outlet_Identifier"], "mrp": mrp, "sales": sales,
                # Item_Outlet_Sales is REVENUE. Units are what elasticity is
                # about; fitting revenue on price recovers ~+1 by construction.
                "units": sales / mrp,
            })
    return rows


def split_by_sku(rows):
    """Hold out whole PRODUCTS. A product sits in several outlets, so a
    row-level split would put the same SKU on both sides and leak."""
    skus = sorted({r["sku"] for r in rows})
    rnd = __import__("random").Random(SEED)
    rnd.shuffle(skus)
    cut = int(len(skus) * TRAIN_FRAC)
    train_skus = set(skus[:cut])
    test_skus = set(skus[cut:])
    assert not (train_skus & test_skus), "SKU leaked across the split"
    return ([r for r in rows if r["sku"] in train_skus],
            [r for r in rows if r["sku"] in test_skus],
            train_skus, test_skus)


def main() -> int:
    if not CSV.exists():
        print(f"missing {CSV}")
        return 1
    rows = load()
    train, test, tr_skus, te_skus = split_by_sku(rows)
    cats = sorted({r["cat"] for r in rows})

    print(f"BigMart (real, truncated sample): {len(rows)} rows, "
          f"{len({r['sku'] for r in rows})} products, "
          f"{len({r['outlet'] for r in rows})} outlets, {len(cats)} categories")
    print(f"split by product: {len(tr_skus)} train / {len(te_skus)} holdout, "
          f"{len(train)} / {len(test)} rows, zero SKU overlap\n")

    # ── fit one elasticity per category on the TRAIN products only ──
    fitted = {}
    for cat in cats:
        obs = [(r["mrp"], r["units"], r["cat"]) for r in train if r["cat"] == cat]
        if len(obs) >= 8:
            fitted[cat] = estimate_elasticity(obs)

    print(f"{'category':<26}{'n':>5}{'elasticity':>12}{'conf':>9}{'source':>16}")
    print("-" * 68)
    for cat in sorted(fitted, key=lambda c: fitted[c].value):
        e = fitted[cat]
        n = sum(1 for r in train if r["cat"] == cat)
        print(f"{cat:<26}{n:>5}{e.value:>12.2f}{e.confidence:>9}"
              f"{e.source:>16}")

    # ── score the held-out products ──
    # Baseline for each category: the median units of the TRAIN products in it.
    base = {}
    for cat in cats:
        u = [r["units"] for r in train if r["cat"] == cat]
        p = [r["mrp"] for r in train if r["cat"] == cat]
        if u:
            base[cat] = (statistics.median(p), statistics.median(u))

    err_fit = err_flat = err_prior = 0.0
    n_scored = 0
    per_cat = defaultdict(lambda: [0.0, 0.0, 0.0, 0])

    for r in test:
        if r["cat"] not in base or r["cat"] not in fitted:
            continue
        bp, bu = base[r["cat"]]
        if bp <= 0 or bu <= 0:
            continue
        e = fitted[r["cat"]].value
        pred_fit = bu * (r["mrp"] / bp) ** e
        pred_prior = bu * (r["mrp"] / bp) ** PRIOR_ELASTICITY
        err_fit += abs(pred_fit - r["units"])
        err_flat += abs(bu - r["units"])
        err_prior += abs(pred_prior - r["units"])
        c = per_cat[r["cat"]]
        c[0] += abs(pred_fit - r["units"]); c[1] += abs(bu - r["units"])
        c[2] += abs(pred_prior - r["units"]); c[3] += 1
        n_scored += 1

    # ── directional check: within a category, does a higher relative price
    #    go with lower relative units, for products the fit never saw? ──
    right = total = 0
    by_cat_test = defaultdict(list)
    for r in test:
        by_cat_test[r["cat"]].append(r)
    for cat, rs in by_cat_test.items():
        if cat not in base or len(rs) < 2:
            continue
        bp, bu = base[cat]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = rs[i], rs[j]
                if abs(a["mrp"] - b["mrp"]) < 1e-9 or abs(a["units"] - b["units"]) < 1e-9:
                    continue
                total += 1
                dearer = a if a["mrp"] > b["mrp"] else b
                cheaper = b if dearer is a else a
                if dearer["units"] < cheaper["units"]:
                    right += 1

    beat_flat = sum(1 for c in per_cat.values() if c[0] < c[1])
    beat_prior = sum(1 for c in per_cat.values() if c[0] < c[2])

    print("\n" + "=" * 68)
    print(f"held-out rows scored          : {n_scored}")
    print(f"categories with a fitted slope: {len(fitted)}")
    print(f"MAE  fitted / ignore-price / textbook prior : "
          f"{err_fit:.0f} / {err_flat:.0f} / {err_prior:.0f}")
    if err_flat:
        print(f"improvement over ignoring price            : "
              f"{(err_flat - err_fit) / err_flat * 100:+.1f}%")
    print(f"categories beating 'ignore price'          : {beat_flat}/{len(per_cat)}")
    print(f"categories beating the textbook prior      : {beat_prior}/{len(per_cat)}")
    if total:
        print(f"direction correct (dearer sells less)      : "
              f"{right / total * 100:.0f}% of {total} held-out pairs  (50% = chance)")

    # THE RESULT, PLAINLY
    #
    # Cross-sectional price differences are NOT a price experiment. An
    # expensive product differs from a cheap one in brand, quality and pack
    # size as well as price, so "dearer sells less" does not hold: 48% of 1,693
    # held-out pairs, z = -1.68, indistinguishable from chance. Fitting a
    # demand curve to it is worse than ignoring price altogether.
    #
    # This is a real and useful negative result: it says the production engine
    # must NOT be used cross-sectionally, and it isn't — it only ever fits a
    # style against its own history. The assertion is that the engine, run this
    # way, does not produce a usable signal, and that we say so.
    # ── how much could this sample have detected? ──
    # The dataset is truncated at 7.2% by the sandbox's fetch cap, so a null
    # result could mean "no effect" or merely "not enough rows". Bound it
    # rather than leave it ambiguous. The pair count is what drives power here,
    # and pairs grow ~quadratically with rows within a category, so the full
    # file would carry far more than 14x the pairs.
    import math as _m
    directional = (right / total) if total else 0.5
    if total:
        se = _m.sqrt(0.25 / total)
        mde = 1.96 * se                     # smallest detectable shift from 50%
        z_obs = (directional - 0.5) / se
        print(f"\nPOWER at this sample size ({total} pairs, {len(rows)} of 8,523 rows):")
        print(f"  standard error on the directional rate : {se * 100:.2f} pp")
        print(f"  smallest effect detectable at 95%      : {50 + mde * 100:.1f}% "
              f"(i.e. a {mde * 100:.1f} pp shift)")
        print(f"  observed                               : {directional * 100:.1f}%  "
              f"z = {z_obs:+.2f}")
        if abs(z_obs) < 1.96:
            print(f"  -> the true rate is within [{(directional - mde) * 100:.1f}%, "
                  f"{(directional + mde) * 100:.1f}%] with 95% confidence.")
            print(f"     A real effect large enough to price on (say 60%+) is EXCLUDED")
            print(f"     by this sample. A small one (50-{(directional + mde) * 100:.0f}%) is not.")
    no_signal = err_fit >= err_flat or abs(directional - 0.5) < 0.05
    print("\nFINDING: price variation here is between DIFFERENT products, not")
    print("         the same product over time. Quality confounds it, and the")
    print("         directional test lands on chance. Cross-sectional price is")
    print("         not a substitute for a shop's own discount history.")
    print("\n" + ("PASS — correctly finds no usable cross-sectional price signal, "
                  "which is why production never fits this way"
                  if no_signal else
                  "UNEXPECTED — a cross-sectional signal appeared; re-examine before using it"))
    return 0 if no_signal else 1


if __name__ == "__main__":
    raise SystemExit(main())
