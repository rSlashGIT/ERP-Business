#!/usr/bin/env python3
"""Mutation harness: break the code on purpose, prove the tests notice.

    python3 scripts/mutate_check.py            (or: make mutate)

WHY THIS IS A SCRIPT AND NOT A SHELL ONE-LINER
----------------------------------------------
The first version of this was an inline shell function. It contained

    sys.exit(0 if s2 != s else 1) or p.write_text(s2)

`sys.exit` raises immediately, so `p.write_text` never ran. Every mutation
reported "tests still pass" — not because the tests were weak, but because
nothing was ever mutated. A green wall that meant nothing.

So this harness asserts its own preconditions: a mutation whose search text is
not found, or which fails to change the file on disk, is reported as BROKEN and
counts as a failure. A mutation tool you cannot trust is worse than none.
"""
from __future__ import annotations

import atexit
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── crash-safe restore ──
# A mutation is a deliberate edit to a source file. If this process is killed
# between the edit and the restore — a timeout, a Ctrl-C, an OOM — the repo is
# left holding a planted bug. That happened: a run cut short by a timeout left
# `gst_export.py` treating credit notes as POSITIVE outward supply, which is a
# wrong tax return, sitting silently in the working tree.
#
# So every original is stashed before the first edit and put back on ANY exit
# path, not just the happy one.
_ORIGINALS: dict[str, str] = {}


def _restore_all(*_a) -> None:
    for path, text in list(_ORIGINALS.items()):
        try:
            if Path(path).read_text() != text:
                Path(path).write_text(text)
                print(f"  [restored {path}]", file=sys.stderr)
        except Exception:
            pass
    _ORIGINALS.clear()


atexit.register(_restore_all)
for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    try:
        signal.signal(_sig, lambda *a: (_restore_all(), sys.exit(130)))
    except (ValueError, AttributeError, OSError):
        pass


@dataclass
class Mutation:
    label: str          # what defect this simulates
    path: str           # file to mutate, relative to repo root
    find: str
    replace: str
    test: str           # command that must FAIL once mutated
    cwd: str = "."
    also: tuple = ()    # extra (find, replace) pairs applied together

    @property
    def edits(self):
        return ((self.find, self.replace),) + tuple(self.also)


MUTATIONS = [
    # ── importer: column matching ──
    Mutation("column matching accepts a zero-confidence match",
             "services/erp-api/app/domain/importing.py",
             "        if score < 40 or mapping[key] is not None or header in used:",
             "        if mapping[key] is not None or header in used:",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("reverts to field-order matching AND the loose threshold",
             "services/erp-api/app/domain/importing.py",
             "        key=lambda p: (-p[0], p[1], p[2]),",
             "        key=lambda p: (FIELD_ORDER[p[1]], -p[0]),",
             "python3 tests/test_importing.py", "services/erp-api",
             also=(("if any(n in s or s in n for s in field_.synonyms "
                    "if len(s) >= 5 and len(n) >= 5):",
                    "if any(n in s or s in n for s in field_.synonyms "
                    "if len(s) >= 3 and len(n) >= 3):"),)),
    Mutation("loose substring match no longer requires length 5",
             "services/erp-api/app/domain/importing.py",
             "if any(n in s or s in n for s in field_.synonyms if len(s) >= 5 and len(n) >= 5):",
             "if any(n in s or s in n for s in field_.synonyms if len(s) >= 3 and len(n) >= 3):",
             "python3 tests/test_importing.py", "services/erp-api"),

    # ── importer: style grouping ──
    Mutation("per-row style column is never demoted to SKU",
             "services/erp-api/app/domain/importing.py",
             "    return len(set(filled)) < len(filled) * 0.9",
             "    return True",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("size aliases not stripped when deriving a style",
             "services/erp-api/app/domain/importing.py",
             "    tokens = {colour, raw_size, size} | set(SIZE_ALIASES.get(size, ()))",
             "    tokens = {colour, size}",
             "python3 tests/test_importing.py", "services/erp-api"),

    # ── importer: cleaners ──
    Mutation("0.18 no longer recognised as 18%",
             "services/erp-api/app/domain/importing.py",
             "    if n <= 1:                      # 0.18 style",
             "    if False:",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("UOM aliases not canonicalised",
             "services/erp-api/app/domain/importing.py",
             '    return UOM_CANON.get(key, key[:5] or "PCS")',
             '    return key[:5] or "PCS"',
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("short HSN accepted instead of dropped",
             "services/erp-api/app/domain/importing.py",
             "    if len(digits) < 4:",
             "    if False:",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("currency symbols and separators not stripped",
             "services/erp-api/app/domain/importing.py",
             "    s = _NUM_JUNK.sub(\"\", s)",
             "    s = s",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("accounting parentheses no longer mean negative",
             "services/erp-api/app/domain/importing.py",
             '        s = "-" + m.group(1)',
             "        s = m.group(1)",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("delimiter forced to comma (breaks Excel paste)",
             "services/erp-api/app/domain/importing.py",
             '    delim = max(("\\t", ",", ";", "|"), key=lambda d: header.count(d))',
             '    delim = ","',
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("negative stock imported as-is",
             "services/erp-api/app/domain/importing.py",
             "            qty = Decimal(\"0\")",
             "            pass",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("duplicate codes inside one file allowed",
             "services/erp-api/app/domain/importing.py",
             '            r.errors.append(f"same code as line {seen_sku[v[\'sku\']]} in this file")',
             "            pass",
             "python3 tests/test_importing.py", "services/erp-api"),
    Mutation("blank item name accepted",
             "services/erp-api/app/domain/importing.py",
             '            r.errors.append("no item name")',
             "            pass",
             "python3 tests/test_importing.py", "services/erp-api"),

    # ── GST engine ──
    Mutation("apparel slab threshold moved off Rs 2,500",
             "services/erp-api/app/domain/gst.py",
             'APPAREL_THRESHOLD = Decimal("2500")',
             'APPAREL_THRESHOLD = Decimal("999999")',
             "python3 tests/test_gst.py", "services/erp-api"),
    Mutation("CGST/SGST split no longer reconciles",
             "services/erp-api/app/domain/gst.py",
             "        sgst = q(tax - cgst)",
             "        sgst = cgst",
             "python3 tests/test_gst.py", "services/erp-api"),

    # ── billing ──
    Mutation("billing drops the tenant predicate on products",
             "demo/erp/billing.py",
             '" WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()',
             '" WHERE id=? AND is_active=1", (pid,)).fetchone()',
             "python3 demo/verify_erp_demo.py"),
    Mutation("payments allocate newest-first instead of oldest-first",
             "demo/erp/billing.py",
             '" AND grand_total - amount_paid > 0.01 ORDER BY invoice_date, invoice_number",',
             '" AND grand_total - amount_paid > 0.01 ORDER BY invoice_date DESC, invoice_number DESC",',
             "python3 demo/verify_erp_demo.py"),
    Mutation("selling no longer decrements stock",
             "demo/erp/billing.py",
             "SET on_hand = on_hand - ?",
             "SET on_hand = on_hand - 0*?",
             "python3 demo/verify_erp_demo.py"),
    Mutation("variant rows lose their size ordering",
             "demo/erp/api.py",
             "ORDER BY COALESCE(p.size_seq, 99999), p.size, p.colour",
             "ORDER BY p.size, p.colour",
             "python3 demo/verify_erp_demo.py"),

    # ── import commit ──
    Mutation("import ADDS to stock instead of setting it (re-import doubles)",
             "demo/erp/importer.py",
             '                    "UPDATE inventory_levels SET on_hand=?, reorder_point=?"',
             '                    "UPDATE inventory_levels SET on_hand=on_hand+?, reorder_point=?"',
             "python3 demo/verify_erp_demo.py"),
    Mutation("import matches styles without the tenant predicate",
             "demo/erp/importer.py",
             '                "SELECT id FROM product_styles WHERE tenant_id=? AND style_code=?",\n'
             '                (tenant_id, code)).fetchone()',
             '                "SELECT id FROM product_styles WHERE style_code=?",\n'
             '                (code,)).fetchone()',
             "python3 demo/verify_erp_demo.py"),
    Mutation("import matches products without the tenant predicate",
             "demo/erp/importer.py",
             '                "SELECT id FROM products WHERE tenant_id=? AND sku=?",\n'
             '                (tenant_id, v["sku"])).fetchone()',
             '                "SELECT id FROM products WHERE sku=?",\n'
             '                (v["sku"],)).fetchone()',
             "python3 demo/verify_erp_demo.py"),
    Mutation("the 'already in the system' hint leaks other tenants' SKUs",
             "demo/erp/importer.py",
             '        "SELECT sku FROM products WHERE tenant_id=?", (tenant_id,))]',
             '        "SELECT sku FROM products", ())]',
             "python3 demo/verify_erp_demo.py"),

    # ── goods receiving ──
    Mutation("receiving overwrites cost instead of weighted-averaging",
             "demo/erp/receiving.py",
             "    return round((on_hand * old_cost + qty_in * new_cost) / total, 2)",
             "    return round(new_cost, 2)",
             "python3 demo/verify_erp_demo.py"),
    Mutation("receiving does not raise stock",
             "demo/erp/receiving.py",
             '                    "UPDATE inventory_levels SET on_hand=on_hand+?,"',
             '                    "UPDATE inventory_levels SET on_hand=on_hand+0*?,"',
             "python3 demo/verify_erp_demo.py"),
    Mutation("receiving accepts another tenant's product",
             "demo/erp/receiving.py",
             '            " WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()',
             '            " WHERE id=? AND is_active=1", (pid,)).fetchone()',
             "python3 demo/verify_erp_demo.py"),
    Mutation("rejected goods are counted as received",
             "demo/erp/receiving.py",
             "            value = round(r[\"accepted\"] * r[\"cost\"], 2)",
             "            value = round((r[\"accepted\"] + r[\"rejected\"]) * r[\"cost\"], 2)",
             "python3 demo/verify_erp_demo.py"),

    # ── credit notes ──
    Mutation("returns reverse GST at today's rate, not the invoice's",
             "demo/erp/returns.py",
             '        rate = Decimal(str(s["gst_rate"]))          # the ORIGINAL rate, never today\'s',
             '        rate = Decimal("5")',
             "python3 demo/verify_erp_demo.py"),
    Mutation("a garment can be returned more than once",
             "demo/erp/returns.py",
             '        if qty > src["returnable"] + 0.001:',
             "        if False:",
             "python3 demo/verify_erp_demo.py"),
    Mutation("written-off returns still go back on the shelf",
             "demo/erp/returns.py",
             '                         "restock": bool(raw.get("restock", True)),',
             '                         "restock": True,',
             "python3 demo/verify_erp_demo.py"),
    # ── EAN-13 labels ──
    Mutation("EAN-13 check digit weights are flipped",
             "demo/erp/labels.py",
             "    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))",
             "    total = sum(int(d) * (1 if i % 2 else 3) for i, d in enumerate(first12))",
             "python3 demo/verify_erp_demo.py"),
    Mutation("a bad check digit is printed as-is instead of corrected",
             "demo/erp/labels.py",
             '        return digits[:12] + want, f"check digit was {digits[12]}, corrected to {want}"',
             '        return digits, ""',
             "python3 demo/verify_erp_demo.py"),
    Mutation("labels ignore the tenant predicate",
             "demo/erp/labels.py",
             '            f" WHERE p.tenant_id=? AND p.id IN ({marks}) AND p.is_active=1"',
             '            f" WHERE p.id IN ({marks}) AND p.is_active=1"',
             "python3 demo/verify_erp_demo.py"),

    # ── GST exports ──
    Mutation("credit notes are added to outward supply instead of netted off",
             "demo/erp/gst_export.py",
             " nl.gst_rate, -nl.taxable_value, -nl.cgst, -nl.sgst, -nl.igst, NULL, -nl.quantity",
             " nl.gst_rate, nl.taxable_value, nl.cgst, nl.sgst, nl.igst, NULL, nl.quantity",
             "python3 demo/verify_erp_demo.py"),
    Mutation("GSTR-3B nets credit as one lump instead of head by head",
             "demo/erp/gst_export.py",
             '    payable = {h: round(max(0.0, outward[h] - inward[h]), 2)\n'
             '               for h in ("cgst", "sgst", "igst")}',
             '    _net = max(0.0, outward["tax"] - inward["tax"])\n'
             '    payable = {"cgst": round(_net, 2), "sgst": 0.0, "igst": 0.0}',
             "python3 demo/verify_erp_demo.py"),
    Mutation("input credit is read from the WRONG tenant",
             "demo/erp/gst_export.py",
             '        " WHERE bl.tenant_id=? AND b.status!=\'cancelled\'"',
             '        " WHERE bl.tenant_id!=? AND b.status!=\'cancelled\'"',
             "python3 demo/verify_erp_demo.py"),

    # ── stocktakes and transfers ──
    Mutation("a transfer adds to the destination without deducting the source",
             "demo/erp/inventory.py",
             '            conn.execute("UPDATE inventory_levels SET on_hand=on_hand-? WHERE id=? AND tenant_id=?", (r["quantity"], r["inv_from_id"], tenant_id))',
             '            pass',
             "python3 demo/verify_inventory_ops.py"),
    Mutation("a transfer writes only one ledger leg",
             "demo/erp/inventory.py",
             "                \"VALUES(?, ?, ?, 'transfer_in', ?, ?, 'transfer', ?, ?)\",",
             "                \"VALUES(?, ?, ?, 'transfer_out', ?, ?, 'transfer', ?, ?)\",",
             "python3 demo/verify_inventory_ops.py"),
    Mutation("a transfer can exceed what is on the shelf",
             "demo/erp/inventory.py",
             "        if on_hand_from < qty:",
             "        if False:",
             "python3 demo/verify_inventory_ops.py"),
    Mutation("a transfer accepts another tenant's product",
             "demo/erp/inventory.py",
             '        prod = conn.execute("SELECT id, name, sku FROM products WHERE id=? AND tenant_id=? AND is_active=1", (pid, tenant_id)).fetchone()',
             '        prod = conn.execute("SELECT id, name, sku FROM products WHERE id=? AND is_active=1", (pid,)).fetchone()',
             "python3 demo/verify_inventory_ops.py"),
    Mutation("a stocktake ledgers the COUNT instead of the variance",
             "demo/erp/inventory.py",
             '                (tenant_id, r["product_id"], location_id, r["variance"], now, st_id, f"st:{st_id}:{i}")',
             '                (tenant_id, r["product_id"], location_id, r["counted"], now, st_id, f"st:{st_id}:{i}")',
             "python3 demo/verify_inventory_ops.py"),
    Mutation("a stocktake does not actually move on-hand",
             "demo/erp/inventory.py",
             '                conn.execute("UPDATE inventory_levels SET on_hand=? WHERE id=? AND tenant_id=?", (r["counted"], r["inv_id"], tenant_id))',
             '                pass',
             "python3 demo/verify_inventory_ops.py"),
    Mutation("a stocktake accepts a negative count",
             "demo/erp/inventory.py",
             "        if counted < 0:",
             "        if False:",
             "python3 demo/verify_inventory_ops.py"),

    Mutation("returns accept another tenant's invoice",
             "demo/erp/returns.py",
             '        " WHERE i.tenant_id=? AND i.id=?", (tenant_id, invoice_id)).fetchone()',
             '        " WHERE i.id=?", (invoice_id,)).fetchone()',
             "python3 demo/verify_erp_demo.py"),
]

# Used by the field-order mutation above; injected into the module under test.
PRELUDE = "FIELD_ORDER = {f.key: i for i, f in enumerate(FIELDS)}\n"


def run(cmd: str, cwd: Path) -> int:
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                          text=True, timeout=600).returncode


def main() -> int:
    # `--only substring` runs a subset. The full suite is ~5 minutes, which is
    # longer than some CI step timeouts; chunking beats being killed mid-edit.
    only = ""
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1].lower()
    caught = missed = broken = 0
    print(f"{'MUTATION':<58} {'BASELINE':<10} MUTATED")
    print("-" * 84)

    for m in MUTATIONS:
        if only and only not in m.label.lower() and only not in m.path.lower():
            continue
        target = ROOT / m.path
        cwd = ROOT / m.cwd
        original = target.read_text()
        _ORIGINALS.setdefault(str(target), original)

        mutated, bad = original, None
        for find, replace in m.edits:
            if find not in mutated:
                bad = "search text not found"
                break
            mutated = mutated.replace(find, replace, 1)
        if "FIELD_ORDER" in m.replace and "FIELD_ORDER =" not in mutated:
            mutated = mutated.replace("REQUIRED = tuple(", PRELUDE + "REQUIRED = tuple(", 1)
        if bad is None and mutated == original:
            bad = "replacement changed nothing"
        if bad:
            print(f"{m.label:<58} {'—':<10} BROKEN: {bad}")
            broken += 1
            continue

        backup = Path(tempfile.mkdtemp()) / target.name
        shutil.copy2(target, backup)
        try:
            target.write_text(mutated)
            # Assert on disk, not in memory — this is the check whose absence
            # made the first version of this harness report a wall of green.
            assert target.read_text() == mutated, "mutation did not reach disk"
            rc = run(m.test, cwd)
        finally:
            shutil.copy2(backup, target)
            assert target.read_text() == original, "FAILED TO RESTORE " + m.path

        if rc != 0:
            print(f"{m.label:<58} {'pass':<10} caught (exit {rc})")
            caught += 1
        else:
            print(f"{m.label:<58} {'pass':<10} MISSED — tests still green")
            missed += 1

    print("-" * 84)
    total = caught + missed + broken
    print(f"{caught}/{total} mutations caught"
          + (f", {missed} MISSED" if missed else "")
          + (f", {broken} BROKEN" if broken else ""))
    if missed or broken:
        print("\nA missed mutation means a test asserts less than it appears to.")
        return 1
    print("\nEvery mutation was caught — the suites are load-bearing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
