"""Migration 0028 creates action_executions with constraints + RLS (FE-03c Task 2)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.models.action_status import ALL_STATUSES
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_tenant_lead_tfv_talk(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create the parent rows action_executions FK on (tenants, talks)."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    talk_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}", "n": "t"},
    )
    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1.0', 'x', 'yaml')"
        ),
        {"i": tfv_id, "t": tenant_id},
    )
    await session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_id},
    )
    await session.execute(
        text(
            "INSERT INTO talks "
            "(id, tenant_id, lead_id, treeflow_id, treeflow_version_id, "
            " status, handling_mode, last_message_at) "
            "VALUES (:tid, :ten, :lid, 'tf', :tfv, 'active', 'ai', now())"
        ),
        {"tid": talk_id, "ten": tenant_id, "lid": lead_id, "tfv": tfv_id},
    )
    return tenant_id, talk_id, lead_id


@pytest.mark.asyncio
async def test_status_check_constraint_accepts_valid(
    db_session: AsyncSession,
) -> None:
    """INSERTing each documented status succeeds."""
    tenant_id, talk_id, _ = await _seed_tenant_lead_tfv_talk(db_session)
    for v in ALL_STATUSES:
        sp = await db_session.begin_nested()
        await db_session.execute(
            text(
                "INSERT INTO action_executions "
                "(tenant_id, talk_id, node_id, field, value_hash, "
                " adapter_name, handler, params_resolved, status) "
                "VALUES (:ten, :tid, 'n', 'f', 'h', 'a', 'h', '{}'::jsonb, :v)"
            ),
            {"ten": tenant_id, "tid": talk_id, "v": v},
        )
        await sp.rollback()


@pytest.mark.asyncio
async def test_status_check_constraint_rejects_invalid(
    db_session: AsyncSession,
) -> None:
    tenant_id, talk_id, _ = await _seed_tenant_lead_tfv_talk(db_session)
    sp = await db_session.begin_nested()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO action_executions "
                "(tenant_id, talk_id, node_id, field, value_hash, "
                " adapter_name, handler, params_resolved, status) "
                "VALUES (:ten, :tid, 'n', 'f', 'h', 'a', 'h', '{}'::jsonb, 'bogus')"
            ),
            {"ten": tenant_id, "tid": talk_id},
        )
    if sp.is_active:
        await sp.rollback()


@pytest.mark.asyncio
async def test_uniqueness_on_talk_field_value_hash(
    db_session: AsyncSession,
) -> None:
    """Second INSERT with same (talk_id, field, value_hash) violates UNIQUE."""
    tenant_id, talk_id, _ = await _seed_tenant_lead_tfv_talk(db_session)
    sp = await db_session.begin_nested()
    await db_session.execute(
        text(
            "INSERT INTO action_executions "
            "(tenant_id, talk_id, node_id, field, value_hash, "
            " adapter_name, handler, params_resolved, status) "
            "VALUES (:ten, :tid, 'n', 'demo_data', 'abc123', "
            " 'logging', 'schedule_event', '{}'::jsonb, 'pending')"
        ),
        {"ten": tenant_id, "tid": talk_id},
    )
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO action_executions "
                "(tenant_id, talk_id, node_id, field, value_hash, "
                " adapter_name, handler, params_resolved, status) "
                "VALUES (:ten, :tid, 'n', 'demo_data', 'abc123', "
                " 'logging', 'schedule_event', '{}'::jsonb, 'pending')"
            ),
            {"ten": tenant_id, "tid": talk_id},
        )
    if sp.is_active:
        await sp.rollback()


@pytest.mark.asyncio
async def test_pg_constraints_registered(async_engine: AsyncEngine) -> None:
    """CHECK constraint + UNIQUE constraint exist in pg_constraint."""
    async with async_engine.connect() as conn:
        check_row = await conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_action_executions_status' AND contype = 'c'"
            )
        )
        assert check_row.scalar() == 1
        uniq_row = await conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'uq_action_executions_dedup' AND contype = 'u'"
            )
        )
        assert uniq_row.scalar() == 1
