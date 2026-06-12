"""E2E reference contract: 3-chunk send with delays (FE-03b Task 17)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_three_paragraph_response_sends_three_messages(
    async_session,
    run_turn_harness,
    fake_llm_polite,
):
    llm = fake_llm_polite(response_text="Oi!\n\nQue legal!\n\nQual seu segmento?")
    await run_turn_harness.send_inbound("oi")
    await run_turn_harness.run(llm=llm)
    sent = run_turn_harness.captured_outbound()
    assert len(sent) == 3
    assert sent[0]["text"] == "Oi!"
    assert sent[2]["text"] == "Qual seu segmento?"
