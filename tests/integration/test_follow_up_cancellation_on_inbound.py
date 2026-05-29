"""On inbound: cancel pending follow-ups + reset counter + cold->active + schedule attempt 1."""

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
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


_YAML_FOLLOWUP = """
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


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def _seed_inactive_lead(db_session, *, talkflow_status="active"):
    tenant = Tenant(slug=f"inb_{uuid.uuid4().hex[:6]}", display_name="I")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml=_YAML_FOLLOWUP,
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
        last_agent_message_at=datetime.now(UTC) - timedelta(hours=10),
        follow_up_attempt_number=2,
    )
    tf.status = talkflow_status
    db_session.add(tf)
    await db_session.flush()

    # Pre-existing pending follow-up + a queued inbound (this triggers the worker)
    db_session.add(
        FollowUpJob(
            tenant_id=tenant.id,
            talkflow_id=tf.id,
            lead_id=lead.id,
            attempt_number=3,
            scheduled_at=datetime.now(UTC) + timedelta(hours=10),
            status="pending",
        )
    )
    db_session.add(
        InboundMessageRow(
            tenant_id=tenant.id,
            provider="whatsapp_cloud",
            external_id=f"wamid_{uuid.uuid4().hex}",
            lead_id=lead.id,
            from_address="+5511999",
            text="estou de volta",
            received_at=datetime.now(UTC),
            raw={},
        )
    )
    await db_session.commit()
    return tenant, tf, lead


def _ctx(session_factory, adapter, runtime_response_text="oi"):
    async def runtime_step_stub(*args, **kwargs):
        return MagicMock(response_text=runtime_response_text)

    runtime = MagicMock()
    runtime.step = runtime_step_stub
    registry = MagicMock()
    registry.get.return_value = adapter
    return {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime}


async def test_inbound_cancels_pending_and_resets_counter(db_session, session_factory) -> None:
    tenant, tf, lead = await _seed_inactive_lead(db_session)
    adapter = FakeMessagingAdapter()

    await process_lead_inbox(
        _ctx(session_factory, adapter),
        str(tenant.id),
        str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(tf)
    assert tf.follow_up_attempt_number == 0
    assert tf.last_lead_message_at is not None

    # Pre-existing pending -> cancelled
    jobs = (
        (
            await db_session.execute(
                select(FollowUpJob)
                .where(FollowUpJob.lead_id == lead.id)
                .order_by(FollowUpJob.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    pre_existing = jobs[0]
    assert pre_existing.status == "cancelled"
    assert pre_existing.error_detail == "lead responded"

    # New attempt 1 scheduled (in-flight TreeFlow has follow_up.enabled=true)
    assert len(jobs) >= 2
    new_jobs = [j for j in jobs if j.status == "pending"]
    assert len(new_jobs) == 1
    assert new_jobs[0].attempt_number == 1


async def test_inbound_reactivates_cold_talkflow(db_session, session_factory) -> None:
    tenant, tf, lead = await _seed_inactive_lead(db_session, talkflow_status="cold")
    adapter = FakeMessagingAdapter()

    await process_lead_inbox(
        _ctx(session_factory, adapter),
        str(tenant.id),
        str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(tf)
    assert tf.status == "active"
