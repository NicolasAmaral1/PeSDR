"""Bootstrap endpoint: who am I + which tenants can I access.

The SPA calls this first to discover its tenant slug(s); every other data
call is tenant-scoped at /api/console/tenants/{slug}/...
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.auth import require_console_user

router = APIRouter(prefix="/api/console")


class TenantBrief(BaseModel):
    slug: str
    display_name: str


class MeOut(BaseModel):
    user: dict
    tenants: list[TenantBrief]


@router.get("/me", response_model=MeOut)
async def get_me(
    user: Annotated[User, Depends(require_console_user)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> MeOut:
    if getattr(user, "is_platform_admin", False):
        rows = (await db.execute(select(Tenant))).scalars().all()
    else:
        rows = (
            await db.execute(
                select(Tenant)
                .join(UserTenantAccess, UserTenantAccess.tenant_id == Tenant.id)
                .where(UserTenantAccess.user_id == user.id)
            )
        ).scalars().all()
    return MeOut(
        user={"id": str(user.id), "username": user.username},
        tenants=[TenantBrief(slug=t.slug, display_name=t.display_name) for t in rows],
    )
