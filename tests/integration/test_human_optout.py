"""Regression: opt-out is honored even when the talk is human-held.

Opt-out detection fires inside resolve_pipeline_context (preprocessing)
BEFORE handle_mode is ever consulted, so a human-held talk that receives
an opt-out keyword must return outcome="opt_out", NOT "skipped_human".
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_optout_wins_over_human_gate(run_turn_human_harness):
    # inbound text is an opt-out keyword; talk is human-held.
    result, adapter, _ = await run_turn_human_harness(handling_mode="human", inbound_text="sair")
    assert result.outcome == "opt_out"  # opt-out wins; NOT skipped_human
