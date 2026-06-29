from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ai_sdr.api.schemas.console_inbox import ContactOut, InstanceOut, MessageOut


def test_contact_out_serializes_state_and_funnel():
    c = ContactOut(
        lead_id=uuid.uuid4(), display_name="João", whatsapp_e164="+5511",
        last_message_at=datetime.now(timezone.utc), last_message_preview="oi",
        state="ai", funnel_node="proposta", unread=2,
    )
    assert c.state == "ai"
    assert c.unread == 2


def test_message_out_side_and_kind():
    m = MessageOut(
        id=uuid.uuid4(), direction="out", origin="operator",
        text="oi", media_type="text", at=datetime.now(timezone.utc),
    )
    assert m.direction == "out"
    assert m.origin == "operator"


def test_instance_out():
    i = InstanceOut(id=uuid.uuid4(), channel_label="main", display_name="Avelum", phone_e164=None)
    assert i.channel_label == "main"
