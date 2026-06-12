"""sender.send_response_text iterates humanized chunks (FE-03b Task 9)."""

from __future__ import annotations

import uuid

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.flowengine.sender import send_response_text
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.lead import Lead


def _lead() -> Lead:
    # Use the SQLAlchemy declarative constructor — bypassing it via
    # ``Lead.__new__`` leaves the InstrumentedAttribute descriptors
    # without backing state, so attribute access blows up. sender only
    # reads ``.id`` and ``.whatsapp_e164``; tenant_id is required for the
    # ORM constructor but otherwise unused here.
    return Lead(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=uuid.uuid4(),
        whatsapp_e164="+5511999999999",
    )


def _decision(text: str) -> TurnDecision:
    return TurnDecision(
        response_text=text,
        collected_fields={},
        reasoning="test",
    )


def _cfg(min_delay_ms: int = 0, max_delay_ms: int = 0) -> HumanizationConfig:
    """Zero-delay config so the test doesn't actually sleep."""
    return HumanizationConfig(
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
    )


@pytest.mark.asyncio
async def test_three_paragraphs_yield_three_sends():
    adapter = FakeMessagingAdapter()
    text = "Olá!\n\nQue legal saber.\n\nQual seu segmento?"
    result = await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision(text),
        humanization_config=_cfg(),
    )
    assert len(adapter.sent_messages) == 3
    assert adapter.sent_messages[0]["text"] == "Olá!"
    assert adapter.sent_messages[2]["text"] == "Qual seu segmento?"
    assert result.status == "sent"


@pytest.mark.asyncio
async def test_typing_indicator_called_before_each_chunk_with_delay():
    adapter = FakeMessagingAdapter()
    cfg = HumanizationConfig(
        chars_per_second_min=10.0,
        chars_per_second_max=10.0,
        min_delay_ms=0,
        max_delay_ms=10,
    )
    await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision("a\n\nbb\n\nccc"),
        humanization_config=cfg,
    )
    # 3 chunks; first has zero delay (no typing call), 2 with delay → 2 typing
    assert len(adapter.typing_calls) == 2


@pytest.mark.asyncio
async def test_single_chunk_no_typing_call():
    adapter = FakeMessagingAdapter()
    await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision("Apenas uma linha sem delimiter."),
        humanization_config=_cfg(),
    )
    assert len(adapter.sent_messages) == 1
    assert adapter.typing_calls == []


@pytest.mark.asyncio
async def test_disabled_humanization_yields_single_send():
    adapter = FakeMessagingAdapter()
    cfg = HumanizationConfig(enabled=False)
    await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision("Olá!\n\nMundo!"),
        humanization_config=cfg,
    )
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0]["text"] == "Olá!\n\nMundo!"
