"""3-turn happy-path E2E through run_turn + FakeMessagingAdapter."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.pipeline import run_turn
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.outbound_message import OutboundMessage

from tests.integration.avelum_helpers import seed_avelum_v2


def _td(text: str, *, collected=None, next_node=None, advance=False) -> TurnDecision:
    return TurnDecision(
        response_text=text,
        collected_fields=collected or {},
        reasoning="r",
        next_node_suggestion=next_node,
        intends_to_advance=advance,
    )


async def _send_inbound(
    session: AsyncSession, tenant, body: str
) -> InboundMessageRow:
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text=body,
        raw={"body": body},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    session.add(inbound)
    await session.flush()
    return inbound


@pytest.mark.asyncio
async def test_three_turn_happy_path(db_session: AsyncSession) -> None:
    tenant, tfv = await seed_avelum_v2(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)
    adapter = FakeMessagingAdapter()
    llm = AsyncMock()
    gcfg = GuardrailConfig(disallowed_price_pattern=r"R\$\d+", allowed_prices=[])

    # Turn 1
    inbound1 = await _send_inbound(db_session, tenant, "oi")
    llm.ainvoke = AsyncMock(return_value=_td("oi! qual seu segmento?"))
    r1 = await run_turn(
        db_session, tenant=tenant, treeflow=treeflow, treeflow_version=tfv,
        inbound=inbound1, llm=llm, adapter=adapter,
        opt_out_keywords=["sair"], guardrail_cfg=gcfg,
        now=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
    )
    assert r1.outcome == "sent"
    assert r1.current_node_after == "saudacao"

    # Turn 2: lead says "saas"
    inbound2 = await _send_inbound(db_session, tenant, "saas")
    llm.ainvoke = AsyncMock(return_value=_td(
        "legal saas! qual seu ticket medio?",
        collected={"segmento": "saas"},
        next_node="qualificacao_economica",
        advance=True,
    ))
    r2 = await run_turn(
        db_session, tenant=tenant, treeflow=treeflow, treeflow_version=tfv,
        inbound=inbound2, llm=llm, adapter=adapter,
        opt_out_keywords=["sair"], guardrail_cfg=gcfg,
        now=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
    )
    assert r2.outcome == "sent"
    assert r2.current_node_after == "qualificacao_economica"

    # Turn 3
    inbound3 = await _send_inbound(db_session, tenant, "uns 2000 por mes")
    llm.ainvoke = AsyncMock(return_value=_td(
        "show, valeu pelas infos.",
        collected={"ticket_medio": "2000"},
    ))
    r3 = await run_turn(
        db_session, tenant=tenant, treeflow=treeflow, treeflow_version=tfv,
        inbound=inbound3, llm=llm, adapter=adapter,
        opt_out_keywords=["sair"], guardrail_cfg=gcfg,
        now=datetime(2026, 6, 2, 10, 10, tzinfo=timezone.utc),
    )
    assert r3.outcome == "sent"

    # 3 outbound rows
    rows = (
        await db_session.execute(
            select(OutboundMessage).where(OutboundMessage.tenant_id == tenant.id)
        )
    ).scalars().all()
    assert len(rows) == 3
    assert {r.body_text for r in rows} == {
        "oi! qual seu segmento?",
        "legal saas! qual seu ticket medio?",
        "show, valeu pelas infos.",
    }
