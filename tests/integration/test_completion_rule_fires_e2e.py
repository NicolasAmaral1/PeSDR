"""E2E reference contract: completion rule closes Talk (FE-03b Task 17)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_collected_field_triggers_completion_close(
    async_session,
    run_turn_harness,
    fake_chat_scripted,
):
    llm = fake_chat_scripted(
        [
            {
                "response_text": "Maravilha!",
                "collected_fields": {"demo_agendada": True},
                "reasoning": "r",
            }
        ]
    )
    await run_turn_harness.send_inbound("ok, agenda")
    await run_turn_harness.run(llm=llm)
    talk = await run_turn_harness.talk()
    await async_session.refresh(talk)
    assert talk.status == "closed_completed_success"
    assert talk.closed_by == "pipeline_hook"
