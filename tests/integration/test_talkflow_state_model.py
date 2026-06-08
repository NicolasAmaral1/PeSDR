"""TalkFlowState model wraps the JSONB-heavy state row."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


@pytest.mark.asyncio
async def test_talkflow_state_round_trip(db_session: AsyncSession) -> None:
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
    talk = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="active", handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

    state = TalkFlowState(
        talk_id=talk.id,
        tenant_id=tenant.id,
        current_node="saudacao",
        collected={"segmento": "saas"},
        extracted_facts={"tem_filha": True},
        messages=[
            {"role": "user", "content": "oi", "source": "lead", "turn_index": 1,
             "timestamp": "2026-06-02T10:00:00+00:00"}
        ],
        objections_handled=[],
        talkflow_stack=[],
    )
    db_session.add(state)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(TalkFlowState).where(TalkFlowState.talk_id == talk.id)
        )
    ).scalar_one()
    assert fetched.current_node == "saudacao"
    assert fetched.collected == {"segmento": "saas"}
    assert fetched.extracted_facts == {"tem_filha": True}
    assert fetched.messages[0]["content"] == "oi"
    assert fetched.active_treatment is None
