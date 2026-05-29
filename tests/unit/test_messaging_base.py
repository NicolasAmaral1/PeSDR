"""Contract surface tests for messaging.base."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from ai_sdr.messaging.base import (
    InboundMessage,
    MessagingAdapter,
    SendResult,
)


def test_inbound_message_is_frozen() -> None:
    m = InboundMessage(
        external_id="wa_1",
        from_address="+5511999999999",
        text="oi",
        received_at_iso="2026-05-25T12:00:00+00:00",
        raw={"id": "wa_1"},
    )
    with pytest.raises(FrozenInstanceError):
        m.text = "tampered"  # type: ignore[misc]


def test_send_result_is_frozen() -> None:
    r = SendResult(external_id="wa_sent_1", sent_at_iso="2026-05-25T12:00:01+00:00")
    with pytest.raises(FrozenInstanceError):
        r.external_id = "x"  # type: ignore[misc]


def test_cannot_instantiate_abstract_adapter() -> None:
    with pytest.raises(TypeError, match="abstract"):
        MessagingAdapter()  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated() -> None:
    class Dummy(MessagingAdapter):
        async def handle_inbound(
            self, raw_body: bytes, headers: Mapping[str, str]
        ) -> list[InboundMessage]:
            return []

        async def send_text(self, to: str, text: str) -> SendResult:
            return SendResult(external_id="x", sent_at_iso="now")

        async def send_template(
            self, to: str, template_ref: str, language: str, params: list[str]
        ) -> SendResult:
            return SendResult(external_id="t", sent_at_iso="now")

        def verification_challenge(self, params: Mapping[str, str]) -> str | None:
            return None

    d = Dummy()
    assert isinstance(d, MessagingAdapter)
