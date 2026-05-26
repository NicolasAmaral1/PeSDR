"""Cross-thread isolation + v1-safety tests for Plan 4a."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
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
                description=(
                    "Lead questiona o valor do investimento ou compara preços com alternativas"
                ),
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda algo curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )


@pytest.mark.integration
async def test_objections_handled_isolated_between_threads() -> None:
    """Different thread_ids do not share objections_handled (the checkpointer keys
    by thread_id, by convention `f'{tenant_id}:{talkflow_id}'`)."""
    await ensure_checkpointer_schema()
    tf = _tf_with_preco()

    async def deflect(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="caro")

    async def no_match(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id=None, confidence=0.0)

    async with checkpointer_from_settings() as saver:
        # Thread A: classifier deflects
        graph_a = compile_treeflow(
            tf,
            tenant_llm=_tenant_llm(),
            secrets={"anthropic_key": "fake"},
            objections=ObjectionsConfig(enabled=True),
            classify_fn=deflect,
            llm_factory=_stub_llm_factory({"qualif": {"response_text": "A"}}),
            checkpointer=saver,
        )
        # Thread B: classifier never matches → main runs
        graph_b = compile_treeflow(
            tf,
            tenant_llm=_tenant_llm(),
            secrets={"anthropic_key": "fake"},
            objections=ObjectionsConfig(enabled=True),
            classify_fn=no_match,
            llm_factory=_stub_llm_factory({"qualif": {"response_text": "B"}}),
            checkpointer=saver,
        )
        config_a = {"configurable": {"thread_id": f"tenant-a:{uuid.uuid4()}"}}
        config_b = {"configurable": {"thread_id": f"tenant-b:{uuid.uuid4()}"}}

        state_a: TalkFlowState = {
            "tenant_id": "t",
            "lead_id": "l",
            "treeflow_id": "tf",
            "treeflow_version": "1.0.0",
            "current_node": "qualif",
            "collected": {},
            "messages": [],
            "last_user_input": "caro",
            "last_agent_response": "",
            "completed": False,
        }
        state_b: TalkFlowState = {
            "tenant_id": "t",
            "lead_id": "l",
            "treeflow_id": "tf",
            "treeflow_version": "1.0.0",
            "current_node": "qualif",
            "collected": {},
            "messages": [],
            "last_user_input": "ok",
            "last_agent_response": "",
            "completed": False,
        }

        final_a = await graph_a.ainvoke(state_a, config=config_a)
        final_b = await graph_b.ainvoke(state_b, config=config_b)

        assert len(final_a.get("objections_handled", [])) == 1
        assert len(final_b.get("objections_handled", [])) == 0


@pytest.mark.integration
async def test_v1_treeflow_without_objections_never_calls_classifier() -> None:
    """A TreeFlow without any objections (v1-style) never invokes the classifier
    even when tenant.objections.enabled=True. Plan 4a invariant: zero cost
    for TreeFlows that haven't opted in to objections."""
    await ensure_checkpointer_schema()

    tf_v1 = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )

    classify_calls: list[Any] = []

    async def tracked_classify(**kwargs: Any) -> ClassifierResult:
        classify_calls.append(kwargs)
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="caro")

    async with checkpointer_from_settings() as saver:
        graph = compile_treeflow(
            tf_v1,
            tenant_llm=_tenant_llm(),
            secrets={"anthropic_key": "fake"},
            objections=ObjectionsConfig(enabled=True),  # tenant has it on …
            classify_fn=tracked_classify,
            llm_factory=_stub_llm_factory({"qualif": {"response_text": "ok"}}),
            checkpointer=saver,
        )
        config = {"configurable": {"thread_id": f"v1-no-obj:{uuid.uuid4()}"}}

        await graph.ainvoke(
            {
                "tenant_id": "t",
                "lead_id": "l",
                "treeflow_id": "tf",
                "treeflow_version": "1.0.0",
                "current_node": "qualif",
                "collected": {},
                "messages": [],
                "last_user_input": "tá caro",
                "last_agent_response": "",
                "completed": False,
            },
            config=config,
        )
        # … but TreeFlow has no objections → never called.
        assert classify_calls == []
