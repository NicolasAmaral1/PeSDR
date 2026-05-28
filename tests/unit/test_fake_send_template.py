"""FakeMessagingAdapter.send_template behavioral tests."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter


async def test_send_template_records_call() -> None:
    fake = FakeMessagingAdapter()
    r = await fake.send_template(
        to="+5511999",
        template_ref="followup_24h_v1",
        language="pt_BR",
        params=["Maria"],
    )
    assert fake.sent_templates == [
        ("+5511999", "followup_24h_v1", "pt_BR", ["Maria"]),
    ]
    assert r.external_id


async def test_fail_next_template_send_raises_once() -> None:
    fake = FakeMessagingAdapter()
    fake.fail_next_template_send(RecipientUnreachable("not on WA"))

    with pytest.raises(RecipientUnreachable):
        await fake.send_template("+5511999", "x", "pt_BR", [])

    # Next call succeeds
    r = await fake.send_template("+5511999", "x", "pt_BR", [])
    assert r.external_id


async def test_send_text_and_send_template_independent_buffers() -> None:
    fake = FakeMessagingAdapter()
    await fake.send_text("+1", "hi")
    await fake.send_template("+2", "ref", "pt_BR", ["X"])
    assert fake.sent_messages == [("+1", "hi")]
    assert fake.sent_templates == [("+2", "ref", "pt_BR", ["X"])]
