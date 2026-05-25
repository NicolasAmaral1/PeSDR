"""FakeMessagingAdapter behavioral tests."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter


async def test_handle_inbound_returns_queued_then_empties() -> None:
    fake = FakeMessagingAdapter()
    msg = InboundMessage(
        external_id="m1",
        from_address="+5511999999999",
        text="oi",
        received_at_iso="2026-05-25T12:00:00+00:00",
        raw={"id": "m1"},
    )
    fake.queue_inbound(msg)

    out = await fake.handle_inbound(b"", {})
    assert out == [msg]

    out_again = await fake.handle_inbound(b"", {})
    assert out_again == []  # queue drained


async def test_send_text_records_sent_messages() -> None:
    fake = FakeMessagingAdapter()
    r1 = await fake.send_text("+5511999999991", "hello")
    r2 = await fake.send_text("+5511999999992", "world")
    assert fake.sent_messages == [
        ("+5511999999991", "hello"),
        ("+5511999999992", "world"),
    ]
    assert r1.external_id != r2.external_id


async def test_fail_next_send_raises_once() -> None:
    fake = FakeMessagingAdapter()
    fake.fail_next_send(RecipientUnreachable("number not on WA"))

    with pytest.raises(RecipientUnreachable):
        await fake.send_text("+5511999999999", "x")

    # Subsequent send succeeds
    r = await fake.send_text("+5511999999999", "y")
    assert r.external_id


def test_verification_challenge_echoes() -> None:
    fake = FakeMessagingAdapter()
    assert fake.verification_challenge({"hub.challenge": "abc123"}) == "abc123"
    assert fake.verification_challenge({}) is None
