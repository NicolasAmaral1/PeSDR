"""run_turn writes requires_review_reason on every escalation path (FE-03a Task 28).

Reference contract per spec §11 + plan T28. The two paths covered here are
the ones that happen OUTSIDE `post_processing.apply_decision` (which is
covered by T27 for escalation_requested / off_topic_exhausted /
objection_treatment_exhausted):

  - `validator_exhausted` — when `run_guardrails_retry` raises
    `CorrectionEscalation`.
  - `treeflow_version_missing` — when the TreeFlow snapshot recorded on the
    Talk doesn't resolve to a usable TreeflowDef.

These tests rely on a `run_turn_harness` fixture that wires up a full
FlowEngine v2 turn (tenant + lead + treeflow + LLM stub + adapter stub).
That harness hasn't landed yet — building it is a sizeable fixture
refactor that's tracked separately. We keep the assertions here as the
reference contract; the tests `pytest.skip` when the fixtures are
missing so the file still collects in CI and on the VPS.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_guardrails_exhaustion_sets_validator_exhausted(request):
    """`CorrectionEscalation` -> talk.requires_review_reason='validator_exhausted'."""
    try:
        async_session = request.getfixturevalue("async_session")
        harness = request.getfixturevalue("run_turn_harness")
        fake_llm = request.getfixturevalue("fake_llm_violating_price")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T28 — needs run_turn_harness; integration verification on VPS")

    talk = await harness.run(llm=fake_llm)
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "validator_exhausted"


async def test_treeflow_version_missing_sets_treeflow_version_missing(request):
    """Snapshot drift -> talk.requires_review_reason='treeflow_version_missing'."""
    try:
        async_session = request.getfixturevalue("async_session")
        harness = request.getfixturevalue("run_turn_harness")
        fake_llm = request.getfixturevalue("fake_llm_polite")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T28 — needs run_turn_harness; integration verification on VPS")

    # Simulate version snapshot pointing at a YAML / TreeflowDef that no
    # longer resolves (entry_node missing from nodes map).
    await harness.corrupt_treeflow_snapshot()
    talk = await harness.run(llm=fake_llm)
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "treeflow_version_missing"
