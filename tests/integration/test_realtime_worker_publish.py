"""After the worker turn sends an AI reply, a message.created is published.

Drives `process_lead_inbox` end-to-end through the FlowEngine v2 path
(architecture_version=2). The LLM is stubbed (monkeypatched
`main_llm_for_tenant`) to return a normal `TurnDecision` so the turn
outcome is 'sent'. A redis subscriber on the lead's instance channel
asserts a `message.created` event arrives — the worker (a separate
process from the API) publishes to Redis, which the API's InboxHub then
fans out to browsers.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.realtime.events import channel_for
from ai_sdr.realtime.producers import resolve_instance_id
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox
from tests.integration.avelum_helpers import seed_avelum_v2

pytestmark = pytest.mark.integration


def _make_tenant_yaml(tmpdir, slug: str) -> None:
    yaml = f"""id: {slug}
display_name: {slug.title()}
timezone: America/Sao_Paulo
schedule:
  mon-fri: "00:00-23:59"
conversation:
  optout_stop_words: ["sair"]
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
guardrails:
  allowed_products: ["sdr_smoke"]
  disallowed_price_pattern: "R\\\\$\\\\s?\\\\d+"
  fallback_text: "Vou validar com a equipe."
"""
    (tmpdir / slug).mkdir(parents=True, exist_ok=True)
    (tmpdir / slug / "tenant.yaml").write_text(yaml)


@pytest.fixture
def session_factory():
    engine = build_engine(get_settings().database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def run_turn_publish_harness(
    db_session, session_factory, isolated_tenants_dir, monkeypatch
):
    """Drive `process_lead_inbox` to a 'sent' outcome and collect realtime events.

    Returns an async factory; calling it runs the worker job end-to-end with
    a stubbed LLM + fake adapter + a real redis subscriber on the lead's
    instance channel, then returns the list of decoded events that arrived.
    """

    async def _harness():
        # 1. architecture_version=2 tenant + treeflow (routes to FlowEngine v2).
        tenant, tfv = await seed_avelum_v2(db_session)

        # tenant.yaml on disk so TenantLoader.load(slug) succeeds in _run_v2_inbox.
        s = get_settings()
        monkeypatch.setattr(s, "tenants_dir", str(isolated_tenants_dir))
        _make_tenant_yaml(isolated_tenants_dir, tenant.slug)

        # 2. Lead (active, channel 'main') + TalkFlow + Instance on 'main'.
        await set_tenant_context(db_session, tenant.id)
        instance = Instance(
            tenant_id=tenant.id, channel_label="main", display_name="Main"
        )
        db_session.add(instance)
        await db_session.flush()

        phone = f"+5511{uuid.uuid4().int % 10**9:09d}"
        lead = Lead(
            tenant_id=tenant.id,
            whatsapp_e164=phone,
            channel_identifiers={"whatsapp": phone},
            status="active",
            inbound_channel_label="main",
        )
        db_session.add(lead)
        await db_session.flush()

        talkflow = TalkFlow(
            tenant_id=tenant.id,
            lead_id=lead.id,
            treeflow_version_id=tfv.id,
            thread_id=f"{tenant.id}:{uuid.uuid4()}",
        )
        db_session.add(talkflow)
        await db_session.flush()

        # 3. Queued inbound for the lead.
        db_session.add(
            InboundMessageRow(
                tenant_id=tenant.id,
                provider="fake",
                external_id=f"ext-{uuid.uuid4().hex[:8]}",
                lead_id=lead.id,
                from_address=phone,
                text="oi",
                raw={"body": "oi"},
                media_type="text",
                received_at=datetime.now(UTC),
            )
        )
        await db_session.commit()

        # 4. Stub the LLM so run_turn produces a normal 'sent' outcome.
        stub_llm = MagicMock()
        stub_llm.ainvoke = AsyncMock(
            return_value=TurnDecision(
                response_text="oi! como posso ajudar?",
                collected_fields={},
                reasoning="r",
                next_node_suggestion=None,
                intends_to_advance=False,
            )
        )
        monkeypatch.setattr(
            "ai_sdr.flowengine.llm_client.main_llm_for_tenant",
            lambda *a, **k: stub_llm,
        )
        # No real secrets on disk — the stubbed LLM never touches them.
        monkeypatch.setattr(
            "ai_sdr.secrets.sops_loader.SopsLoader.load",
            lambda self, slug: {},
        )

        # 5. Real adapter registry stub returning a FakeMessagingAdapter.
        adapter = FakeMessagingAdapter()
        registry = MagicMock()
        registry.get_for_tenant.return_value = adapter

        # 6. Real redis client on ctx + a subscriber on the lead's instance channel.
        inst_id = await resolve_instance_id(
            db_session, tenant_id=tenant.id, channel_label="main"
        )
        assert inst_id is not None
        redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_for(inst_id))
        await pubsub.get_message(timeout=1.0)  # drain subscribe confirmation

        ctx = {
            "session_factory": session_factory,
            "adapter_registry": registry,
            "redis": redis,
        }

        try:
            await process_lead_inbox(ctx, str(tenant.id), str(lead.id))

            events = []
            for _ in range(20):
                m = await pubsub.get_message(
                    timeout=0.5, ignore_subscribe_messages=True
                )
                if m:
                    events.append(json.loads(m["data"]))
            return events
        finally:
            await pubsub.aclose()
            await redis.aclose()

    return _harness


async def test_ai_reply_publishes_message_created(run_turn_publish_harness):
    events = await run_turn_publish_harness()
    assert any(e["type"] == "message.created" for e in events), (
        f"expected a message.created event, got: {[e['type'] for e in events]}"
    )
