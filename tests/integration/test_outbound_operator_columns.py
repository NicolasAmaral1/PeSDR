from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from ai_sdr.models.outbound_message import OutboundMessage

pytestmark = pytest.mark.integration


async def test_operator_outbound_row_allowed(db_session, seeded_talk_factory):
    talk, tenant = await seeded_talk_factory(handling_mode="human")
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=talk.id, lead_id=talk.lead_id,
        provider="whatsapp_cloud", message_type="text", body_text="oi do operador",
        status="sent", triggered_by="operator", client_message_id=uuid.uuid4(),
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    await db_session.flush()  # must NOT violate ck_outbound_triggered_by
    assert row.client_message_id is not None
