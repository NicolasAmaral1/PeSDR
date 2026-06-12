"""E2E reference contract: lead returns post-close → new Talk (FE-03b Task 17)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_inbound_after_close_creates_new_talk(
    async_session,
    run_turn_harness,
    fake_llm_polite,
):
    # First turn — Talk active
    await run_turn_harness.send_inbound("oi")
    await run_turn_harness.run(llm=fake_llm_polite())
    talk1 = await run_turn_harness.talk()
    talk1_id = talk1.id

    # Close the Talk manually
    talk1.status = "closed_inactivity"
    await async_session.commit()

    # Second turn — lead returns
    await run_turn_harness.send_inbound("oi de novo")
    await run_turn_harness.run(llm=fake_llm_polite())
    talk2 = await run_turn_harness.talk()
    assert talk2.id != talk1_id
    assert talk2.status == "active"
