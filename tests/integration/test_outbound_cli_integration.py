"""ai-sdr outbound list — hits real DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from ai_sdr.cli.app import app
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration

runner = CliRunner()


async def test_list_shows_recent_outbound(db_session) -> None:
    tenant = Tenant(slug=f"cli_{uuid.uuid4().hex[:6]}", display_name="CLI")
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
    await db_session.flush()

    # 1 sent text + 1 failed template
    db_session.add(
        OutboundMessage(
            tenant_id=tenant.id,
            talkflow_id=tf.id,
            lead_id=lead.id,
            provider="whatsapp_cloud",
            message_type="text",
            body_text="Olá",
            status="sent",
            external_id="wamid.A",
            triggered_by="inbound",
            sent_at=datetime.now(UTC),
        )
    )
    db_session.add(
        OutboundMessage(
            tenant_id=tenant.id,
            talkflow_id=tf.id,
            lead_id=lead.id,
            provider="whatsapp_cloud",
            message_type="template",
            template_ref="t1",
            template_language="pt_BR",
            template_params=["x"],
            status="failed",
            error_detail="AuthError: bad token",
            triggered_by="follow_up_scanner",
            sent_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    r = runner.invoke(app, ["outbound", "list", "--tenant", tenant.slug])
    assert r.exit_code == 0
    assert "Olá" in r.output
    assert "t1" in r.output
    assert "inbound" in r.output
    assert "follow_up_scanner" in r.output
    assert "sent" in r.output
    assert "failed" in r.output


async def test_list_filter_status_failed(db_session) -> None:
    tenant = Tenant(slug=f"cli2_{uuid.uuid4().hex[:6]}", display_name="C2")
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
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+2", status="active")
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
    db_session.add(
        OutboundMessage(
            tenant_id=tenant.id,
            talkflow_id=tf.id,
            lead_id=lead.id,
            provider="whatsapp_cloud",
            message_type="text",
            body_text="OK",
            status="sent",
            external_id="wamid.X",
            triggered_by="inbound",
            sent_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    r = runner.invoke(app, ["outbound", "list", "--tenant", tenant.slug, "--status", "failed"])
    assert r.exit_code == 0
    assert "no outbound messages" in r.output.lower()
