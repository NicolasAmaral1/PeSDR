"""End-to-end: webhook → ingest → assign → worker drains queue → adapter.send."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"


async def test_end_to_end_webhook_assign_worker_reply(app, db_session) -> None:
    # --- 1. Set up tenant + treeflow + a registry that returns a FakeAdapter ---
    tenant = Tenant(slug=f"e2e_{uuid.uuid4().hex[:6]}", display_name="E2E")
    db_session.add(tenant)
    await db_session.flush()
    # treeflow_versions has RLS — set context before inserting.
    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="mentoria",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: mentoria\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.commit()

    # Static registry: pretend tenant.yaml says provider=whatsapp_cloud, but we
    # mount a real WhatsAppCloudAPIAdapter with known secrets so we can sign
    # the inbound webhook body. The *outbound* send_text path is patched to
    # a FakeMessagingAdapter so we don't hit the real Graph API.
    from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
    from ai_sdr.schemas.tenant_yaml import MessagingConfig

    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999",
        "wa_token": "EAA",
        "wa_verify": "vt",
        "wa_app_secret": "appsecret_e2e",
    }
    wa_adapter = WhatsAppCloudAPIAdapter(cfg, secrets)

    fake_for_send = FakeMessagingAdapter()

    class HybridAdapter:
        """For inbound, behave like WhatsApp (HMAC + parser). For send_text,
        delegate to the FakeMessagingAdapter so we don't network out."""

        async def handle_inbound(self, body, headers):
            return await wa_adapter.handle_inbound(body, headers)

        async def send_text(self, to, text):
            return await fake_for_send.send_text(to, text)

        def verification_challenge(self, params):
            return wa_adapter.verification_challenge(params)

    hybrid = HybridAdapter()

    class StaticRegistry:
        def get(self, tenant, provider):
            return hybrid

    app.state.adapter_registry = StaticRegistry()

    # arq pool that simply records jobs (we'll invoke them manually below).
    enqueued: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args))

        async def aclose(self) -> None:
            pass

    app.state.arq_pool = FakePool()

    # --- 2. POST a signed inbound webhook ---
    body = (FIXTURES / "inbound_text.json").read_bytes()
    sig = "sha256=" + hmac.new(b"appsecret_e2e", body, hashlib.sha256).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            f"/webhooks/{tenant.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": sig},
        )
    assert r.status_code == 200

    # --- 3. The lead is now pending; the worker job should no-op on it ---
    # Capture IDs as plain values BEFORE expire_all, since expire makes
    # subsequent .id access trigger a sync attribute refresh that breaks
    # outside an async greenlet context.
    tenant_uuid = tenant.id
    tenant_id_str = str(tenant_uuid)
    tenant_slug = tenant.slug
    await set_tenant_context(db_session, tenant_uuid)
    db_session.expire_all()
    lead = (await db_session.execute(select(Lead))).scalar_one()
    assert lead.status == "pending_assignment"
    lead_id_str = str(lead.id)

    engine = build_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = MagicMock()
    registry.get.return_value = hybrid
    runtime = MagicMock()
    runtime.step = MagicMock(side_effect=AssertionError("must not step on pending lead"))

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        tenant_id_str,
        lead_id_str,
    )
    assert fake_for_send.sent_messages == []

    # --- 4. Operator assigns ---
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            f"/tenants/{tenant_slug}/leads/{lead_id_str}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 202

    # --- 5. Worker runs again (now lead is active) and replies ---
    async def runtime_step_stub(session, t, talkflow_id, user_input):
        return MagicMock(response_text="Olá! Recebi sua mensagem.")

    runtime_alive = MagicMock()
    runtime_alive.step = runtime_step_stub

    await process_lead_inbox(
        {
            "session_factory": session_factory,
            "adapter_registry": registry,
            "runtime": runtime_alive,
        },
        tenant_id_str,
        lead_id_str,
    )
    assert fake_for_send.sent_messages == [
        ("+5511988887777", "Olá! Recebi sua mensagem."),
    ]

    # All inbounds processed
    await set_tenant_context(db_session, tenant_uuid)
    db_session.expire_all()
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert {r.status for r in rows} == {"processed"}
