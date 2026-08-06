#!/usr/bin/env python3
"""
Audit: tenant scoping of every DB-reachable query in every router.

THE DEFECT CLASS
----------------
A `session.execute()` whose statement was never passed through `scope_query()`
returns every tenant's rows. `GET /api/v1/inventory` shipped that way and
returned all tenants' stock to any caller.

WHY A SCRIPT AND NOT A CHECKLIST
--------------------------------
The same reason as scripts/audit_uniqueness.py: a named list only covers what
someone remembered to name. This walks every function in every router and
classifies every query, so a new route or a new helper is covered the moment it
is written.

CLASSIFICATION
--------------
  DIRECT      the statement contains a literal scope_query(...) call
  TRANSITIVE  the statement derives from a value that was itself scoped --
              e.g. `stmt = scope_query(...)` then `session.execute(stmt)`, or
              filtering on a run_id that was fetched through a scoped query
  PRINCIPAL   filtered on a principal-derived value (principal.tenant_id etc.)
  UNSCOPED    none of the above -- a defect

Dataflow uses real ast.Name resolution, NOT substring matching on variable
names. Substring matching is unsound: a variable called `r` matches almost any
expression and silently launders an unscoped query into a pass.

FETCH-BY-ID
-----------
A query filtered on `Model.id == <request value>` with no tenant scoping is
reported separately. Being a UUID is not an access control; ids leak through
logs, URLs and exports. Either scope the query or call assert_same_tenant on
the result.

Run: python3 scripts/audit_route_scoping.py     (exit 1 on any defect)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROUTERS = Path(__file__).resolve().parents[1] / "services/erp-api/app/api/v1"

SCOPE_FN = "scope_query"
ASSERT_FN = "assert_same_tenant"
PRINCIPAL_ROOTS = {"principal"}


def _names(node: ast.AST) -> Set[str]:
    """Every bare Name referenced in an expression. Precise, unlike substrings."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _calls(node: ast.AST, fn: str) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == fn) or \
               (isinstance(f, ast.Attribute) and f.attr == fn):
                return True
    return False


class FunctionAudit:
    """Resolve which local names carry a tenant-scoped value."""

    def __init__(self, fn: ast.AST, module_scoped: Set[str]) -> None:
        self.fn = fn
        self.tainted: Set[str] = set(module_scoped)
        self.params: Set[str] = set()
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        self._propagate()

    def _propagate(self) -> None:
        """Fixed-point: a name is scoped if assigned from a scoped expression."""
        for _ in range(6):                       # converges well before this
            before = len(self.tainted)
            for node in ast.walk(self.fn):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                if value is None:
                    continue
                targets = (node.targets if isinstance(node, ast.Assign) else [node.target])
                if not self._is_scoped_expr(value):
                    continue
                for t in targets:
                    if isinstance(t, ast.Name):
                        self.tainted.add(t.id)
            if len(self.tainted) == before:
                break

    def _is_scoped_expr(self, node: ast.AST) -> bool:
        if _calls(node, SCOPE_FN):
            return True
        used = _names(node)
        if used & self.tainted:
            return True
        # principal.tenant_id / principal.subject used as a filter value
        for n in ast.walk(node):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
               and n.value.id in PRINCIPAL_ROOTS:
                return True
        return False

    def classify(self, stmt_expr: ast.AST) -> str:
        if _calls(stmt_expr, SCOPE_FN):
            return "DIRECT"
        for n in ast.walk(stmt_expr):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
               and n.value.id in PRINCIPAL_ROOTS:
                return "PRINCIPAL"
        if _names(stmt_expr) & self.tainted:
            return "TRANSITIVE"
        return "UNSCOPED"

    def is_fetch_by_id(self, stmt_expr: ast.AST) -> bool:
        """Filtered on <Model>.id == <something from the request>."""
        for n in ast.walk(stmt_expr):
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Attribute) \
               and n.left.attr == "id" and n.ops and isinstance(n.ops[0], ast.Eq):
                rhs = n.comparators[0]
                if _names(rhs) & (self.params - PRINCIPAL_ROOTS):
                    return True
        return False


def audit_module(path: Path) -> Tuple[List[dict], List[dict]]:
    tree = ast.parse(path.read_text())
    findings: List[dict] = []
    routes: List[dict] = []

    module_scoped: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets and _calls(node.value, SCOPE_FN):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    module_scoped.add(t.id)

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decs = [ast.unparse(d) for d in fn.decorator_list]
        is_route = any("router." in d for d in decs)
        audit = FunctionAudit(fn, module_scoped)
        has_principal = "principal" in audit.params
        has_dep = any("current_principal" in d for d in
                      [ast.unparse(a.annotation) if a.annotation else "" for a in fn.args.args] +
                      [ast.unparse(fn.args.defaults[i]) if i < len(fn.args.defaults) else ""
                       for i in range(len(fn.args.defaults))])
        # UNDECLARED PRINCIPAL. A function that references `principal` without
        # taking it as a parameter is a NameError at runtime, and it silently
        # satisfies every scoping check that looks for the word. This is not
        # hypothetical: procurement.ai_variance shipped in exactly that state
        # -- a signature patch failed to match while the body patch succeeded,
        # so the route was reported as scoped and would have 500'd on first
        # call. Cheap to detect, so detect it.
        uses_principal = any(
            isinstance(n, ast.Name) and n.id in PRINCIPAL_ROOTS and isinstance(n.ctx, ast.Load)
            for n in ast.walk(fn)
        )
        if uses_principal and "principal" not in audit.params:
            findings.append({"file": path.name, "fn": fn.name,
                             "kind": "principal_undeclared",
                             "detail": "references `principal` but does not take it "
                                       "-> NameError at runtime"})

        queries = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
               and n.func.attr == "execute" and n.args:
                arg = n.args[0]
                cls = audit.classify(arg)
                fetch = audit.is_fetch_by_id(arg)
                guarded = _calls(fn, ASSERT_FN)
                src = ast.unparse(arg).replace("\n", " ")
                queries.append({"class": cls, "src": src, "fetch_by_id": fetch,
                                "assert_guarded": guarded})
                if cls == "UNSCOPED":
                    findings.append({"file": path.name, "fn": fn.name, "kind": "unscoped_query",
                                     "detail": src[:90]})
                elif fetch and cls != "DIRECT" and not guarded:
                    findings.append({"file": path.name, "fn": fn.name,
                                     "kind": "fetch_by_id_unguarded", "detail": src[:90]})
        if is_route:
            routes.append({"file": path.name, "name": fn.name, "principal": has_principal,
                           "queries": queries})
            if not has_principal:
                findings.append({"file": path.name, "fn": fn.name, "kind": "route_no_principal",
                                 "detail": "route takes no principal"})
        elif queries and not has_principal:
            # a DB-touching helper that cannot scope because it never receives
            # the caller -- reachable from a route, so it is in scope
            findings.append({"file": path.name, "fn": fn.name, "kind": "helper_no_principal",
                             "detail": f"{len(queries)} query(ies), no principal parameter"})
    return findings, routes


def main() -> int:
    files = sorted(p for p in ROUTERS.glob("*.py") if p.name != "__init__.py")
    all_findings: List[dict] = []
    all_routes: List[dict] = []
    for f in files:
        fnd, rts = audit_module(f)
        all_findings += fnd
        all_routes += rts

    print("=" * 78)
    print(" Route scoping audit — every DB-reachable query in every router")
    print("=" * 78)
    counts: Dict[str, int] = {}
    for r in all_routes:
        for q in r["queries"]:
            counts[q["class"]] = counts.get(q["class"], 0) + 1

    print(f"\n  {len(files)} router(s), {len(all_routes)} route(s), "
          f"{sum(len(r['queries']) for r in all_routes)} quer(ies) in routes")
    for k in ("DIRECT", "TRANSITIVE", "PRINCIPAL", "UNSCOPED"):
        if counts.get(k):
            print(f"    {k:<11} {counts[k]}")

    print(f"\n  {'route':<34}{'principal':<11}{'queries'}")
    print("  " + "-" * 74)
    for r in sorted(all_routes, key=lambda x: (x["file"], x["name"])):
        qs = ",".join(q["class"][:4] for q in r["queries"]) or "-"
        flag = "yes" if r["principal"] else "NO"
        print(f"  {r['file'][:-3] + '.' + r['name']:<34}{flag:<11}{qs}")

    print(f"\n  FINDINGS ({len(all_findings)})")
    if not all_findings:
        print("    none")
    for f in all_findings:
        print(f"    {f['kind']:<24} {f['file']}::{f['fn']}  {f['detail']}")

    print("\n" + "=" * 78)
    if all_findings:
        print(f" {len(all_findings)} ROUTE-SCOPING DEFECT(S)")
        return 1
    print(" NO ROUTE-SCOPING DEFECTS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
