"""Integration test: full turn cycle with mocked classifier (Plan 4a).

Uses the real Postgres checkpointer; mocks the classifier LLM. Verifies that:
1. Detection above threshold deflects to __inline, appends ObjectionRecord,
   keeps current_node unchanged.
2. The next turn re-enters the classifier (because current_node stayed).
3. max_handled_per_lead emits the warning event when exceeded.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog.testing
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.schemas.treeflow_yaml import (
    ExitCondition,
    GlobalObjection,
    NodeSpec,
    Transition,
    TreeFlow,
)
from ai_sdr.treeflow.checkpointer import (
    checkpointer_from_settings,
    ensure_checkpointer_schema,
)
from ai_sdr.treeflow.classifier import ClassifierResult
from ai_sdr.treeflow.compiler import compile_treeflow
from ai_sdr.treeflow.state import TalkFlowState


def _tenant_llm() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_ref="secrets/anthropic_key",
        ),
        classifier=LLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5",
            api_key_ref="secrets/anthropic_key",
        ),
    )


def _stub_llm_factory(per_node_responses: dict[str, dict[str, Any]]) -> Any:
    """Mirror of the unit-test stub: per-node payload returned from with_structured_output."""

    class _Stub:
        def __init__(self, current_node: str) -> None:
            self._node = current_node

        def with_structured_output(self, model: type[BaseModel]) -> Any:
            payload = per_node_responses[self._node]
            return RunnableLambda(lambda _msgs: model.model_validate(payload))

    def factory(cfg: LLMConfig, secrets: dict[str, str], current_node: str) -> Any:
        return _Stub(current_node)

    return factory


def _tf_with_preco() -> TreeFlow:
    return TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        global_objections=[
            GlobalObjection(
                id="preco",
                kb="kb_obj_preco",
                description="Lead questiona o valor do investimento ou compara com alternativas",
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )


@pytest.mark.integration
async def test_inline_objection_appends_record_and_does_not_advance() -> None:
    """Turn 1 deflects via classifier; turn 2 no-matches and advances via main."""
    await ensure_checkpointer_schema()
    tf = _tf_with_preco()

    async def deflect(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.85, quote="tá caro")

    async with checkpointer_from_settings() as saver:
        graph_t1 = compile_treeflow(
            tf,
            tenant_llm=_tenant_llm(),
            secrets={"anthropic_key": "fake"},
            objections=ObjectionsConfig(enabled=True),
            classify_fn=deflect,
            llm_factory=_stub_llm_factory({"qualif": {"response_text": "explico já"}}),
            checkpointer=saver,
        )

        thread_id = f"test-tenant:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}

        # Turn 1 — inline deflect
        state_in: TalkFlowState = {
            "tenant_id": "t",
            "lead_id": "l",
            "treeflow_id": "tf",
            "treeflow_version": "1.0.0",
            "current_node": "qualif",
            "collected": {},
            "messages": [],
            "last_user_input": "tá muito caro",
            "last_agent_response": "",
            "completed": False,
        }
        final1 = await graph_t1.ainvoke(state_in, config=config)
        handled = final1.get("objections_handled", [])
        assert len(handled) == 1
        assert handled[0]["objection_id"] == "preco"
        assert final1["current_node"] == "qualif"
        assert final1["last_agent_response"] == "explico já"
        assert final1.get("completed") is False

        # Turn 2 — no match, main runs and advances to END
        # (TreeFlow has only 1 node which then goes END)
        async def no_match(**kwargs: Any) -> ClassifierResult:
            return ClassifierResult(objection_id=None, confidence=0.0)

        graph_t2 = compile_treeflow(
            tf,
            tenant_llm=_tenant_llm(),
            secrets={"anthropic_key": "fake"},
            objections=ObjectionsConfig(enabled=True),
            classify_fn=no_match,
            llm_factory=_stub_llm_factory({"qualif": {"response_text": "ok, fechado"}}),
            checkpointer=saver,
        )
        # Pass only last_user_input — checkpointer restores current_node, messages, etc.
        final2 = await graph_t2.ainvoke({"last_user_input": "fechado"}, config=config)
        handled2 = final2.get("objections_handled", [])
        assert len(handled2) == 1  # no new objection appended this turn
        assert final2["current_node"] == "END"  # qualif's exit_condition + transition target=END
        assert final2.get("completed") is True


@pytest.mark.integration
async def test_max_handled_threshold_emits_warning() -> None:
    """Crossing max_handled_per_lead emits objection.threshold.exceeded warning."""
    await ensure_checkpointer_schema()
    tf = _tf_with_preco()

    async def always_preco(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="caro")

    async with checkpointer_from_settings() as saver:
        graph = compile_treeflow(
            tf,
            tenant_llm=_tenant_llm(),
            secrets={"anthropic_key": "fake"},
            objections=ObjectionsConfig(enabled=True, max_handled_per_lead=2),
            classify_fn=always_preco,
            llm_factory=_stub_llm_factory({"qualif": {"response_text": "respondendo"}}),
            checkpointer=saver,
        )

        config = {"configurable": {"thread_id": f"test-threshold:{uuid.uuid4()}"}}
        # 3 deflecting turns → threshold (2) crossed on turn 3.
        with structlog.testing.capture_logs() as log_entries:
            for i in range(3):
                state: TalkFlowState = {
                    "tenant_id": "t",
                    "lead_id": "l",
                    "treeflow_id": "tf",
                    "treeflow_version": "1.0.0",
                    "current_node": "qualif",
                    "collected": {},
                    "messages": [],
                    "last_user_input": f"caro {i}",
                    "last_agent_response": "",
                    "completed": False,
                }
                await graph.ainvoke(state, config=config)

        threshold_warnings = [
            e for e in log_entries if e.get("event") == "objection.threshold.exceeded"
        ]
        assert len(threshold_warnings) >= 1
