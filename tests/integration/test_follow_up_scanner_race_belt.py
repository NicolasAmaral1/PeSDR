"""Race belt: scheduler.last_lead_message_at > job.scheduled_at -> cancel, don't send."""

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
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.follow_up_scanner import follow_up_scanner

pytestmark = pytest.mark.integration


_YAML = """
id: t1
version: 1.0.0
display_name: T1
entry_node: n1
nodes:
  - id: n1
    prompt: hi
    exit_condition:
      type: all_fields_filled
    next_nodes:
      - condition: "true"
        target: END
follow_up:
  enabled: true
  max_attempts: 1
  sequence:
    - after: PT1H
      template_ref: t1
"""


@pytest.fixture
def session_factory():
    return async_sessionmaker(
        build_engine(get_settings().database_url), expire_on_commit=False
    )


async def test_lead_responded_after_scheduling_cancels_job(
    db_session, session_factory
) -> None:
    tenant = Tenant(slug=f"rb_{uuid.uuid4().hex[:6]}", display_name="RB")
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

    # Job scheduled at T0, but lead responded at T0+something later.
    job_scheduled_at = datetime.now(UTC) - timedelta(minutes=5)
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
        last_lead_message_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    db_session.add(tf)
    await db_session.flush()
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=job_scheduled_at, status="pending",
    ))
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    registry = MagicMock()
    registry.get.return_value = adapter

    await follow_up_scanner({
        "session_factory": session_factory,
        "adapter_registry": registry,
    })

    # Race-belt fires: job cancelled, no template sent
    assert adapter.sent_templates == []
    await set_tenant_context(db_session, tenant.id)
    job = (await db_session.execute(select(FollowUpJob))).scalar_one()
    assert job.status == "cancelled"
    assert "responded" in (job.error_detail or "")
