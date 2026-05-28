"""ABC enforcement — subclass without send_template can't instantiate."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from ai_sdr.messaging.base import MessagingAdapter, SendResult


def test_subclass_without_send_template_fails() -> None:
    class _Incomplete(MessagingAdapter):
        async def handle_inbound(self, raw_body: bytes, headers: Mapping[str, str]):
            return []

        async def send_text(self, to: str, text: str) -> SendResult:
            return SendResult(external_id="x", sent_at_iso="now")

        def verification_challenge(self, params: Mapping[str, str]) -> str | None:
            return None

    with pytest.raises(TypeError, match="abstract"):
        _Incomplete()  # type: ignore[abstract]


def test_complete_subclass_instantiates() -> None:
    class _Complete(MessagingAdapter):
        async def handle_inbound(self, raw_body, headers):
            return []

        async def send_text(self, to, text):
            return SendResult(external_id="x", sent_at_iso="now")

        async def send_template(self, to, template_ref, language, params):
            return SendResult(external_id="t", sent_at_iso="now")

        def verification_challenge(self, params):
            return None

    a = _Complete()
    assert isinstance(a, MessagingAdapter)
