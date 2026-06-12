"""run_turn rolls back state on send failure (FE-03a Task 29 §9.1).

If `adapter.send_text` raises, the state mutations from this turn must
NOT persist. The worker can then safely retry the inbound — it will see
the same pre-turn state and re-run the pipeline.

This test relies on the same `run_turn_harness` family of fixtures used
by T28's `test_pipeline_review_reasons.py`. Those fixtures haven't
landed yet — we keep the assertions here as the reference contract and
`pytest.skip` when fixtures are missing so the file still collects in
CI and on the VPS.
"""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import TransientError

pytestmark = pytest.mark.asyncio


async def test_send_failure_rolls_back_state(request):
    """adapter.send_text raise -> no state changes from this turn persist."""
    try:
        async_session = request.getfixturevalue("async_session")
        harness = request.getfixturevalue("run_turn_harness")
        fake_llm = request.getfixturevalue("fake_llm_polite")
        raising_adapter = request.getfixturevalue("raising_adapter")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T29 — needs run_turn_harness; integration verification on VPS")

    talk_before = await harness.talk()
    state_before = await harness.state()
    state_before_node = state_before.current_node
    turn_count_before = talk_before.turn_count

    with pytest.raises(TransientError):
        await harness.run(llm=fake_llm, adapter=raising_adapter)

    # New session — verify nothing committed.
    async with harness.new_session() as fresh:
        talk_after = await fresh.get(type(talk_before), talk_before.id)
        state_after = await harness.state_for_session(fresh, talk_before.id)
        assert talk_after.turn_count == turn_count_before
        assert state_after.current_node == state_before_node

    # Quiet the unused warning on async_session in the skip path.
    _ = async_session
