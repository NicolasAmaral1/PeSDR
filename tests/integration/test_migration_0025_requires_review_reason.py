"""Migration 0025 adds talks.requires_review_reason (FE-03a Task 3)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.models.review_reason import ALL_REASONS
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
    """Create parent rows the talks INSERT depends on. Sets tenant context for RLS."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}", "n": "t"},
    )
    # RLS on talks/leads/treeflow_versions requires app.current_tenant set
    # for the connection before we INSERT child rows.
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
async def test_requires_review_reason_column_exists(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as conn:

        def _cols(sync_conn):
            insp = inspect(sync_conn)
            return {c["name"]: c for c in insp.get_columns("talks")}

        cols = await conn.run_sync(_cols)
    assert "requires_review_reason" in cols
    col = cols["requires_review_reason"]
    assert col["nullable"] is True
    # String column (VARCHAR or TEXT-ish)
    assert "VARCHAR" in str(col["type"]).upper() or "TEXT" in str(col["type"]).upper()


@pytest.mark.asyncio
async def test_requires_review_reason_check_constraint_accepts_valid(
    db_session: AsyncSession,
) -> None:
    """INSERTing each documented enum value succeeds (constraint allows them)."""
    tenant_id, lead_id, tfv_id = await _seed_tenant_lead_tfv(db_session)
    for v in ALL_REASONS:
        # SAVEPOINT-style: open + rollback so we leave no side effects.
        sp = await db_session.begin_nested()
        await db_session.execute(
            text(
                "INSERT INTO talks "
                "(tenant_id, lead_id, treeflow_id, treeflow_version_id, "
                " status, handling_mode, last_message_at, "
                " requires_review_reason) "
                "VALUES "
                "(:tid, :lid, 'tf', :tfv, "
                " 'requires_review', 'ai', now(), :v)"
            ),
            {"tid": tenant_id, "lid": lead_id, "tfv": tfv_id, "v": v},
        )
        await sp.rollback()


@pytest.mark.asyncio
async def test_requires_review_reason_check_constraint_rejects_invalid(
    db_session: AsyncSession,
) -> None:
    """INSERTing a value NOT in the enum raises a CHECK violation."""
    tenant_id, lead_id, tfv_id = await _seed_tenant_lead_tfv(db_session)
    # Open a SAVEPOINT so the IntegrityError doesn't poison the outer
    # transaction; SQLAlchemy auto-rolls-back the nested tx on exception.
    sp = await db_session.begin_nested()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO talks "
                "(tenant_id, lead_id, treeflow_id, treeflow_version_id, "
                " status, handling_mode, last_message_at, "
                " requires_review_reason) "
                "VALUES "
                "(:tid, :lid, 'tf', :tfv, "
                " 'requires_review', 'ai', now(), 'totally_bogus_reason')"
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
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_talks_requires_review_reason' "
                "AND contype = 'c'"
            )
        )
        assert r.scalar() == 1
