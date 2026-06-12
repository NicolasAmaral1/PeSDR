"""RLS isolation on action_executions (FE-03c Task 14)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_reads(db_session: AsyncSession) -> None:
    """Tenant A inserts a row; tenant B reading sees zero rows."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    for tid in (tenant_a, tenant_b):
        await db_session.execute(
            text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
            {"i": tid, "s": f"t-{tid.hex[:8]}", "n": "t"},
        )

    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    talk_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1', 'x', 'y')"
        ),
        {"i": tfv_id, "t": tenant_a},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_a},
    )
    await db_session.execute(
        text(
            "INSERT INTO talks (id, tenant_id, lead_id, treeflow_id, "
            " treeflow_version_id, status, handling_mode, last_message_at) "
            "VALUES (:tid, :ten, :lid, 'tf', :tfv, 'active', 'ai', now())"
        ),
        {"tid": talk_id, "ten": tenant_a, "lid": lead_id, "tfv": tfv_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO action_executions "
            "(tenant_id, talk_id, node_id, field, value_hash, "
            " adapter_name, handler, params_resolved, status) "
            "VALUES (:ten, :tid, 'n', 'f', 'h', 'logging', 'x', '{}'::jsonb, 'pending')"
        ),
        {"ten": tenant_a, "tid": talk_id},
    )

    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_b)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM action_executions"))
    assert result.scalar() == 0

    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM action_executions"))
    assert result.scalar() == 1
