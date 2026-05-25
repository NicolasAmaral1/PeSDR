"""Webhook routes — challenge handshake, signature failure, ingest happy path."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.tenant_yaml import MessagingConfig

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"


@pytest.fixture
async def example_tenant_in_db(db_session) -> Tenant:
    t = Tenant(slug=f"webhk_{uuid.uuid4().hex[:6]}", display_name="Hook Tenant")
    db_session.add(t)
    await db_session.commit()
    return t


@pytest.fixture
def signed_app(app, monkeypatch, example_tenant_in_db, db_session):
    """Mount an AdapterRegistry that returns a real WhatsAppCloudAPIAdapter
    seeded with known test secrets."""
    from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter

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
        "wa_verify": "verify_token_42",
        "wa_app_secret": "app_secret_xyz",
    }
    adapter = WhatsAppCloudAPIAdapter(cfg, secrets)

    class StaticRegistry:
        def get(self, tenant, provider):
            return adapter

    app.state.adapter_registry = StaticRegistry()
    return app


async def test_get_challenge_echoes_when_token_matches(signed_app, example_tenant_in_db) -> None:
    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.get(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify_token_42",
                "hub.challenge": "challenge_payload",
            },
        )
    assert r.status_code == 200
    assert r.text == "challenge_payload"


async def test_get_challenge_401_when_token_mismatch(signed_app, example_tenant_in_db) -> None:
    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.get(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "WRONG",
                "hub.challenge": "x",
            },
        )
    assert r.status_code == 401


async def test_post_returns_401_on_bad_signature(signed_app, example_tenant_in_db) -> None:
    body = (FIXTURES / "inbound_text.json").read_bytes()
    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": "sha256=" + "0" * 64},
        )
    assert r.status_code == 401


async def test_post_ingests_and_enqueues(
    signed_app, example_tenant_in_db, db_session, monkeypatch
) -> None:
    body = (FIXTURES / "inbound_text.json").read_bytes()
    sig = "sha256=" + hmac.new(b"app_secret_xyz", body, hashlib.sha256).hexdigest()

    enqueued = []

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args, kwargs))

    signed_app.state.arq_pool = FakePool()

    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": sig},
        )
    assert r.status_code == 200

    from ai_sdr.db.rls import set_tenant_context

    await set_tenant_context(db_session, example_tenant_in_db.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "oi, queria saber sobre a mentoria"

    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert len(leads) == 1
    assert leads[0].status == "pending_assignment"
    assert leads[0].whatsapp_e164 == "+5511988887777"

    # One job enqueued for the affected lead
    assert len(enqueued) == 1
    name, args, _ = enqueued[0]
    assert name == "process_lead_inbox"
    assert args == (str(example_tenant_in_db.id), str(leads[0].id))


async def test_post_idempotent_on_duplicate_external_id(
    signed_app, example_tenant_in_db, db_session
) -> None:
    body = (FIXTURES / "inbound_text.json").read_bytes()
    sig = "sha256=" + hmac.new(b"app_secret_xyz", body, hashlib.sha256).hexdigest()

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            return None

    signed_app.state.arq_pool = FakePool()

    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r1 = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": sig},
        )
        r2 = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": sig},
        )
    assert r1.status_code == 200
    assert r2.status_code == 200

    from ai_sdr.db.rls import set_tenant_context

    await set_tenant_context(db_session, example_tenant_in_db.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1  # second was deduped
