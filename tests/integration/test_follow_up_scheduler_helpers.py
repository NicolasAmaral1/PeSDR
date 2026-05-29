"""follow_up.scheduler — cancel_pending_for_lead, schedule_next_followup, mark_cold_if_exhausted."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.scheduler import (
    cancel_pending_for_lead,
    mark_cold_if_exhausted,
    schedule_next_followup,
)
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.treeflow_yaml import FollowUpConfig, FollowUpStep

pytestmark = pytest.mark.integration


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"s_{uuid.uuid4().hex[:6]}", display_name="S")
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
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
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


def _config() -> FollowUpConfig:
    return FollowUpConfig(
        enabled=True,
        max_attempts=3,
        sequence=[
            FollowUpStep(after="PT24H", template_ref="t1"),
            FollowUpStep(after="P3D", template_ref="t2"),
            FollowUpStep(after="P7D", template_ref="t3"),
        ],
    )


async def test_cancel_pending_for_lead_marks_only_pending(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)

    # 1 pending + 1 completed + 1 cancelled
    db_session.add_all(
        [
            FollowUpJob(
                tenant_id=tenant.id,
                talkflow_id=tf.id,
                lead_id=lead.id,
                attempt_number=1,
                scheduled_at=datetime.now(UTC),
                status="pending",
            ),
            FollowUpJob(
                tenant_id=tenant.id,
                talkflow_id=tf.id,
                lead_id=lead.id,
                attempt_number=2,
                scheduled_at=datetime.now(UTC),
                status="completed",
                fired_at=datetime.now(UTC),
            ),
            FollowUpJob(
                tenant_id=tenant.id,
                talkflow_id=tf.id,
                lead_id=lead.id,
                attempt_number=3,
                scheduled_at=datetime.now(UTC),
                status="cancelled",
            ),
        ]
    )
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    rowcount = await cancel_pending_for_lead(db_session, lead.id, reason="lead responded")
    await db_session.commit()
    assert rowcount == 1

    await set_tenant_context(db_session, tenant.id)
    rows = (
        (
            await db_session.execute(
                select(FollowUpJob)
                .where(FollowUpJob.lead_id == lead.id)
                .order_by(FollowUpJob.attempt_number)
            )
        )
        .scalars()
        .all()
    )
    assert [r.status for r in rows] == ["cancelled", "completed", "cancelled"]
    assert rows[0].error_detail == "lead responded"


async def test_schedule_next_followup_inserts_with_correct_delay(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    cfg = _config()
    before = datetime.now(UTC)
    await schedule_next_followup(db_session, tf, lead, tenant, cfg, next_attempt_number=2)
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    job = (
        await db_session.execute(select(FollowUpJob).where(FollowUpJob.lead_id == lead.id))
    ).scalar_one()
    assert job.attempt_number == 2
    assert job.status == "pending"
    # sequence[1] is "P3D" -> 72h
    expected_delta = timedelta(days=3)
    assert (
        before + expected_delta - timedelta(seconds=5)
        <= job.scheduled_at
        <= datetime.now(UTC) + expected_delta + timedelta(seconds=5)
    )


def test_mark_cold_if_exhausted() -> None:
    tf = TalkFlow(
        tenant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        treeflow_version_id=uuid.uuid4(),
        thread_id="x",
    )
    tf.status = "active"
    cfg = _config()  # max_attempts=3

    # attempt 1 -> not exhausted
    assert mark_cold_if_exhausted(tf, cfg, 1) is False
    assert tf.status == "active"

    # attempt 3 -> exhausted
    assert mark_cold_if_exhausted(tf, cfg, 3) is True
    assert tf.status == "cold"
