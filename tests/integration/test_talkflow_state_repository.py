"""TalkFlowStateRepository — load + initialize + append_message."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.state import Message
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository


async def _seed_talk(db_session: AsyncSession) -> Talk:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="tf",
        version="1",
        content_hash="x",
        content_yaml="y",
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    talk = Talk(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id="tf",
        treeflow_version_id=tfv.id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()
    return talk


@pytest.mark.asyncio
async def test_load_returns_none_before_init(db_session: AsyncSession) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    assert await repo.load(talk.id) is None


@pytest.mark.asyncio
async def test_initialize_creates_default_state(db_session: AsyncSession) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    state = await repo.initialize(talk_id=talk.id, tenant_id=talk.tenant_id, entry_node="saudacao")
    await db_session.flush()
    assert state.current_node == "saudacao"
    assert state.collected == {}
    assert state.messages == []
    assert state.objections_handled == []
    assert state.active_treatment is None


@pytest.mark.asyncio
async def test_append_message_grows_rolling_window(
    db_session: AsyncSession,
) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    state = await repo.initialize(talk_id=talk.id, tenant_id=talk.tenant_id, entry_node="saudacao")
    await db_session.flush()

    m = Message(
        role="user",
        content="oi",
        source="lead",
        turn_index=1,
        timestamp=datetime.now(timezone.utc),
    )
    await repo.append_message(state, m, max_window=15)
    await db_session.flush()
    assert len(state.messages) == 1
    assert state.messages[0]["content"] == "oi"


@pytest.mark.asyncio
async def test_append_message_evicts_when_window_exceeded(
    db_session: AsyncSession,
) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    state = await repo.initialize(talk_id=talk.id, tenant_id=talk.tenant_id, entry_node="saudacao")
    await db_session.flush()

    for i in range(1, 18):
        m = Message(
            role="user",
            content=f"msg-{i}",
            source="lead",
            turn_index=i,
            timestamp=datetime.now(timezone.utc),
        )
        await repo.append_message(state, m, max_window=15)
    await db_session.flush()
    assert len(state.messages) == 15
    assert state.messages[0]["content"] == "msg-3"  # oldest two evicted
    assert state.messages[-1]["content"] == "msg-17"
