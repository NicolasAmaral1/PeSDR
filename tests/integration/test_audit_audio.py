"""record_outbound_audit persists audio metadata columns."""

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

pytestmark = pytest.mark.integration


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
async def test_audit_persists_audio_fields(db_session: AsyncSession) -> None:
    talk, inbound = await _seed(db_session)
    await record_outbound_audit(
        db_session,
        talk=talk,
        inbound=inbound,
        response_text="bom dia",
        turn_index=1,
        send_result=SendResult(external_id="wamid.AUD", status="sent"),
        provider="whatsapp_cloud",
        sent_at=inbound.received_at,
        media_type="audio",
        audio_url="https://minio.local/outbound/x.ogg",
        media_storage_key="outbound/x.ogg",
        synthesis_voice_id="v1",
        voice_emotion="calm",
        audio_duration_ms=4200,
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(OutboundMessage).where(OutboundMessage.external_id == "wamid.AUD")
        )
    ).scalar_one()
    assert row.media_type == "audio"
    assert row.message_type == "audio"
    assert row.synthesis_voice_id == "v1"
    assert row.audio_duration_ms == 4200
    assert row.audio_url.endswith("/outbound/x.ogg")
