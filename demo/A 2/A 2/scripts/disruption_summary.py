#!/usr/bin/env python3
"""
Phase 4.5 — 3-way Pareto comparison table for the disruption sweep.

Loads data/disruption_results.json and writes a fixed-width comparison
table to data/disruption_summary_table.txt.

Compares the three policies in disruption_engine.load_policies():
    default_classical    legacy classical (s=1000, S=3000)
    grid_tuned_classical Phase-4.5 grid-search winner
    cmaes_pernode_robust Phase-2.8 production CMA-ES policy

For each scenario it prints all three rows plus three pairwise margins:
    cmaes vs default      (mean profit, p5 tail)
    cmaes vs grid-tuned   (mean profit, p5 tail)
    grid-tuned vs default (mean profit, p5 tail)
A positive margin means the first-named policy wins that dimension.

Run:
    python scripts/disruption_summary.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple


RESULTS_PATH = os.path.join('data', 'disruption_results.json')
OUT_PATH     = os.path.join('data', 'disruption_summary_table.txt')

POLICY_DEFAULT = 'default_classical'
POLICY_GRID    = 'grid_tuned_classical'
POLICY_CMAES   = 'cmaes_pernode_robust'

POLICY_ORDER: List[str] = [POLICY_DEFAULT, POLICY_GRID, POLICY_CMAES]

SCENARIO_ORDER: List[str] = [
    'calm',
    'mild_supplier',
    'major_supplier',
    'demand_spike',
    'port_strike',
    'compound_crisis',
]


def _fmt_money(x: float) -> str:
    return f"{x:>+12,.0f}"


def _fmt_recovery(x) -> str:
    if x is None:
        return "n/a"
    try:
        if x != x:           # NaN check, no numpy import
            return "n/a"
    except TypeError:
        return "n/a"
    return f"{float(x):.1f}d"


def _pairwise(a: Dict, b: Dict) -> Tuple[float, float, str, str]:
    """Return (profit_margin, tail_margin, profit_winner_label, tail_winner_label)
    where margins are signed (a − b) and winner labels say which side won."""
    profit_margin = a['mean_profit'] - b['mean_profit']
    tail_margin   = a['p5_worst']    - b['p5_worst']
    profit_winner = "A" if profit_margin > 0 else ("tie" if profit_margin == 0 else "B")
    tail_winner   = "A" if tail_margin   > 0 else ("tie" if tail_margin   == 0 else "B")
    return profit_margin, tail_margin, profit_winner, tail_winner


def build_table(results: Dict) -> str:
    policies = results['policies']
    for k in POLICY_ORDER:
        if k not in policies:
            raise KeyError(
                f"results.policies must contain {k!r}; "
                f"found {sorted(policies.keys())}"
            )

    meta = results.get('metadata', {})
    n_eps  = meta.get('n_episodes_per_scenario', '?')
    ep_len = meta.get('episode_length_days', '?')
    seed   = meta.get('base_seed', '?')
    src    = meta.get('data_source', '?')

    lines: List[str] = []
    lines.append("=" * 116)
    lines.append("Phase 4.5 — 3-way Disruption Resilience Pareto Table")
    lines.append("=" * 116)
    lines.append(f"  episodes/scenario : {n_eps}")
    lines.append(f"  episode length    : {ep_len} days")
    lines.append(f"  base_seed         : {seed}")
    lines.append(f"  data source       : {src}")
    lines.append(f"  policies compared : "
                 f"{POLICY_DEFAULT!r}  vs  {POLICY_GRID!r}  vs  "
                 f"{POLICY_CMAES!r}")
    for k in POLICY_ORDER:
        p = policies[k]
        s = p.get('warehouse_s')
        S = p.get('warehouse_S')
        src_path = p.get('source_path') or '(builtin)'
        lines.append(f"    {k:<22}  s={s:>6.1f}  S={S:>6.1f}  "
                     f"src={src_path}")
    lines.append("")

    hdr = (f"  {'Scenario':<18}"
           f"{'Policy':<24}"
           f"{'MeanProfit':>14}"
           f"{'P5Worst':>14}"
           f"{'SL':>9}"
           f"{'Drop%':>9}"
           f"{'Recovery':>10}")
    lines.append(hdr)
    lines.append("  " + "-" * (len(hdr) - 2))

    for scen in SCENARIO_ORDER:
        for policy_key in POLICY_ORDER:
            r = policies[policy_key]['scenarios'][scen]
            sl   = r['mean_service_level']
            drop = r['profit_drop_vs_calm_pct']
            rec  = r['recovery_days']
            lines.append(
                f"  {scen:<18}"
                f"{policy_key:<24}"
                f"{_fmt_money(r['mean_profit'])}  "
                f"{_fmt_money(r['p5_worst'])}"
                f"{sl:>9.1%}"
                f"{drop:>+8.1f}%"
                f"{_fmt_recovery(rec):>10}"
            )

        d = policies[POLICY_DEFAULT]['scenarios'][scen]
        g = policies[POLICY_GRID]['scenarios'][scen]
        c = policies[POLICY_CMAES]['scenarios'][scen]

        cd_p, cd_t, cd_pw, cd_tw = _pairwise(c, d)   # cmaes vs default
        cg_p, cg_t, cg_pw, cg_tw = _pairwise(c, g)   # cmaes vs grid
        gd_p, gd_t, gd_pw, gd_tw = _pairwise(g, d)   # grid vs default

        # Re-label winners with policy nicknames for readability.
        def _name(label, A, B):
            if label == 'tie':
                return 'tie'
            return A if label == 'A' else B

        lines.append(
            f"  {'':<18}{'  cmaes − default        :':<24}"
            f"  profit={cd_p:>+11,.0f}  tail={cd_t:>+11,.0f}  "
            f"  winners profit={_name(cd_pw,'cmaes','default')}, "
            f"tail={_name(cd_tw,'cmaes','default')}"
        )
        lines.append(
            f"  {'':<18}{'  cmaes − grid_tuned     :':<24}"
            f"  profit={cg_p:>+11,.0f}  tail={cg_t:>+11,.0f}  "
            f"  winners profit={_name(cg_pw,'cmaes','grid_tuned')}, "
            f"tail={_name(cg_tw,'cmaes','grid_tuned')}"
        )
        lines.append(
            f"  {'':<18}{'  grid_tuned − default   :':<24}"
            f"  profit={gd_p:>+11,.0f}  tail={gd_t:>+11,.0f}  "
            f"  winners profit={_name(gd_pw,'grid_tuned','default')}, "
            f"tail={_name(gd_tw,'grid_tuned','default')}"
        )
        lines.append("  " + "-" * (len(hdr) - 2))

    # ── win-count summary across all 6 scenarios ──
    def _wins(a_key: str, b_key: str) -> Tuple[int, int]:
        wins_profit = 0
        wins_tail   = 0
        for s in SCENARIO_ORDER:
            a = policies[a_key]['scenarios'][s]
            b = policies[b_key]['scenarios'][s]
            if a['mean_profit'] > b['mean_profit']:
                wins_profit += 1
            if a['p5_worst']    > b['p5_worst']:
                wins_tail   += 1
        return wins_profit, wins_tail

    n = len(SCENARIO_ORDER)
    wp_cd, wt_cd = _wins(POLICY_CMAES, POLICY_DEFAULT)
    wp_cg, wt_cg = _wins(POLICY_CMAES, POLICY_GRID)
    wp_gd, wt_gd = _wins(POLICY_GRID,  POLICY_DEFAULT)

    lines.append("")
    lines.append(f"  Pairwise scenario wins (mean / tail) out of {n}:")
    lines.append(f"    cmaes      vs default     : "
                 f"profit {wp_cd}/{n}  tail {wt_cd}/{n}")
    lines.append(f"    cmaes      vs grid_tuned  : "
                 f"profit {wp_cg}/{n}  tail {wt_cg}/{n}")
    lines.append(f"    grid_tuned vs default     : "
                 f"profit {wp_gd}/{n}  tail {wt_gd}/{n}")
    lines.append("")
    lines.append("  Positive Drop% = scenario worse than that policy's "
                 "calm baseline.")
    lines.append("  Pairwise margins are signed (left − right); positive "
                 "means the left-hand policy wins that dimension.")
    lines.append("=" * 116)
    return "\n".join(lines) + "\n"


def main() -> int:
    if not os.path.exists(RESULTS_PATH):
        print(f"ERROR: {RESULTS_PATH} not found — run "
              f"`python src/disruption_engine.py` first.",
              file=sys.stderr)
        return 1
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    table = build_table(results)
    print(table)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        f.write(table)
    print(f"Saved -> {OUT_PATH}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
