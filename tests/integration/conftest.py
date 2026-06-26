"""Integration-level fixtures shared across inbox test files."""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis as sync_redis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.testclient import TestClient

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.pipeline import run_turn
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.realtime.events import channel_for
from ai_sdr.schemas.tenant_yaml import TenantConfig
from ai_sdr.settings import get_settings
from ai_sdr.web.auth import sign_session_cookie
from ai_sdr.web.passwords import hash_password
from tests.integration.avelum_helpers import seed_avelum_v2


def _make_tenant_yaml(tmpdir: Path, slug: str) -> None:
    (tmpdir / slug).mkdir(parents=True, exist_ok=True)
    yaml = f"""id: {slug}
display_name: {slug.title()}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
console:
  enabled: true
"""
    (tmpdir / slug / "tenant.yaml").write_text(yaml)


def _patch_settings(monkeypatch, tdir: Path, secret: str = "x" * 48) -> None:
    from ai_sdr.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "console_secret_key", secret)
    monkeypatch.setattr(s, "tenants_dir", str(tdir))


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
async def authed_inbox_client(app, db_session, isolated_tenants_dir, monkeypatch):
    """Signed-in httpx client + seeded tenant with console enabled, instance, lead, inbound.

    Yields (client, ctx) where ctx contains:
      - slug: tenant slug
      - tenant: Tenant ORM object
      - user: User ORM object
      - lead: Lead ORM object
      - lead_id: UUID of the seeded lead (convenience alias)
      - instance: Instance ORM object
    """
    _patch_settings(monkeypatch, isolated_tenants_dir)

    tenant = Tenant(slug=f"inbox-{uuid.uuid4().hex[:6]}", display_name="InboxTest")
    db_session.add(tenant)
    await db_session.flush()
    _make_tenant_yaml(isolated_tenants_dir, tenant.slug)

    await set_tenant_context(db_session, tenant.id)

    user = User(username=f"u_{uuid.uuid4().hex[:6]}", password_hash=hash_password("pw"))
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role="operator"))

    instance = Instance(tenant_id=tenant.id, channel_label="main", display_name="Main")
    db_session.add(instance)
    await db_session.flush()

    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511988887777",
        status="pending_assignment",
        inbound_channel_label="main",
    )
    db_session.add(lead)
    await db_session.flush()

    db_session.add(
        InboundMessageRow(
            tenant_id=tenant.id,
            provider="whatsapp_cloud",
            external_id=f"wamid.{uuid.uuid4().hex}",
            lead_id=lead.id,
            from_address="+5511988887777",
            text="oi, queria saber sobre a mentoria",
            received_at=datetime.now(UTC),
            raw={},
        )
    )
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        login_resp = await client.post(
            "/console/login",
            data={"username": user.username, "password": "pw"},
        )
        assert login_resp.status_code == 303, f"Login failed: {login_resp.status_code} {login_resp.text}"
        cookie = login_resp.cookies["pesdr_session"]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
        cookies={"pesdr_session": cookie},
    ) as client:
        yield client, {
            "slug": tenant.slug,
            "tenant": tenant,
            "user": user,
            "lead": lead,
            "lead_id": lead.id,
            "instance": instance,
        }


@pytest.fixture
def seeded_talk_factory(db_session):
    """Async factory fixture that seeds Tenant + Lead + TreeflowVersion + Talk.

    Usage::

        talk, tenant = await seeded_talk_factory(handling_mode="ai")
        talk, tenant = await seeded_talk_factory(handling_mode="human", status="active")
        talk, tenant = await seeded_talk_factory(lead_id=existing_lead.id)

    Mirrors Talk-seeding in tests/integration/test_inbox_filters.py
    (test_multi_lead_active_talk_lateral) — sets: treeflow_id,
    treeflow_version_id, status, handling_mode, last_message_at.

    Args:
        handling_mode: "ai" | "human" | "auto_with_approval" (default "ai").
        status: Talk status string (default "active").
        lead_id: If provided, attach the Talk to this existing lead instead of
                 creating a new one. The Lead's tenant_id must match the seeded
                 tenant (caller's responsibility).
    """

    async def _factory(
        handling_mode: str = "ai",
        status: str = "active",
        lead_id: uuid.UUID | None = None,
    ) -> tuple[Talk, Tenant]:
        if lead_id is not None:
            # --- lead_id path: attach Talk to an existing lead's tenant ---
            existing_lead = await db_session.get(Lead, lead_id)
            if existing_lead is None:
                raise ValueError(f"seeded_talk_factory: lead {lead_id} not found")

            tenant = await db_session.get(Tenant, existing_lead.tenant_id)
            if tenant is None:
                raise ValueError(
                    f"seeded_talk_factory: tenant {existing_lead.tenant_id} not found"
                )

            # Set RLS tenant context to the lead's tenant.
            await set_tenant_context(db_session, tenant.id)

            # Reuse an existing TreeflowVersion for this tenant if available,
            # otherwise create one.
            tfv_result = await db_session.execute(
                select(TreeflowVersion)
                .where(TreeflowVersion.tenant_id == tenant.id)
                .limit(1)
            )
            tfv = tfv_result.scalars().first()
            if tfv is None:
                tfv = TreeflowVersion(
                    tenant_id=tenant.id,
                    treeflow_id="tf-seeded",
                    version="1",
                    content_hash=uuid.uuid4().hex,
                    content_yaml="nodes: []",
                )
                db_session.add(tfv)
                await db_session.flush()

        else:
            # --- no lead_id path: create a fresh Tenant + Lead + TreeflowVersion ---
            tenant = Tenant(slug=f"talk-{uuid.uuid4().hex[:6]}", display_name="TalkTest")
            db_session.add(tenant)
            await db_session.flush()

            # Set RLS tenant context (required by tenant-scoped tables).
            await set_tenant_context(db_session, tenant.id)

            # Seed a TreeflowVersion so Talk FK is satisfied.
            tfv = TreeflowVersion(
                tenant_id=tenant.id,
                treeflow_id="tf-seeded",
                version="1",
                content_hash=uuid.uuid4().hex,
                content_yaml="nodes: []",
            )
            db_session.add(tfv)
            await db_session.flush()

            lead = Lead(
                tenant_id=tenant.id,
                whatsapp_e164=f"+551{uuid.uuid4().int % 10**10:010d}",
                status="pending_assignment",
                inbound_channel_label="main",
            )
            db_session.add(lead)
            await db_session.flush()
            lead_id = lead.id

        talk = Talk(
            tenant_id=tenant.id,
            lead_id=lead_id,
            treeflow_id="tf-seeded",
            treeflow_version_id=tfv.id,
            status=status,
            handling_mode=handling_mode,
            last_message_at=datetime.now(UTC),
        )
        db_session.add(talk)
        await db_session.flush()

        return talk, tenant

    return _factory


# ---------------------------------------------------------------------------
# authed_inbox_client_with_fake_adapter
# ---------------------------------------------------------------------------

class _FakeRegistryStub:
    """Minimal registry stub: get_for_tenant always returns the shared FakeMessagingAdapter."""

    def __init__(self, adapter: FakeMessagingAdapter) -> None:
        self._adapter = adapter

    def get_for_tenant(self, tenant: object) -> FakeMessagingAdapter:  # noqa: ARG002
        return self._adapter


@pytest.fixture
async def authed_inbox_client_with_fake_adapter(app, db_session, isolated_tenants_dir, monkeypatch):
    """Like authed_inbox_client but replaces app.state.adapter_registry with a
    _FakeRegistryStub backed by a shared FakeMessagingAdapter.

    Yields (client, ctx) where ctx contains:
      - slug: tenant slug
      - tenant: Tenant ORM object
      - user: User ORM object
      - lead: Lead ORM object
      - lead_id: UUID of the seeded lead (convenience alias)
      - instance: Instance ORM object
      - fake_adapter: the shared FakeMessagingAdapter instance (for inspection/forcing errors)
    """
    _patch_settings(monkeypatch, isolated_tenants_dir)

    tenant = Tenant(slug=f"inbox-{uuid.uuid4().hex[:6]}", display_name="InboxTest")
    db_session.add(tenant)
    await db_session.flush()
    _make_tenant_yaml(isolated_tenants_dir, tenant.slug)

    await set_tenant_context(db_session, tenant.id)

    user = User(username=f"u_{uuid.uuid4().hex[:6]}", password_hash=hash_password("pw"))
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role="operator"))

    instance = Instance(tenant_id=tenant.id, channel_label="main", display_name="Main")
    db_session.add(instance)
    await db_session.flush()

    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511988887777",
        status="pending_assignment",
        inbound_channel_label="main",
    )
    db_session.add(lead)
    await db_session.flush()

    db_session.add(
        InboundMessageRow(
            tenant_id=tenant.id,
            provider="whatsapp_cloud",
            external_id=f"wamid.{uuid.uuid4().hex}",
            lead_id=lead.id,
            from_address="+5511988887777",
            text="oi, queria saber sobre a mentoria",
            received_at=datetime.now(UTC),
            raw={},
        )
    )
    await db_session.commit()

    # Wire up the fake adapter registry BEFORE creating the client.
    fake_adapter = FakeMessagingAdapter()
    app.state.adapter_registry = _FakeRegistryStub(fake_adapter)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        login_resp = await client.post(
            "/console/login",
            data={"username": user.username, "password": "pw"},
        )
        assert login_resp.status_code == 303, f"Login failed: {login_resp.status_code} {login_resp.text}"
        cookie = login_resp.cookies["pesdr_session"]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
        cookies={"pesdr_session": cookie},
    ) as client:
        yield client, {
            "slug": tenant.slug,
            "tenant": tenant,
            "user": user,
            "lead": lead,
            "lead_id": lead.id,
            "instance": instance,
            "fake_adapter": fake_adapter,
        }


# ---------------------------------------------------------------------------
# run_turn_human_harness
# ---------------------------------------------------------------------------

def _stub_human_gate_tenant_cfg(slug: str) -> TenantConfig:
    return TenantConfig.model_validate(
        {
            "id": slug,
            "display_name": "Human Gate Test stub",
            "timezone": "America/Sao_Paulo",
            "schedule": {"mon-fri": "08:00-22:00"},
            "conversation": {"optout_stop_words": ["sair"]},
            "llm": {
                "default": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "api_key_ref": "secrets/openai_key",
                },
            },
            "guardrails": {
                "allowed_products": ["sdr_smoke"],
                "disallowed_price_pattern": r"R\$\s?\d+",
                "fallback_text": "Vou validar com a equipe.",
            },
        }
    )


class _LLMCalledFlag:
    """Mutable flag object used to detect if the stub LLM was invoked."""

    def __init__(self) -> None:
        self.value = False


class _StubLLMRaisesIfCalled:
    """Stub LLM that raises AssertionError and flips the flag if ainvoke is called."""

    def __init__(self, flag: _LLMCalledFlag) -> None:
        self._flag = flag

    async def ainvoke(self, messages):  # noqa: ARG002
        self._flag.value = True
        raise AssertionError("LLM was invoked despite human handling_mode — gate failed")


@pytest.fixture
def run_turn_human_harness(db_session):
    """Async factory that wires run_turn for the AI-suppression gate test.

    Pre-seeds: tenant + treeflow (avelum_v2) + lead + active Talk with
    handling_mode='human' + TalkFlowState so state_repo.load() succeeds.

    The stub LLM raises (and flips llm_called.value) if called.
    Returns (result, adapter, llm_called) where llm_called is an
    _LLMCalledFlag.

    Usage::

        result, adapter, llm_called = await run_turn_human_harness(handling_mode="human")
    """

    async def _harness(handling_mode: str = "human", inbound_text: str = "oi"):
        # 1. Seed tenant + treeflow using the same helper as the smoke tests.
        tenant, tfv = await seed_avelum_v2(db_session)
        treeflow = load_treeflow_v2(tfv.content_yaml)
        tenant_cfg = _stub_human_gate_tenant_cfg(tenant.slug)

        # 2. Seed a Lead with a specific phone (matches inbound.from_address below).
        phone = "+5511888880001"
        lead = Lead(
            tenant_id=tenant.id,
            channel_identifiers={"whatsapp": phone},
            whatsapp_e164=phone,
            status="active",
        )
        db_session.add(lead)
        await db_session.flush()

        # 3. Pre-create the Talk with handling_mode='human' so
        #    resolve_pipeline_context → find_active_for_lead returns it
        #    instead of creating a new ai-mode talk.
        talk = Talk(
            tenant_id=tenant.id,
            lead_id=lead.id,
            treeflow_id=tfv.treeflow_id,
            treeflow_version_id=tfv.id,
            status="active",
            handling_mode=handling_mode,
            last_message_at=datetime.now(UTC),
        )
        db_session.add(talk)
        await db_session.flush()

        # 4. Initialize TalkFlowState so state_repo.load() returns a row
        #    (for existing talks, preprocessing does NOT create state).
        state = TalkFlowState(
            talk_id=talk.id,
            tenant_id=tenant.id,
            current_node=treeflow.entry_node,
            collected={},
            extracted_facts={},
            messages=[],
            objections_handled=[],
            talkflow_stack=[],
        )
        db_session.add(state)
        await db_session.flush()

        # 5. Seed the inbound message from the same phone.
        #    inbound_text is parameterized so callers can supply an opt-out keyword.
        inbound = InboundMessageRow(
            tenant_id=tenant.id,
            provider="fake",
            external_id=f"ext-{uuid.uuid4().hex[:6]}",
            from_address=phone,
            text=inbound_text,
            raw={"body": inbound_text},
            media_type="text",
            received_at=datetime.now(UTC),
        )
        db_session.add(inbound)
        await db_session.flush()

        # 6. Wire stub LLM (raises if called) + FakeMessagingAdapter.
        llm_called = _LLMCalledFlag()
        stub_llm = _StubLLMRaisesIfCalled(llm_called)
        adapter = FakeMessagingAdapter()

        gcfg = GuardrailConfig(
            disallowed_price_pattern=r"R\$\d+",
            allowed_prices=[],
            allowed_products=["sdr_smoke"],
            fallback_text="Vou validar com a equipe.",
        )

        result = await run_turn(
            db_session,
            tenant=tenant,
            tenant_cfg=tenant_cfg,
            treeflow=treeflow,
            treeflow_version=tfv,
            inbound=inbound,
            llm=stub_llm,
            adapter=adapter,
            opt_out_keywords=["sair"],
            guardrail_cfg=gcfg,
            now=datetime(2026, 6, 26, 10, 0, tzinfo=UTC),
        )

        return result, adapter, llm_called

    return _harness


# ---------------------------------------------------------------------------
# ws_authed_ctx — WebSocket inbox route (cookie auth + live delivery)
# ---------------------------------------------------------------------------


@pytest.fixture
async def ws_authed_ctx(app, db_session, isolated_tenants_dir, monkeypatch):
    """Sync Starlette TestClient (lifespan run) + seeded instance + auth cookie.

    Yields (client, ctx) where ctx contains:
      - instance_id: UUID (str-able) of the seeded Instance
      - cookie: signed pesdr_session value for a User with tenant access
      - publish(*, type, lead_id, payload): publishes an inbox event to the
        instance's channel via a SYNCHRONOUS redis client, replicating the
        seq INCR + envelope shape of publish_inbox_event. This sidesteps the
        sync/async bridge: the TestClient runs the app (and the hub's pubsub
        reader) in its own portal thread; the test publishes from the main
        thread, so the event genuinely round-trips through Redis.

    Mirrors test_console_leads_page.py for tenant.yaml (console.enabled=true),
    User + UserTenantAccess seeding, and cookie signing via sign_session_cookie.
    """
    _patch_settings(monkeypatch, isolated_tenants_dir)

    tenant = Tenant(slug=f"ws-{uuid.uuid4().hex[:6]}", display_name="WsTest")
    db_session.add(tenant)
    await db_session.flush()
    _make_tenant_yaml(isolated_tenants_dir, tenant.slug)

    await set_tenant_context(db_session, tenant.id)

    user = User(username=f"u_{uuid.uuid4().hex[:6]}", password_hash=hash_password("pw"))
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role="operator"))

    instance = Instance(tenant_id=tenant.id, channel_label="main", display_name="Main")
    db_session.add(instance)
    await db_session.flush()

    instance_id = instance.id
    await db_session.commit()

    cookie = sign_session_cookie(user.id)

    redis_url = get_settings().redis_url
    rconn = sync_redis.Redis.from_url(redis_url, decode_responses=True)

    def publish(*, type: str, lead_id: uuid.UUID | None, payload: dict) -> int:
        seq = rconn.incr(f"seq:inst:{instance_id}")
        envelope = {
            "seq": int(seq),
            "type": type,
            "instance_id": str(instance_id),
            "lead_id": str(lead_id) if lead_id is not None else None,
            "payload": payload,
        }
        rconn.publish(channel_for(instance_id), json.dumps(envelope))
        return int(seq)

    # The WS endpoint opens a DB session via the module-global sessionmaker
    # (db.session._sessionmaker). It lazy-inits a *pooled* asyncpg engine bound
    # to whatever loop first touches it. In the full suite, an earlier test may
    # have left that global engine bound to a now-dead loop; reusing it inside
    # the TestClient portal loop then raises "got Future attached to a different
    # loop". So we reset the globals BOTH before (fresh engine on the portal
    # loop) AND after (so the next test re-inits on its OWN loop).
    import ai_sdr.db.session as _dbsession

    _dbsession._engine = None
    _dbsession._sessionmaker = None

    # Enter the sync TestClient as a context manager so the app lifespan runs
    # (creating app.state.redis + app.state.inbox_hub). The hub's psubscribe
    # reader runs on the app's event loop inside the portal thread.
    with TestClient(app) as client:
        try:
            yield client, {
                "instance_id": instance_id,
                "cookie": cookie,
                "publish": publish,
            }
        finally:
            rconn.close()
            _dbsession._engine = None
            _dbsession._sessionmaker = None
