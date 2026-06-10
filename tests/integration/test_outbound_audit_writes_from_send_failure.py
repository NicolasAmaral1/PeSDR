"""Worker inbound: send_text failure → outbound_messages row with status=failed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_recipient_unreachable_writes_failed_outbound(db_session, session_factory) -> None:
    # Same seed as previous test — abridged here
    tenant = Tenant(slug=f"f_{uuid.uuid4().hex[:6]}", display_name="F")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.flush()
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}",
        lead_id=lead.id,
        from_address="+5511999",
        text="oi",
        received_at=datetime.now(UTC),
        raw={},
    )
    db_session.add(inbound)
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(RecipientUnreachable("number not on WA"))

    runtime = MagicMock()

    async def step_stub(*a, **kw):
        return MagicMock(response_text="Olá")

    runtime.step = step_stub
    registry = MagicMock()
    registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id),
        str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    row = (
        await db_session.execute(select(OutboundMessage).where(OutboundMessage.lead_id == lead.id))
    ).scalar_one()
    assert row.status == "failed"
    assert row.message_type == "text"
    assert row.body_text == "Olá"
    assert row.triggered_by == "inbound"
    assert "RecipientUnreachable" in (row.error_detail or "")
    assert row.external_id is None
