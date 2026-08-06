"""FastAPI wiring for app.security.core. Contains no authorisation logic itself."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from .core import ApprovalDenied, AuthError, Principal, Role, TokenExpired, can_approve, require_roles, verify_token


async def current_principal(
    authorization: Optional[str] = Header(default=None),
) -> Principal:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_token(authorization)
    except TokenExpired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc),
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}) from exc
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc),
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def requires(*roles: Role):
    """Route dependency: `dependencies=[Depends(requires(Role.APPROVER))]`."""
    async def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        try:
            require_roles(principal, *roles)
        except ApprovalDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        return principal
    return _dep


def assert_can_approve(principal: Principal, value: Decimal) -> None:
    try:
        can_approve(principal, value)
    except ApprovalDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
