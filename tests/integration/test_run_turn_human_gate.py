"""When the active talk is human-held, run_turn must NOT call the LLM or send."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from ai_sdr.flowengine.pipeline import run_turn
from ai_sdr.flowengine.preprocessing import PipelineContext, resolve_pipeline_context
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.treeflow_version import TreeflowVersion
from tests.integration.avelum_helpers import seed_avelum_v2
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.schemas.tenant_yaml import TenantConfig

pytestmark = pytest.mark.integration


async def test_run_turn_skips_when_human(db_session, seeded_talk_factory, run_turn_human_harness):
    # harness: builds tenant/treeflow/inbound for a lead whose ACTIVE talk is handling_mode='human';
    # llm is a stub that RAISES if invoked; adapter is a FakeMessagingAdapter.
    result, adapter, llm_called = await run_turn_human_harness(handling_mode="human")
    assert result.outcome == "skipped_human"
    assert not adapter.sent_messages   # nothing sent
    assert llm_called.value is False   # LLM never invoked


async def test_run_turn_skips_when_takeover_mid_turn_race(db_session):
    """Regression: session.refresh() under the lock closes the takeover-mid-turn race.

    Scenario: resolve_pipeline_context loads ctx.talk with handling_mode='ai'
    (the in-session ORM object cached as 'ai'). A takeover then updates the DB
    row to handling_mode='human' BEFORE the advisory lock is acquired. Because
    expire_on_commit=False, the ORM attribute is stale and would still read 'ai'
    without an explicit refresh.

    The fix (session.refresh(ctx.talk) immediately after acquire_lead_lock) must
    re-read the DB value under the lock so the gate fires and returns
    outcome='skipped_human' — no LLM call, no message sent.

    Simulation: we patch resolve_pipeline_context to (a) call the real impl so
    the talk is loaded with handling_mode='ai', (b) immediately UPDATE the DB row
    to handling_mode='human' via raw SQL (mimicking an external takeover), and
    (c) manually reset the ORM attribute back to 'ai' to simulate the stale cache
    that expire_on_commit=False leaves after the preprocessing commit. Without
    session.refresh() in pipeline.py, the gate check would read the stale 'ai'
    and invoke the LLM. With the refresh, it reads 'human' from the DB and skips.
    """
    # 1. Seed tenant + treeflow + lead + talk(handling_mode='ai') + state + inbound.
    tenant, tfv = await seed_avelum_v2(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)

    phone = "+5511777770002"
    lead = Lead(
        tenant_id=tenant.id,
        channel_identifiers={"whatsapp": phone},
        whatsapp_e164=phone,
        status="active",
    )
    db_session.add(lead)
    await db_session.flush()

    # Start as 'ai' — this is what resolve_pipeline_context will see initially.
    talk = Talk(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id=tfv.treeflow_id,
        treeflow_version_id=tfv.id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

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

    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address=phone,
        text="oi",
        raw={"body": "oi"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound)
    await db_session.flush()

    talk_id = talk.id

    # 2. Wrap resolve_pipeline_context to simulate the race:
    #    - Call real impl (loads talk with handling_mode='ai' into ORM cache)
    #    - UPDATE the DB row to handling_mode='human' (external takeover)
    #    - Reset the ORM attribute to 'ai' (simulate stale cache from expire_on_commit=False)
    real_resolve = resolve_pipeline_context

    async def _patched_resolve(session, **kwargs):
        ctx = await real_resolve(session, **kwargs)
        # Simulate an external takeover: DB row is now 'human'.
        await session.execute(
            text("UPDATE talks SET handling_mode = 'human' WHERE id = :talk_id"),
            {"talk_id": talk_id},
        )
        # Simulate stale ORM cache (expire_on_commit=False leaves old value).
        # After the preprocessing commit in run_turn, the object is NOT expired,
        # so it would still report 'ai' without a refresh.
        ctx.talk.__dict__["handling_mode"] = "ai"
        return ctx

    # Stub LLM: raises + flips flag if invoked.
    llm_called = False

    class _StubLLMRaises:
        async def ainvoke(self, messages):  # noqa: ARG002
            nonlocal llm_called
            llm_called = True
            raise AssertionError("LLM was invoked — session.refresh() race fix failed")

    adapter = FakeMessagingAdapter()
    tenant_cfg = TenantConfig.model_validate(
        {
            "id": tenant.slug,
            "display_name": "Race Test",
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
    gcfg = GuardrailConfig(
        disallowed_price_pattern=r"R\$\d+",
        allowed_prices=[],
        allowed_products=["sdr_smoke"],
        fallback_text="Vou validar com a equipe.",
    )

    with patch(
        "ai_sdr.flowengine.pipeline.resolve_pipeline_context",
        side_effect=_patched_resolve,
    ):
        result = await run_turn(
            db_session,
            tenant=tenant,
            tenant_cfg=tenant_cfg,
            treeflow=treeflow,
            treeflow_version=tfv,
            inbound=inbound,
            llm=_StubLLMRaises(),
            adapter=adapter,
            opt_out_keywords=["sair"],
            guardrail_cfg=gcfg,
            now=datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc),
        )

    # session.refresh(ctx.talk) under the lock must read 'human' from the DB,
    # causing the gate to fire and skip the LLM call.
    assert result.outcome == "skipped_human", (
        f"Expected 'skipped_human' but got '{result.outcome}' — "
        "session.refresh() race fix may be missing or broken"
    )
    assert not adapter.sent_messages, "No message must be sent for a human-held talk"
    assert llm_called is False, "LLM must NOT be invoked for a human-held talk"
