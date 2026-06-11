"""record_outbound_audit writes one row per turn, idempotent by (talk, turn)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.audit import record_outbound_audit
from ai_sdr.flowengine.sender import SendResult
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


async def _seed(db_session: AsyncSession) -> tuple[Talk, InboundMessageRow]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999")
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
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="oi",
        raw={"body": "oi"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound)
    await db_session.flush()
    return talk, inbound


@pytest.mark.asyncio
async def test_records_outbound_row_with_media_type_text(
    db_session: AsyncSession,
) -> None:
    talk, inbound = await _seed(db_session)
    await record_outbound_audit(
        db_session,
        talk=talk,
        inbound=inbound,
        response_text="oi! qual segmento?",
        turn_index=1,
        send_result=SendResult(external_id="ext-snd", status="sent", error_detail=None),
        provider="fake",
        sent_at=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
    )
    await db_session.flush()
    rows = (
        (
            await db_session.execute(
                select(OutboundMessage).where(OutboundMessage.talkflow_id == talk.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.media_type == "text"
    assert row.message_type == "text"
    assert row.body_text == "oi! qual segmento?"
    assert row.status == "sent"
    assert row.external_id == "ext-snd"
    assert row.triggered_by == "inbound"
    assert row.inbound_message_id == inbound.id


@pytest.mark.asyncio
async def test_duplicate_call_is_idempotent(db_session: AsyncSession) -> None:
    talk, inbound = await _seed(db_session)
    args = dict(
        talk=talk,
        inbound=inbound,
        response_text="oi",
        turn_index=1,
        send_result=SendResult(external_id="ext", status="sent", error_detail=None),
        provider="fake",
        sent_at=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
    )
    await record_outbound_audit(db_session, **args)
    await record_outbound_audit(db_session, **args)
    await db_session.flush()
    rows = (
        (
            await db_session.execute(
                select(OutboundMessage).where(OutboundMessage.talkflow_id == talk.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
