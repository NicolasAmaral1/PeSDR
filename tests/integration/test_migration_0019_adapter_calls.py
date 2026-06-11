"""Verifies migration 0019 creates adapter_calls table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_adapter_calls_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'adapter_calls' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id",
        "tenant_id",
        "talk_id",
        "lead_id",
        "adapter_category",
        "adapter_provider",
        "operation",
        "args",
        "result",
        "status",
        "error_detail",
        "latency_ms",
        "idempotency_key",
        "started_at",
        "completed_at",
        "created_at",
    }


@pytest.mark.asyncio
async def test_adapter_calls_idempotency_key_unique(db_session: AsyncSession) -> None:
    """Same idempotency_key in same tenant cannot be inserted twice."""
    import uuid
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    insert_sql = text(
        "INSERT INTO adapter_calls (tenant_id, adapter_category, "
        "adapter_provider, operation, args, status, idempotency_key, started_at) "
        "VALUES (:t, 'crm', 'kommo', 'create_lead', CAST('{}' AS JSONB), 'ok', "
        ":k, :s)"
    )
    await db_session.execute(
        insert_sql,
        {"t": tenant_id, "k": "abc", "s": datetime.now(timezone.utc)},
    )
    with pytest.raises(Exception):
        await db_session.execute(
            insert_sql,
            {"t": tenant_id, "k": "abc", "s": datetime.now(timezone.utc)},
        )
    await db_session.rollback()
