"""When the active talk is human-held, run_turn must NOT call the LLM or send."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_run_turn_skips_when_human(db_session, seeded_talk_factory, run_turn_human_harness):
    # harness: builds tenant/treeflow/inbound for a lead whose ACTIVE talk is handling_mode='human';
    # llm is a stub that RAISES if invoked; adapter is a FakeMessagingAdapter.
    result, adapter, llm_called = await run_turn_human_harness(handling_mode="human")
    assert result.outcome == "skipped_human"
    assert not adapter.sent_messages   # nothing sent
    assert llm_called.value is False   # LLM never invoked
