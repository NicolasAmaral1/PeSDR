"""run_turn end-to-end smoke against FakeMessagingAdapter + canned LLM."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.pipeline import RunTurnResult, run_turn
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

from tests.fixtures.canned_decisions import (
    collect_segment_decision,
    greeting_decision,
)


MINIMAL_TF_YAML = """
schema_version: 1
id: t
version: "1"
sdr_persona:
  voice: "Tom PT-BR"
  conduct: "Sempre reconheca"
  examples: []
entry_node: saudacao
nodes:
  - id: saudacao
    objetivo: descobrir segmento
    bridge_instruction: ""
    collects:
      - field: segmento
        type: text
        required: true
    exit_condition: {type: all_fields_filled}
    next_nodes:
      - condition: "true"
        target: qualificacao
  - id: qualificacao
    objetivo: descobrir ticket
    bridge_instruction: ""
    collects:
      - field: ticket_medio
        type: text
        required: true
    exit_condition: {type: all_fields_filled}
    next_nodes: []
"""


async def _seed_tenant(db_session: AsyncSession) -> tuple[Tenant, TreeflowVersion]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml=MINIMAL_TF_YAML,
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    return tenant, tfv


@pytest.mark.asyncio
async def test_first_turn_sends_greeting_and_writes_outbound(
    db_session: AsyncSession,
) -> None:
    tenant, tfv = await _seed_tenant(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)

    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="oi",
        raw={"body": "oi"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound)
    await db_session.flush()

    adapter = FakeMessagingAdapter()
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=greeting_decision())

    result = await run_turn(
        db_session,
        tenant=tenant,
        treeflow=treeflow,
        treeflow_version=tfv,
        inbound=inbound,
        llm=llm,
        adapter=adapter,
        opt_out_keywords=[],
        guardrail_cfg=GuardrailConfig(disallowed_price_pattern=r"R\$\d+", allowed_prices=[]),
        now=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
    )

    assert isinstance(result, RunTurnResult)
    assert result.outcome == "sent"
    # FakeMessagingAdapter stores (to, text) tuples in .sent_messages
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0][1] == greeting_decision().response_text

    # Outbound row exists
    rows = (
        await db_session.execute(
            select(OutboundMessage).where(OutboundMessage.tenant_id == tenant.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].body_text == greeting_decision().response_text


@pytest.mark.asyncio
async def test_second_turn_advances_node(db_session: AsyncSession) -> None:
    tenant, tfv = await _seed_tenant(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)

    adapter = FakeMessagingAdapter()
    llm = AsyncMock()

    # Turn 1: greeting
    inbound1 = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"a-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="oi",
        raw={"body": "oi"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound1)
    await db_session.flush()
    llm.ainvoke = AsyncMock(return_value=greeting_decision())
    await run_turn(
        db_session, tenant=tenant, treeflow=treeflow, treeflow_version=tfv,
        inbound=inbound1, llm=llm, adapter=adapter,
        opt_out_keywords=[],
        guardrail_cfg=GuardrailConfig(disallowed_price_pattern=r"R\$\d+", allowed_prices=[]),
        now=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
    )

    # Turn 2: lead says "saas" -> collect + advance
    inbound2 = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"b-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="saas",
        raw={"body": "saas"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound2)
    await db_session.flush()
    llm.ainvoke = AsyncMock(return_value=collect_segment_decision())
    result = await run_turn(
        db_session, tenant=tenant, treeflow=treeflow, treeflow_version=tfv,
        inbound=inbound2, llm=llm, adapter=adapter,
        opt_out_keywords=[],
        guardrail_cfg=GuardrailConfig(disallowed_price_pattern=r"R\$\d+", allowed_prices=[]),
        now=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
    )
    assert result.outcome == "sent"
    assert result.current_node_after == "qualificacao"
