"""MessagingAdapter.mark_as_typing protocol + impls (FE-03b Task 8)."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.fake import FakeMessagingAdapter


def test_default_protocol_is_no_op():
    """The base class's mark_as_typing default implementation returns None."""
    assert MessagingAdapter.mark_as_typing.__doc__ is not None


@pytest.mark.asyncio
async def test_fake_adapter_records_typing_calls():
    """FakeMessagingAdapter records mark_as_typing calls for tests."""
    adapter = FakeMessagingAdapter()
    await adapter.mark_as_typing("+5511999999999")
    assert adapter.typing_calls == ["+5511999999999"]


@pytest.mark.asyncio
async def test_fake_adapter_typing_calls_empty_by_default():
    adapter = FakeMessagingAdapter()
    assert adapter.typing_calls == []
