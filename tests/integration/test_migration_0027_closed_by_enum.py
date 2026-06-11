"""Migration 0027 extends talks.closed_by CHECK constraint (FE-03b hotfix)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.models.talk_closed_by import ALL_CLOSED_BY
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_tenant_lead_tfv(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
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
    return tenant_id, lead_id, tfv_id


@pytest.mark.asyncio
async def test_closed_by_check_constraint_accepts_valid(
    db_session: AsyncSession,
) -> None:
    """INSERTing each documented closed_by value succeeds."""
    tenant_id, lead_id, tfv_id = await _seed_tenant_lead_tfv(db_session)
    for v in ALL_CLOSED_BY:
        sp = await db_session.begin_nested()
        await db_session.execute(
            text(
                "INSERT INTO talks "
                "(tenant_id, lead_id, treeflow_id, treeflow_version_id, "
                " status, handling_mode, last_message_at, closed_by) "
                "VALUES "
                "(:tid, :lid, 'tf', :tfv, "
                " 'closed_completed_success', 'ai', now(), :cb)"
            ),
            {"tid": tenant_id, "lid": lead_id, "tfv": tfv_id, "cb": v},
        )
        await sp.rollback()


@pytest.mark.asyncio
async def test_closed_by_check_constraint_rejects_invalid(
    db_session: AsyncSession,
) -> None:
    """INSERTing a value NOT in the enum raises a CHECK violation."""
    tenant_id, lead_id, tfv_id = await _seed_tenant_lead_tfv(db_session)
    sp = await db_session.begin_nested()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO talks "
                "(tenant_id, lead_id, treeflow_id, treeflow_version_id, "
                " status, handling_mode, last_message_at, closed_by) "
                "VALUES "
                "(:tid, :lid, 'tf', :tfv, "
                " 'closed_completed_success', 'ai', now(), 'totally_bogus_closer')"
            ),
            {"tid": tenant_id, "lid": lead_id, "tfv": tfv_id},
        )
    if sp.is_active:
        await sp.rollback()


@pytest.mark.asyncio
async def test_pg_constraint_registered(async_engine: AsyncEngine) -> None:
    """The named constraint exists in pg_constraint."""
    async with async_engine.connect() as conn:
        r = await conn.execute(
            text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'ck_talks_closed_by' AND contype = 'c'"
            )
        )
        assert r.scalar() == 1
