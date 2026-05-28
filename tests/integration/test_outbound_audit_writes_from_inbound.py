"""Worker inbound: send_text success → outbound_messages row with triggered_by=inbound."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
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


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead, InboundMessageRow]:
    tenant = Tenant(slug=f"oi_{uuid.uuid4().hex[:6]}", display_name="OI")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.flush()

    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="oi",
        received_at=datetime.now(UTC), raw={},
    )
    db_session.add(inbound)
    await db_session.commit()
    return tenant, tf, lead, inbound


async def test_send_text_success_writes_outbound_row(
    db_session, session_factory
) -> None:
    tenant, tf, lead, inbound = await _seed(db_session)

    adapter = FakeMessagingAdapter()
    runtime = MagicMock()
    async def step_stub(*a, **kw):
        return MagicMock(response_text="Olá! Como posso ajudar?")
    runtime.step = step_stub
    registry = MagicMock()
    registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(OutboundMessage).where(OutboundMessage.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "sent"
    assert row.message_type == "text"
    assert row.body_text == "Olá! Como posso ajudar?"
    assert row.triggered_by == "inbound"
    assert row.inbound_message_id == inbound.id
    assert row.follow_up_job_id is None
    assert row.external_id  # populated by FakeMessagingAdapter
