"""
Auth, role and tenant-isolation tests.

Fully executable here: PyJWT is installed and app.security.core imports no
framework and touches no database. That is the reason the logic was factored
out of the FastAPI layer.
"""
from __future__ import annotations

import os
import sys
import time
from decimal import Decimal

os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jwt  # noqa: E402
from app.security.core import (  # noqa: E402
    ApprovalDenied, AuthError, Principal, Role, TokenExpired,
    approval_limit, assert_same_tenant, can_approve, issue_token,
    require_roles, scope_query, verify_token,
)

FAILURES = []


def check(name, cond, extra=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {extra}")
        FAILURES.append(name)


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception:
        return False
    return False


def test_roundtrip():
    t = issue_token("alice@erp", "tenant-a", ["buyer"])
    p = verify_token(t)
    check("issued token verifies", p.subject == "alice@erp")
    check("tenant claim survives the round trip", p.tenant_id == "tenant-a")
    check("roles survive the round trip", p.has_role(Role.BUYER))
    check("bearer prefix is tolerated", verify_token(f"Bearer {t}").subject == "alice@erp")


def test_rejects_tampering():
    t = issue_token("alice@erp", "tenant-a", ["buyer"])
    head, payload, sig = t.split(".")
    forged = f"{head}.{payload}.{'A' * len(sig)}"
    check("a forged signature is rejected", raises(AuthError, verify_token, forged))

    # The classic escalation: re-sign with a different key.
    body = jwt.decode(t, os.environ["JWT_SECRET"], algorithms=["HS256"])
    body["roles"] = ["admin"]
    evil = jwt.encode(body, "some-other-secret-that-is-long-enough-32", algorithm="HS256")
    check("a token signed with the wrong key is rejected",
          raises(AuthError, verify_token, evil))

    # alg=none downgrade
    none_tok = jwt.encode(body, key="", algorithm="none")
    check("an alg=none token is rejected", raises(AuthError, verify_token, none_tok))
    check("empty token is rejected", raises(AuthError, verify_token, ""))
    check("garbage is rejected", raises(AuthError, verify_token, "not.a.token"))


def test_expiry():
    past = int(time.time()) - 100_000
    t = issue_token("bob@erp", "tenant-a", ["buyer"], ttl_seconds=10, now=past)
    check("an expired token raises TokenExpired", raises(TokenExpired, verify_token, t))
    fresh = issue_token("bob@erp", "tenant-a", ["buyer"], ttl_seconds=3600)
    check("a fresh token is accepted", verify_token(fresh).subject == "bob@erp")


def test_missing_tenant_claim():
    body = {"sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 600}
    t = jwt.encode(body, os.environ["JWT_SECRET"], algorithm="HS256")
    check("a token with no tenant claim is rejected", raises(AuthError, verify_token, t))


def test_unknown_role_rejected_at_issue():
    check("issuing an unknown role fails loudly",
          raises(AuthError, issue_token, "x", "t", ["superuser"]))


def test_role_gating():
    buyer = verify_token(issue_token("b@erp", "t1", ["buyer"]))
    approver = verify_token(issue_token("a@erp", "t1", ["approver"]))
    admin = verify_token(issue_token("root@erp", "t1", ["admin"]))
    viewer = verify_token(issue_token("v@erp", "t1", ["viewer"]))

    check("buyer passes a buyer gate", require_roles(buyer, Role.BUYER) is None)
    check("buyer is blocked by an approver gate",
          raises(ApprovalDenied, require_roles, buyer, Role.APPROVER))
    check("approver passes an approver gate", require_roles(approver, Role.APPROVER) is None)
    check("admin passes every gate", require_roles(admin, Role.APPROVER) is None)
    check("viewer is blocked from buyer actions",
          raises(ApprovalDenied, require_roles, viewer, Role.BUYER))


def test_approval_threshold():
    buyer = verify_token(issue_token("b@erp", "t1", ["buyer"]))
    approver = verify_token(issue_token("a@erp", "t1", ["approver"]))
    viewer = verify_token(issue_token("v@erp", "t1", ["viewer"]))

    lim = approval_limit(Role.BUYER)
    check(f"buyer may approve at the limit ({lim:,})",
          can_approve(buyer, lim) is None)
    check("buyer is blocked one unit above the limit",
          raises(ApprovalDenied, can_approve, buyer, lim + 1))
    check("approver clears an amount the buyer cannot",
          can_approve(approver, lim + 1) is None)
    check("approver is blocked above the approver limit",
          raises(ApprovalDenied, can_approve, approver, approval_limit(Role.APPROVER) + 1))
    check("viewer cannot approve anything",
          raises(ApprovalDenied, can_approve, viewer, 1))
    check("negative PO value is rejected",
          raises(ApprovalDenied, can_approve, approver, -5))
    check("a buyer cannot self-approve a large order (the fraud path)",
          raises(ApprovalDenied, can_approve, buyer, Decimal("400000")))


def test_multi_role_takes_the_highest():
    p = verify_token(issue_token("m@erp", "t1", ["viewer", "buyer", "approver"]))
    check("max_role is the highest held", p.max_role is Role.APPROVER, p.max_role)
    check("limit follows the highest role",
          p.approval_limit == approval_limit(Role.APPROVER))


def test_tenant_isolation():
    a = verify_token(issue_token("a@erp", "tenant-a", ["buyer"]))
    b = verify_token(issue_token("b@erp", "tenant-b", ["buyer"]))
    check("tenants differ", a.tenant_id != b.tenant_id)

    class FakeCol:
        def __eq__(self, other): return ("eq", other)

    class Model:
        __name__ = "Recommendation"
        tenant_id = FakeCol()

    class Q:
        def __init__(self): self.filters = []
        def where(self, cond):
            self.filters.append(cond); return self

    qa = scope_query(Q(), Model, a)
    check("scope_query filters on the token tenant, not user input",
          qa.filters == [("eq", "tenant-a")], qa.filters)
    qb = scope_query(Q(), Model, b)
    check("a different principal produces a different filter",
          qb.filters == [("eq", "tenant-b")])

    class NoTenant:
        __name__ = "Product"

    check("a model without tenant_id is refused rather than served unfiltered",
          raises(RuntimeError, scope_query, Q(), NoTenant, a))

    check("fetch-by-id cross-tenant read is blocked",
          raises(ApprovalDenied, assert_same_tenant, a, "tenant-b"))
    check("fetch-by-id same-tenant read is allowed",
          assert_same_tenant(a, "tenant-a") is None)


def test_secret_hygiene():
    orig = os.environ.get("JWT_SECRET")
    try:
        os.environ["JWT_SECRET"] = ""
        check("an unset secret fails closed rather than defaulting",
              raises(AuthError, issue_token, "x", "t", ["buyer"]))
        os.environ["JWT_SECRET"] = "short"
        check("a short secret is rejected",
              raises(AuthError, issue_token, "x", "t", ["buyer"]))
    finally:
        if orig is not None:
            os.environ["JWT_SECRET"] = orig


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nAuth / role / tenant tests ({len(tests)} groups)")
    print("=" * 62)
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "=" * 62)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
