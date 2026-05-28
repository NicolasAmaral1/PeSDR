"""OutboundMessage ORM — RLS, FK cascades, XOR check, triggered_by enum."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"o_{uuid.uuid4().hex[:6]}", display_name="O")
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
    await db_session.commit()
    return tenant, tf, lead


async def test_create_text_message(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="Olá",
        status="sent",
        external_id="wamid.X",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    assert row.id is not None
    assert row.created_at is not None


async def test_create_template_message(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="template",
        template_ref="followup_24h_v1",
        template_language="pt_BR",
        template_params=["amigo"],
        status="sent",
        external_id="wamid.Y",
        triggered_by="follow_up_scanner",
        sent_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    assert row.template_params == ["amigo"]


async def test_xor_check_text_with_template_ref_fails(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="Olá",
        template_ref="should_not_be_here",
        status="sent",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    with pytest.raises(IntegrityError):  # ck_outbound_body_consistency
        await db_session.commit()
    await db_session.rollback()


async def test_xor_check_template_missing_ref_fails(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="template",
        # no template_ref — must fail
        status="sent",
        triggered_by="follow_up_scanner",
        sent_at=datetime.now(UTC),
    ))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_triggered_by_enum_rejects_invalid(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="x",
        status="sent",
        triggered_by="manual_takeover",  # not yet a valid enum value
        sent_at=datetime.now(UTC),
    ))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_rls_blocks_cross_tenant_read(db_session) -> None:
    tenant_a, tf_a, lead_a = await _seed(db_session)
    await set_tenant_context(db_session, tenant_a.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant_a.id, talkflow_id=tf_a.id, lead_id=lead_a.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="visible only to tenant A",
        status="sent",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    await db_session.commit()

    tenant_b = Tenant(slug=f"b_{uuid.uuid4().hex[:6]}", display_name="B")
    db_session.add(tenant_b)
    await db_session.commit()
    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(OutboundMessage))).scalars().all()
    assert rows == []


async def test_lead_cascade_delete_removes_outbound(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="bye",
        status="sent",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    await db_session.commit()

    await db_session.delete(lead)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)
    rows = (await db_session.execute(select(OutboundMessage))).scalars().all()
    assert rows == []
