from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.repositories.inbox_repository import derive_state, list_contacts

pytestmark = pytest.mark.integration


def test_derive_state_pure():
    class T:  # minimal active-talk stand-in
        def __init__(self, status, hm): self.status, self.handling_mode = status, hm
    assert derive_state(None) == "awaiting"
    assert derive_state(T("active", "ai")) == "ai"
    assert derive_state(T("requires_review", "ai")) == "requires_review"
    assert derive_state(T("active", "human")) == "human"


async def test_list_contacts_includes_lead_without_talk(db_session):
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T", architecture_version=2)
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant.id)})

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="pending_assignment", inbound_channel_label="main")
    db_session.add(lead)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud", external_id=f"e-{uuid.uuid4().hex[:8]}",
        lead_id=lead.id, from_address="+5511999", text="oi quero registrar",
        received_at=now, raw={"body": "oi"}, status="queued", media_type="text",
    ))
    await db_session.flush()

    contacts = await list_contacts(
        db_session, tenant_id=tenant.id, channel_label="main", user_id=uuid.uuid4()
    )
    assert len(contacts) == 1
    c = contacts[0]
    assert c.lead_id == lead.id
    assert c.state == "awaiting"          # no Talk → awaiting
    assert c.last_message_preview.startswith("oi")
    assert c.unread == 1                  # no read marker → 1 unread
