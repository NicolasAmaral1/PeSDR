"""Helper to scope a connection/session to a tenant via Postgres RLS."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Set app.current_tenant for the current transaction (local scope).

    Must be called at the start of every transaction that touches tenant-scoped
    tables. We use ``set_config('app.current_tenant', :tid, true)`` (third arg
    = ``is_local``) instead of ``SET LOCAL`` because asyncpg does not accept
    parameter binding on the SET statement, while ``set_config`` is a regular
    function call and binds normally.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
