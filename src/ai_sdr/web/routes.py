"""Console HTML routes — /console/{slug}/leads + HTMX partial endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.auth import require_tenant_access
from ai_sdr.web.deps import templates

router = APIRouter()


async def _tenants_visible_to(user: User, db: AsyncSession) -> list[Tenant]:
    if user.is_platform_admin:
        rows = (await db.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
        return list(rows)
    rows = (
        (
            await db.execute(
                select(Tenant)
                .join(UserTenantAccess, UserTenantAccess.tenant_id == Tenant.id)
                .where(UserTenantAccess.user_id == user.id)
                .order_by(Tenant.slug)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/console/{tenant_slug}/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    tenant, user = access
    tenants_available = await _tenants_visible_to(user, db)
    return templates.TemplateResponse(
        request,
        "leads_list.html",
        {
            "current_tenant": tenant,
            "current_user": user,
            "tenants_available": tenants_available,
        },
    )
