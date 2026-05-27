"""ai-sdr follow-ups cancel — hits real DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from ai_sdr.cli.app import app
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration

runner = CliRunner()


async def test_cancel_marks_pending_as_cancelled(db_session) -> None:
    tenant = Tenant(slug=f"cli_{uuid.uuid4().hex[:6]}", display_name="C")
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
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    ))
    await db_session.commit()

    r = runner.invoke(
        app,
        ["follow-ups", "cancel", "--tenant", tenant.slug, "--lead", str(lead.id)],
    )
    assert r.exit_code == 0
    assert "cancelled 1" in r.output

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    job = (
        await db_session.execute(
            select(FollowUpJob).where(FollowUpJob.lead_id == lead.id)
        )
    ).scalar_one()
    assert job.status == "cancelled"
    assert "manual" in (job.error_detail or "")
