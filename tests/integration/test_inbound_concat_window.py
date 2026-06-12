"""Worker concatenates pending inbounds within a 2s window before run_turn (FE-03a T30, B1).

When a lead sends a burst of messages within ~2s, the worker collapses
them into a single `run_turn` invocation (1 LLM call) instead of N. The
window is configurable via ``WORKER_INBOUND_CONCAT_WINDOW_SECONDS``
(default 2 seconds).

These tests rely on a ``worker_harness`` fixture that wires up a real
TalkflowVersion + Lead + Talk + a fake messaging adapter and exposes
``enqueue_inbound`` / ``process_one`` helpers. The harness hasn't
landed yet — we keep the assertions here as the reference contract and
``pytest.skip`` when fixtures are missing so the file still collects
in CI and on the VPS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio


async def test_two_inbounds_within_window_collapsed_to_one_turn(request):
    """Two inbounds 500ms apart -> one run_turn call seeing both texts."""
    try:
        async_session = request.getfixturevalue("async_session")
        worker_harness = request.getfixturevalue("worker_harness")
        fake_llm_polite = request.getfixturevalue("fake_llm_polite")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T30 — needs worker_harness; integration verification on VPS")

    lead = await worker_harness.lead()
    now = datetime.now(UTC)
    await worker_harness.enqueue_inbound(
        lead=lead,
        text="ok",
        received_at=now,
    )
    await worker_harness.enqueue_inbound(
        lead=lead,
        text="manda link",
        received_at=now + timedelta(milliseconds=500),
    )
    result = await worker_harness.process_one(llm=fake_llm_polite)
    # Single run_turn invocation, both texts visible in inbound payload.
    assert result.run_turn_invocations == 1
    assert "ok" in result.consolidated_text
    assert "manda link" in result.consolidated_text

    _ = async_session


async def test_inbound_outside_window_starts_new_turn(request):
    """Two inbounds 3s apart -> head turn sees only the head text."""
    try:
        async_session = request.getfixturevalue("async_session")
        worker_harness = request.getfixturevalue("worker_harness")
        fake_llm_polite = request.getfixturevalue("fake_llm_polite")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T30 — needs worker_harness; integration verification on VPS")

    lead = await worker_harness.lead()
    now = datetime.now(UTC)
    await worker_harness.enqueue_inbound(lead=lead, text="ok", received_at=now)
    await worker_harness.enqueue_inbound(
        lead=lead,
        text="??",
        received_at=now + timedelta(seconds=3),
    )
    result = await worker_harness.process_one(llm=fake_llm_polite)
    assert result.run_turn_invocations == 1
    assert "ok" in result.consolidated_text
    assert "??" not in result.consolidated_text

    _ = async_session
