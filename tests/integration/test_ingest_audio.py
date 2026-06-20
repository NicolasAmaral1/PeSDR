"""Integration test — ingest persists media_type for audio messages."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.ingest import ingest_inbound_message
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session) -> Tenant:
    t = Tenant(slug=f"t_{uuid.uuid4().hex[:6]}", display_name="T")
    session.add(t)
    await session.flush()
    return t


async def test_ingest_persists_audio_media_type(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    msg = InboundMessage(
        external_id="wamid.A1",
        from_address="+5511988887777",
        text="",
        received_at_iso="2026-06-19T12:00:00+00:00",
        raw={"id": "wamid.A1", "type": "audio", "audio": {"id": "media-xyz"}},
        media_type="audio",
        media_ref="media-xyz",
    )
    await ingest_inbound_message(db_session, tenant, "whatsapp_cloud", msg)
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    row = (
        await db_session.execute(
            select(InboundMessageRow).where(
                InboundMessageRow.tenant_id == tenant.id,
                InboundMessageRow.external_id == "wamid.A1",
            )
        )
    ).scalar_one()
    assert row.media_type == "audio"
    assert row.raw["audio"]["id"] == "media-xyz"
