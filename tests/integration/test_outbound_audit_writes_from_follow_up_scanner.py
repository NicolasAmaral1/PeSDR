"""Scanner fires job → 1 outbound row with triggered_by=follow_up_scanner."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.follow_up_scanner import follow_up_scanner

pytestmark = pytest.mark.integration


_YAML = """
id: t1
version: 1.0.0
entry_node: n1
nodes: {n1: {prompt: hi}}
follow_up:
  enabled: true
  max_attempts: 1
  sequence:
    - after: PT1H
      template_ref: followup_24h_v1
      language: pt_BR
      params: ["{{ collected.nome | default('amigo') }}"]
"""


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_scanner_send_template_writes_outbound_row(
    db_session, session_factory
) -> None:
    tenant = Tenant(slug=f"sa_{uuid.uuid4().hex[:6]}", display_name="SA")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64, content_yaml=_YAML,
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
        last_agent_message_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(tf)
    await db_session.flush()

    job = FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(job)
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    registry = MagicMock()
    registry.get.return_value = adapter

    await follow_up_scanner({
        "session_factory": session_factory,
        "adapter_registry": registry,
    })

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(OutboundMessage).where(OutboundMessage.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "sent"
    assert row.message_type == "template"
    assert row.template_ref == "followup_24h_v1"
    assert row.template_params == ["amigo"]
    assert row.triggered_by == "follow_up_scanner"
    assert row.follow_up_job_id == job.id
    assert row.inbound_message_id is None
