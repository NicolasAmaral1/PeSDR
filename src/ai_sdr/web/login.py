"""Login + logout handlers (GET form / POST submit / GET logout)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, TypedDict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.settings import get_settings
from ai_sdr.web.auth import sign_session_cookie
from ai_sdr.web.deps import templates
from ai_sdr.web.passwords import verify_password

router = APIRouter()

_COOKIE_NAME = "pesdr_session"
_COOKIE_MAX_AGE = 12 * 60 * 60  # 12h


class _CookieKwargs(TypedDict):
    httponly: bool
    samesite: Literal["lax", "strict", "none"]
    secure: bool
    path: str


def _cookie_kwargs() -> _CookieKwargs:
    """Cookie flags consistent across set/clear."""
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": get_settings().app_env != "development",
        "path": "/",  # app-wide: the SPA at /inbox calls /api/console/* with the cookie
    }


@router.get("/console/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/console/login")
async def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> object:
    """Verify credentials → sign cookie → 302 to first accessible tenant."""
    # Case-insensitive username lookup (matches the unique index from migration 0009).
    user = (
        await db.execute(select(User).where(func.lower(User.username) == username.lower()))
    ).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        # Uniform error path (timing-attack safe up to bcrypt's natural variance).
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Usuário ou senha incorretos"},
            status_code=401,
        )

    user.last_login_at = datetime.now(UTC)
    await db.commit()

    # Resolve where to redirect.
    if user.is_platform_admin:
        # Pick any tenant for landing; the header dropdown lets them switch.
        first_tenant = (
            await db.execute(select(Tenant).order_by(Tenant.slug).limit(1))
        ).scalar_one_or_none()
    else:
        first_tenant = (
            await db.execute(
                select(Tenant)
                .join(UserTenantAccess, UserTenantAccess.tenant_id == Tenant.id)
                .where(UserTenantAccess.user_id == user.id)
                .order_by(Tenant.slug)
                .limit(1)
            )
        ).scalar_one_or_none()

    if first_tenant is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Sem acesso a nenhum tenant. Contate o administrador."},
            status_code=403,
        )

    cookie_value = sign_session_cookie(user.id)
    response = RedirectResponse(
        url=f"/console/{first_tenant.slug}/leads",
        status_code=303,
    )
    response.set_cookie(
        _COOKIE_NAME,
        cookie_value,
        max_age=_COOKIE_MAX_AGE,
        **_cookie_kwargs(),
    )
    return response


@router.get("/console/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/console/login", status_code=303)
    response.delete_cookie(_COOKIE_NAME, **_cookie_kwargs())
    return response
