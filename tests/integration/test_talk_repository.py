"""TalkRepository — active Talk lookup + creation helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.talk_repository import TalkRepository


async def _seed_tenant_lead_treeflow(
    db_session: AsyncSession,
) -> tuple[Tenant, Lead, TreeflowVersion]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml="y",
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    return tenant, lead, tfv


@pytest.mark.asyncio
async def test_find_active_for_lead_returns_active_talk(
    db_session: AsyncSession,
) -> None:
    tenant, lead, tfv = await _seed_tenant_lead_treeflow(db_session)
    repo = TalkRepository(db_session)
    assert await repo.find_active_for_lead(tenant.id, lead.id) is None

    talk = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="active", handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

    found = await repo.find_active_for_lead(tenant.id, lead.id)
    assert found is not None
    assert found.id == talk.id


@pytest.mark.asyncio
async def test_find_active_ignores_closed_talks(db_session: AsyncSession) -> None:
    tenant, lead, tfv = await _seed_tenant_lead_treeflow(db_session)
    closed = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="closed_completed",
        handling_mode="ai", last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(closed)
    await db_session.flush()

    repo = TalkRepository(db_session)
    assert await repo.find_active_for_lead(tenant.id, lead.id) is None


@pytest.mark.asyncio
async def test_create_talk_initializes_defaults(db_session: AsyncSession) -> None:
    tenant, lead, tfv = await _seed_tenant_lead_treeflow(db_session)
    repo = TalkRepository(db_session)
    talk = await repo.create(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id="tf",
        treeflow_version_id=tfv.id,
    )
    await db_session.flush()
    assert talk.status == "active"
    assert talk.handling_mode == "ai"
    assert talk.turn_count == 0
    assert talk.tokens_consumed == {}
