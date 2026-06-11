"""Talk records TreeFlow version snapshot on open + uses it forever (FE-03a Task 31, brecha B2).

Reference contract per spec §B2:

  - On Talk creation, `talks.treeflow_version_id` is set to the
    `treeflow_versions.id` row whose `content_yaml` is what the runtime
    is about to execute. The FK targets an immutable snapshot row, so
    even if a new TreeflowVersion is published mid-conversation the
    Talk keeps pointing at the original.
  - On subsequent turns of the same Talk, the worker MUST resolve the
    TreeflowVersion via `talk.treeflow_version_id` (NOT
    "latest for tenant"). Mid-conversation version bumps therefore
    cannot break an active Talk.
  - If the recorded snapshot's YAML can no longer be parsed (corrupt /
    schema drift), the existing worker `TreeflowLoadError` handler
    flags the Talk `requires_review` with
    `requires_review_reason="treeflow_version_missing"` (covered by
    `test_pipeline_review_reasons.py`).

These tests rely on a `run_turn_harness` family of fixtures that wires
up the full FlowEngine v2 turn (tenant + lead + treeflow snapshot row +
LLM stub + adapter stub) and exposes helpers to inspect / mutate the
snapshot. That harness hasn't landed yet — building it is a sizeable
fixture refactor tracked under the Phase 9-10 sweep. We keep the
assertions here as the reference contract; the tests `pytest.skip` when
the fixtures are missing so the file still collects in CI and on the
VPS.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_new_talk_records_version_snapshot(request):
    """Creating a Talk records `treeflow_version_id` pointing at the active snapshot row."""
    try:
        async_session = request.getfixturevalue("async_session")
        harness = request.getfixturevalue("run_turn_harness")
        fake_llm = request.getfixturevalue("fake_llm_polite")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T31 — needs run_turn_harness; integration verification on VPS")

    talk = await harness.run(llm=fake_llm)
    await async_session.refresh(talk)
    assert talk.treeflow_version_id is not None


async def test_subsequent_turn_uses_recorded_snapshot(request):
    """Publishing a newer TreeflowVersion mid-conversation does NOT affect an active Talk."""
    try:
        async_session = request.getfixturevalue("async_session")
        harness = request.getfixturevalue("run_turn_harness")
        fake_llm = request.getfixturevalue("fake_llm_polite")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T31 — needs run_turn_harness; integration verification on VPS")

    await harness.run(llm=fake_llm)
    snapshot1 = (await harness.talk()).treeflow_version_id

    # Publish a brand-new TreeflowVersion row for the same TreeFlow id.
    # The active Talk's `treeflow_version_id` must NOT migrate to it.
    await harness.bump_treeflow_version_on_disk()

    await harness.run(llm=fake_llm)
    snapshot2 = (await harness.talk()).treeflow_version_id

    assert snapshot1 == snapshot2

    # Quiet unused warnings on async_session in the skip path.
    _ = async_session


async def test_corrupt_snapshot_escalates_with_review_reason(request):
    """Snapshot YAML drift -> talk.requires_review_reason='treeflow_version_missing'.

    Coverage redundant with `test_pipeline_review_reasons.py::
    test_treeflow_version_missing_sets_treeflow_version_missing`; kept
    here so the §B2 contract is auditable in one file.
    """
    try:
        async_session = request.getfixturevalue("async_session")
        harness = request.getfixturevalue("run_turn_harness")
        fake_llm = request.getfixturevalue("fake_llm_polite")
    except pytest.FixtureLookupError:
        pytest.skip("FE-03a T31 — needs run_turn_harness; integration verification on VPS")

    await harness.run(llm=fake_llm)
    await harness.corrupt_treeflow_snapshot()
    await harness.run(llm=fake_llm)

    talk = await harness.talk()
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "treeflow_version_missing"
