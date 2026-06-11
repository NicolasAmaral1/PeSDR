"""send_response_text dispatches to MessagingAdapter.send_text."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.sender import SendResult, send_response_text
from ai_sdr.messaging.base import SendResult as AdapterSendResult


def _adapter() -> MagicMock:
    a = MagicMock()
    a.send_text = AsyncMock(
        return_value=AdapterSendResult(
            external_id="ext-123",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )
    )
    return a


def _lead() -> MagicMock:
    l = MagicMock()
    l.id = uuid.uuid4()
    l.whatsapp_e164 = "+5511999999999"
    return l


@pytest.mark.asyncio
async def test_dispatches_to_adapter_send_text() -> None:
    adapter = _adapter()
    lead = _lead()
    decision = TurnDecision(
        response_text="oi",
        collected_fields={},
        reasoning="r",
    )
    result = await send_response_text(
        adapter=adapter,
        lead=lead,
        decision=decision,
    )
    assert isinstance(result, SendResult)
    assert result.external_id == "ext-123"
    assert result.status == "sent"
    adapter.send_text.assert_awaited_once_with("+5511999999999", "oi")


@pytest.mark.asyncio
async def test_voice_format_falls_back_to_text_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _adapter()
    lead = _lead()
    decision = TurnDecision(
        response_text="oi",
        collected_fields={},
        reasoning="r",
        response_format="voice",
    )
    with caplog.at_level(logging.WARNING):
        result = await send_response_text(
            adapter=adapter,
            lead=lead,
            decision=decision,
        )
    assert result.status == "sent"
    adapter.send_text.assert_awaited_once_with("+5511999999999", "oi")
    assert any("voice" in r.message.lower() for r in caplog.records)
