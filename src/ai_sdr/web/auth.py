"""Console auth — cookie signing + FastAPI deps.

This module is the auth boundary of the console. Two responsibilities:

1. **Cookie signing**: sign + verify session cookies with
   `itsdangerous.URLSafeTimedSerializer`. The cookie payload is a tiny
   dict {"user_id": "<uuid-str>"}; expiration is enforced by `max_age`
   on verify (caller passes the configured window).

2. **FastAPI deps** (Task 13): `require_console_user`, `require_tenant_access`.

The serializer is constructed lazily per call (not cached) so test
monkeypatching of settings.console_secret_key takes effect. Production
overhead is negligible — URLSafeTimedSerializer instantiation is cheap.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.web.deps import tenant_loader_dep

_SALT = "pesdr-console-v1"


def _serializer() -> URLSafeTimedSerializer:
    secret = get_settings().console_secret_key
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "CONSOLE_SECRET_KEY must be set (32+ chars). Startup validator "
            "should have caught this — check main.py lifespan."
        )
    return URLSafeTimedSerializer(secret, salt=_SALT)


def sign_session_cookie(user_id: uuid.UUID) -> str:
    """Return a signed cookie value carrying `user_id`."""
    return _serializer().dumps({"user_id": str(user_id)})


def verify_session_cookie(cookie_value: str, *, max_age_seconds: int) -> dict[str, str] | None:
    """Return the payload if signature is valid and not expired; else None.

    None covers: signature mismatch, expired, malformed input, empty
    string. Caller treats every None case as "log them out".
    """
    if not cookie_value:
        return None
    try:
        return _serializer().loads(cookie_value, max_age=max_age_seconds)  # type: ignore[no-any-return]
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# FastAPI deps (Task 13)
# ---------------------------------------------------------------------------

_COOKIE_MAX_AGE_SECONDS = 12 * 60 * 60  # 12h, matches login.py


def _redirect_to_login() -> HTTPException:
    return HTTPException(status_code=303, headers={"Location": "/console/login"})


async def require_console_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
) -> User:
    """Resolve cookie → User. Redirect to /console/login on any failure."""
    cookie = request.cookies.get("pesdr_session")
    payload = verify_session_cookie(cookie or "", max_age_seconds=_COOKIE_MAX_AGE_SECONDS)
    if payload is None:
        raise _redirect_to_login()
    try:
        user_id = uuid.UUID(payload["user_id"])
    except (KeyError, ValueError):
        raise _redirect_to_login() from None
    user = await db.get(User, user_id)
    if user is None:
        raise _redirect_to_login()
    return user


async def require_tenant_access(
    tenant_slug: str,
    user: Annotated[User, Depends(require_console_user)],
    db: Annotated[AsyncSession, Depends(db_session)],
    tenants: Annotated[TenantLoader, Depends(tenant_loader_dep)],
) -> tuple[Tenant, User]:
    """Resolve tenant from URL + verify user can access it + set RLS context."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {tenant_slug!r} not found")

    cfg = tenants.load(tenant_slug)
    if cfg.console is None or not cfg.console.enabled:
        raise HTTPException(status_code=404, detail="console disabled for this tenant")

    if not user.is_platform_admin:
        granted = (
            await db.execute(
                select(UserTenantAccess).where(
                    UserTenantAccess.user_id == user.id,
                    UserTenantAccess.tenant_id == tenant.id,
                )
            )
        ).scalar_one_or_none()
        if granted is None:
            raise HTTPException(status_code=403, detail="no access to this tenant")

    await set_tenant_context(db, tenant.id)
    return tenant, user
