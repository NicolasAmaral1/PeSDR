"""Helper to scope a connection/session to a tenant via Postgres RLS."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """SET LOCAL app.current_tenant for the current transaction.

    Must be called at the start of every request that touches tenant-scoped tables.
    LOCAL scope ties the setting to the current transaction, so it does not leak
    across pooled connections.
    """
    await session.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": str(tenant_id)})
