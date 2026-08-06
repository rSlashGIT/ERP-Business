"""
Authentication, roles and tenant isolation — pure logic, no framework.

DELIBERATELY FRAMEWORK-FREE. Everything here is testable without FastAPI, a
database or a running server, because authorisation bugs are the ones you
cannot afford to discover in integration. The FastAPI wiring lives in
app/security/deps.py and does nothing except call into this module.

THREAT MODEL THIS ADDRESSES
---------------------------
1. Unauthenticated access            -> signature-verified bearer tokens
2. Privilege escalation              -> role claims checked per endpoint
3. Self-approval of large spend      -> approval threshold by role
4. Cross-tenant data access          -> tenant_id is taken from the TOKEN,
                                        never from the request body or query
5. Token replay after role change    -> short TTL + issued-at check

POINT 4 IS THE ONE THAT ACTUALLY LEAKS DATA. Every multi-tenant breach in this
shape comes from trusting a tenant_id the client supplied. `Principal.tenant_id`
is read from the signed token and `scope_query` is the only sanctioned way to
filter -- if a query does not go through it, it is not tenant-safe.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence

import jwt  # PyJWT

ALGORITHM = "HS256"
DEFAULT_TTL_SECONDS = 8 * 3600
LEEWAY_SECONDS = 30


class AuthError(Exception):
    """401 — the caller is not who they claim to be."""


class TokenExpired(AuthError):
    """401 — valid signature, expired token."""


class ApprovalDenied(Exception):
    """403 — authenticated, but not permitted to do this."""


class Role(str, Enum):
    VIEWER = "viewer"      # read-only
    BUYER = "buyer"        # may stage decisions and approve small POs
    APPROVER = "approver"  # may approve any PO up to the global cap
    ADMIN = "admin"        # everything, including policy changes

    @property
    def rank(self) -> int:
        return {"viewer": 0, "buyer": 1, "approver": 2, "admin": 3}[self.value]


def _secret() -> str:
    s = os.getenv("JWT_SECRET", "")
    if not s:
        # Failing closed is the only safe default. A dev-friendly fallback
        # secret is how a signing key ends up in production.
        raise AuthError("JWT_SECRET is not configured; refusing to sign or verify")
    if len(s) < 32:
        raise AuthError("JWT_SECRET must be at least 32 characters")
    return s


def approval_limit(role: Role) -> Decimal:
    """Maximum PO value this role may approve. Env-overridable per deployment."""
    defaults = {
        Role.VIEWER: "0",
        Role.BUYER: os.getenv("APPROVAL_LIMIT_BUYER", "10000"),
        Role.APPROVER: os.getenv("APPROVAL_LIMIT_APPROVER", "250000"),
        Role.ADMIN: os.getenv("APPROVAL_LIMIT_ADMIN", "100000000"),
    }
    return Decimal(str(defaults[role]))


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Everything here came from a signed token."""

    subject: str
    tenant_id: str
    roles: frozenset = field(default_factory=frozenset)
    issued_at: int = 0
    expires_at: int = 0
    token_id: str = ""

    def has_role(self, role: Role) -> bool:
        return role.value in self.roles

    @property
    def max_role(self) -> Role:
        best = Role.VIEWER
        for r in self.roles:
            try:
                cand = Role(r)
            except ValueError:
                continue
            if cand.rank > best.rank:
                best = cand
        return best

    @property
    def approval_limit(self) -> Decimal:
        return approval_limit(self.max_role)


def issue_token(
    subject: str,
    tenant_id: str,
    roles: Sequence[str],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[int] = None,
) -> str:
    if not subject or not tenant_id:
        raise AuthError("subject and tenant_id are required")
    bad = [r for r in roles if r not in {x.value for x in Role}]
    if bad:
        raise AuthError(f"unknown role(s): {bad}")
    ts = int(now if now is not None else time.time())
    payload = {
        "sub": subject,
        "tid": tenant_id,
        "roles": sorted(set(roles)),
        "iat": ts,
        "exp": ts + int(ttl_seconds),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def verify_token(token: str, now: Optional[int] = None) -> Principal:
    if not token:
        raise AuthError("missing bearer token")
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        payload = jwt.decode(
            token,
            _secret(),
            # Pinning the algorithm list is what blocks the `alg: none` and
            # HS/RS confusion attacks. Never pass algorithms=None here.
            algorithms=[ALGORITHM],
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    tenant = payload.get("tid")
    if not tenant:
        raise AuthError("token carries no tenant claim")
    return Principal(
        subject=str(payload["sub"]),
        tenant_id=str(tenant),
        roles=frozenset(payload.get("roles") or []),
        issued_at=int(payload.get("iat", 0)),
        expires_at=int(payload.get("exp", 0)),
        token_id=str(payload.get("jti", "")),
    )


def require_roles(principal: Principal, *allowed: Role) -> None:
    """Raise ApprovalDenied unless the principal holds one of `allowed`."""
    if not allowed:
        return
    if principal.has_role(Role.ADMIN):
        return
    if not any(principal.has_role(r) for r in allowed):
        raise ApprovalDenied(
            f"requires one of {[r.value for r in allowed]}; "
            f"caller has {sorted(principal.roles) or 'no roles'}"
        )


def can_approve(principal: Principal, po_value: Decimal | float | int) -> None:
    """Enforce the value threshold. Raises ApprovalDenied with the numbers.

    A buyer approving their own 400k order is the single most common
    procurement-fraud pattern, so this check is value-based rather than a plain
    role gate: a buyer can clear routine replenishment without a bottleneck,
    and anything material escalates.
    """
    value = Decimal(str(po_value))
    if value < 0:
        raise ApprovalDenied("purchase order value cannot be negative")
    require_roles(principal, Role.BUYER, Role.APPROVER)
    limit = principal.approval_limit
    if value > limit:
        raise ApprovalDenied(
            f"{principal.subject} ({principal.max_role.value}) may approve up to "
            f"{limit:,}; this order is {value:,}. Escalate to an approver."
        )


def scope_query(query: Any, model: Any, principal: Principal) -> Any:
    """Apply the tenant filter to a SQLAlchemy query.

    THE ONLY sanctioned way to filter by tenant. The value comes from the
    signed token, never from user input. Models without a tenant_id column are
    rejected loudly rather than silently returned unfiltered — a silent pass
    here is a cross-tenant leak.
    """
    if not hasattr(model, "tenant_id"):
        raise RuntimeError(
            f"{getattr(model, '__name__', model)} has no tenant_id column; "
            "it cannot be served through a tenant-scoped endpoint"
        )
    return query.where(model.tenant_id == principal.tenant_id)


def assert_same_tenant(principal: Principal, record_tenant_id: str) -> None:
    """Guard for a record fetched by primary key, where no filter applied.

    Fetch-by-id is the classic tenant-isolation hole: the id is a UUID so it
    "cannot be guessed", right up until it appears in a log or a shared URL.
    """
    if str(record_tenant_id) != principal.tenant_id:
        raise ApprovalDenied("record belongs to another tenant")
