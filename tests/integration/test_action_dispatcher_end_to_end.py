"""E2E reference contract: turn collects field → action enqueues → worker executes (FE-03c Task 14).

Skip-friendly: depends on the `run_turn_harness`, `fake_llm_polite` and
`tenant_factory` fixtures that don't exist locally — will skip with
"fixture not found" until the harness module lands (post-FE-03c).
Reference contract documents the expected end-to-end behavior.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_turn_collecting_field_enqueues_action(
    async_session, run_turn_harness, fake_llm_polite, tenant_factory,
):
    """Lead emits demo_data → action_executions row pending → worker → success."""
    fake_llm = fake_llm_polite(
        response_text="Beleza, agendado.",
        collected_fields={"demo_data": "2026-06-13"},
    )
    await run_turn_harness.send_inbound("quarta às 14h")
    await run_turn_harness.run(llm=fake_llm)

    rows = await run_turn_harness.fetch_action_executions()
    assert len(rows) == 1
    assert rows[0]["field"] == "demo_data"
    assert rows[0]["status"] == "success"
    assert rows[0]["external_id"].startswith("fake-schedule_event-")


async def test_same_value_twice_skips_duplicate(
    async_session, run_turn_harness, fake_llm_polite,
):
    """Two turns both emit same demo_data → only 1 action_executions row."""
    fake_llm = fake_llm_polite(
        response_text="ok",
        collected_fields={"demo_data": "2026-06-13"},
    )
    await run_turn_harness.send_inbound("quarta às 14h")
    await run_turn_harness.run(llm=fake_llm)
    await run_turn_harness.send_inbound("confirmado quarta")
    await run_turn_harness.run(llm=fake_llm)

    rows = await run_turn_harness.fetch_action_executions()
    assert len(rows) == 1
