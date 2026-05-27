"""Scanner picks only due pending jobs, ignores future/cancelled/completed."""

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
  max_attempts: 2
  sequence:
    - after: PT1H
      template_ref: t1
    - after: PT2H
      template_ref: t2
"""


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"sb_{uuid.uuid4().hex[:6]}", display_name="SB")
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
    await db_session.commit()
    return tenant, tf, lead


@pytest.fixture
def session_factory():
    return async_sessionmaker(
        build_engine(get_settings().database_url), expire_on_commit=False
    )


async def test_scanner_fires_only_due_pending(db_session, session_factory) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    # 1 due + 1 future + 1 cancelled
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    ))
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=2, scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    ))
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=2, scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
        status="cancelled",
    ))
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    registry = MagicMock()
    registry.get.return_value = adapter

    await follow_up_scanner({
        "session_factory": session_factory,
        "adapter_registry": registry,
    })

    # Exactly one template sent (the due pending)
    assert len(adapter.sent_templates) == 1
    assert adapter.sent_templates[0][1] == "t1"

    # State updates
    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    jobs = (await db_session.execute(
        select(FollowUpJob)
        .where(FollowUpJob.lead_id == lead.id)
        .order_by(FollowUpJob.attempt_number, FollowUpJob.created_at)
    )).scalars().all()
    completed = [j for j in jobs if j.status == "completed"]
    pending = [j for j in jobs if j.status == "pending"]
    cancelled = [j for j in jobs if j.status == "cancelled"]
    assert len(completed) == 1  # attempt 1 just fired
    assert len(pending) == 2    # original future + newly-scheduled attempt 2
    assert len(cancelled) == 1  # untouched
