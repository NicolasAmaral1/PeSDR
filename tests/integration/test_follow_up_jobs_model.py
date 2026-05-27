"""FollowUpJob ORM — RLS isolation, FK cascades, check constraints, partial indexes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
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

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    return tenant, tf, lead


async def test_create_follow_up_job_defaults(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    job = FollowUpJob(
        tenant_id=tenant.id,
        talkflow_id=tf.id,
        lead_id=lead.id,
        attempt_number=1,
        scheduled_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(job)
    await db_session.commit()
    assert job.status == "pending"
    assert job.fired_at is None
    assert job.created_at is not None


async def test_check_constraint_rejects_bad_status(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC), status="weird",
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_check_constraint_rejects_zero_attempt(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=0, scheduled_at=datetime.now(UTC),
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_rls_blocks_cross_tenant_read(db_session) -> None:
    tenant_a, tf_a, lead_a = await _seed(db_session)
    await set_tenant_context(db_session, tenant_a.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant_a.id, talkflow_id=tf_a.id, lead_id=lead_a.id,
        attempt_number=1, scheduled_at=datetime.now(UTC),
    ))
    await db_session.commit()

    # Switch to a fresh tenant — should see nothing
    tenant_b = Tenant(slug=f"b_{uuid.uuid4().hex[:6]}", display_name="B")
    db_session.add(tenant_b)
    await db_session.commit()
    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(FollowUpJob))).scalars().all()
    assert rows == []


async def test_lead_cascade_delete_removes_jobs(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC),
    ))
    await db_session.commit()

    await db_session.delete(lead)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)
    rows = (await db_session.execute(select(FollowUpJob))).scalars().all()
    assert rows == []


async def test_talkflow_new_columns_default_correctly(db_session) -> None:
    tenant, tf, _lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    await db_session.refresh(tf)
    assert tf.last_agent_message_at is None
    assert tf.last_lead_message_at is None
    assert tf.follow_up_attempt_number == 0
