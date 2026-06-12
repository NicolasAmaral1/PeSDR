"""apply_decision invokes dispatch_actions when node.on_collected is non-empty (FE-03c Task 13)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_apply_decision_calls_dispatch_actions_when_on_collected_present():
    """apply_decision must invoke dispatch_actions after merging collected_fields."""
    with patch(
        "ai_sdr.flowengine.post_processing.flag_modified",
    ), patch(
        "ai_sdr.flowengine.post_processing.dispatch_actions",
        AsyncMock(),
    ) as mock_dispatch, patch(
        "ai_sdr.flowengine.post_processing.apply_objection_state",
        return_value=SimpleNamespace(
            changes_treatment=False,
            new_active_treatment=None,
            appended_objection_history=[],
            events=[],
            requires_review_reason=None,
        ),
    ), patch(
        "ai_sdr.flowengine.post_processing.apply_contradiction_heuristic",
        side_effect=lambda d: (d, []),
    ), patch(
        "ai_sdr.flowengine.post_processing.detect_implicit_transition",
        return_value=[],
    ), patch(
        "ai_sdr.flowengine.post_processing.evaluate_completion_rule",
        return_value=None,
    ), patch(
        "ai_sdr.flowengine.post_processing.handle_offtopic",
        return_value=(0, None),
    ), patch(
        "ai_sdr.flowengine.post_processing.resolve_escalation_reason",
        return_value=None,
    ), patch(
        "ai_sdr.flowengine.post_processing.TalkFlowStateRepository"
    ) as MockRepo, patch(
        "ai_sdr.flowengine.post_processing._emit_events"
    ), patch(
        "ai_sdr.flowengine.post_processing._load_lead_for_actions",
        AsyncMock(return_value=SimpleNamespace(id=uuid4(), whatsapp_e164="+1", external_label="x")),
    ):
        MockRepo.return_value.append_message = AsyncMock()

        from ai_sdr.flowengine.post_processing import apply_decision

        node = SimpleNamespace(
            id="n1",
            on_collected=[
                SimpleNamespace(field="x", adapter="logging", handler="h", params={})
            ],
        )
        treeflow = SimpleNamespace(
            nodes={"n1": node},
            talk_lifecycle=None,
        )
        state = SimpleNamespace(
            collected={},
            extracted_facts={},
            current_node="n1",
            active_treatment=None,
            objections_handled=[],
        )
        decision = SimpleNamespace(
            collected_fields={"x": 1},
            extracted_facts={},
            response_text="ok",
            response_format="text",
            next_node=None,
            objection=None,
            objection_resolved=False,
            off_topic=False,
            escalation_reason=None,
            suggest_close_talk="no",
        )
        talk = SimpleNamespace(
            id=uuid4(),
            tenant_id=uuid4(),
            lead_id=uuid4(),
            treeflow_id="tf",
            turn_count=0,
            last_message_at=None,
            status="active",
            closed_at=None,
            closed_reason=None,
            closed_by=None,
            requires_review_reason=None,
        )

        session = MagicMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        await apply_decision(
            session,
            talk=talk,
            state=state,
            decision=decision,
            resolved_target_node="n1",
            now=datetime.now(UTC),
            treeflow=treeflow,
        )

    mock_dispatch.assert_awaited_once()
    kwargs = mock_dispatch.await_args.kwargs
    assert kwargs["state"] is state
    assert kwargs["decision"] is decision
    assert kwargs["node_spec"].id == "n1"


@pytest.mark.asyncio
async def test_apply_decision_skips_dispatch_when_no_on_collected():
    """No on_collected on node → dispatch_actions NOT invoked (perf optimization)."""
    with patch(
        "ai_sdr.flowengine.post_processing.flag_modified",
    ), patch(
        "ai_sdr.flowengine.post_processing.dispatch_actions",
        AsyncMock(),
    ) as mock_dispatch, patch(
        "ai_sdr.flowengine.post_processing.apply_objection_state",
        return_value=SimpleNamespace(
            changes_treatment=False,
            new_active_treatment=None,
            appended_objection_history=[],
            events=[],
            requires_review_reason=None,
        ),
    ), patch(
        "ai_sdr.flowengine.post_processing.apply_contradiction_heuristic",
        side_effect=lambda d: (d, []),
    ), patch(
        "ai_sdr.flowengine.post_processing.detect_implicit_transition",
        return_value=[],
    ), patch(
        "ai_sdr.flowengine.post_processing.evaluate_completion_rule",
        return_value=None,
    ), patch(
        "ai_sdr.flowengine.post_processing.handle_offtopic",
        return_value=(0, None),
    ), patch(
        "ai_sdr.flowengine.post_processing.resolve_escalation_reason",
        return_value=None,
    ), patch(
        "ai_sdr.flowengine.post_processing.TalkFlowStateRepository"
    ) as MockRepo, patch(
        "ai_sdr.flowengine.post_processing._emit_events"
    ):
        MockRepo.return_value.append_message = AsyncMock()

        from ai_sdr.flowengine.post_processing import apply_decision

        node = SimpleNamespace(id="n1", on_collected=[])
        treeflow = SimpleNamespace(nodes={"n1": node}, talk_lifecycle=None)
        state = SimpleNamespace(
            collected={},
            extracted_facts={},
            current_node="n1",
            active_treatment=None,
            objections_handled=[],
        )
        decision = SimpleNamespace(
            collected_fields={"x": 1},
            extracted_facts={},
            response_text="ok",
            response_format="text",
            next_node=None,
            objection=None,
            objection_resolved=False,
            off_topic=False,
            escalation_reason=None,
            suggest_close_talk="no",
        )
        talk = SimpleNamespace(
            id=uuid4(),
            tenant_id=uuid4(),
            lead_id=uuid4(),
            treeflow_id="tf",
            turn_count=0,
            last_message_at=None,
            status="active",
            closed_at=None,
            closed_reason=None,
            closed_by=None,
            requires_review_reason=None,
        )

        session = MagicMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        await apply_decision(
            session,
            talk=talk,
            state=state,
            decision=decision,
            resolved_target_node="n1",
            now=datetime.now(UTC),
            treeflow=treeflow,
        )

    mock_dispatch.assert_not_awaited()
