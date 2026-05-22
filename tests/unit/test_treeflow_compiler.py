from typing import Any

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.treeflow.compiler import compile_treeflow
from ai_sdr.treeflow.state import TalkFlowState

DEMO_YAML = {
    "id": "demo",
    "version": "0.1.0",
    "display_name": "Demo",
    "entry_node": "saudacao",
    "nodes": [
        {
            "id": "saudacao",
            "prompt": "Cumprimente o lead.",
            "exit_condition": {"type": "all_fields_filled"},
            "next_nodes": [{"condition": "true", "target": "qualificacao"}],
        },
        {
            "id": "qualificacao",
            "prompt": "Pergunte faturamento.",
            "collects": [{"field": "faturamento", "type": "number", "required": True}],
            "exit_condition": {
                "type": "rule_expression",
                "expression": "faturamento != None",
            },
            "next_nodes": [
                {"condition": "faturamento >= 30000", "target": "premium"},
                {"condition": "faturamento < 30000", "target": "basica"},
            ],
        },
        {
            "id": "premium",
            "prompt": "Oferta premium.",
            "exit_condition": {"type": "all_fields_filled"},
            "next_nodes": [{"condition": "true", "target": "END"}],
        },
        {
            "id": "basica",
            "prompt": "Oferta básica.",
            "exit_condition": {"type": "all_fields_filled"},
            "next_nodes": [{"condition": "true", "target": "END"}],
        },
    ],
}

TENANT_LLM = LLMDefaults(
    default=LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_ref="secrets/anthropic_key",
    )
)


def _stub_llm_factory(per_node_responses: dict[str, dict[str, Any]]) -> Any:
    """Returns a callable mimicking build_llm; the stub LLM's with_structured_output
    yields a runnable returning the per-node payload as the requested pydantic model."""

    class _Stub:
        def __init__(self, current_node: str) -> None:
            self._node = current_node

        def with_structured_output(self, model: type[BaseModel]) -> Any:
            payload = per_node_responses[self._node]
            return RunnableLambda(lambda _msgs: model.model_validate(payload))

    def factory(cfg: LLMConfig, secrets: dict[str, str], current_node: str) -> Any:
        return _Stub(current_node)

    return factory


async def test_compiled_graph_runs_one_node_per_turn_and_routes() -> None:
    tf = TreeFlow.model_validate(DEMO_YAML)
    per_node: dict[str, dict[str, Any]] = {
        "saudacao": {"response_text": "Oi! Tudo bem?"},
        "qualificacao": {"response_text": "Anotado: 50k.", "faturamento": 50000},
        "premium": {"response_text": "Te apresento a Mentoria."},
    }
    graph = compile_treeflow(
        tf,
        tenant_llm=TENANT_LLM,
        secrets={"anthropic_key": "fake"},
        llm_factory=_stub_llm_factory(per_node),
    )

    # turn 1: lead arrives, no input yet — engine sends greeting
    state: TalkFlowState = {
        "tenant_id": "t",
        "lead_id": "l",
        "treeflow_id": "demo",
        "treeflow_version": "0.1.0",
        "current_node": "saudacao",
        "collected": {},
        "messages": [],
        "last_user_input": "",
        "last_agent_response": "",
        "completed": False,
    }
    out1 = await graph.ainvoke(state)
    assert out1["last_agent_response"] == "Oi! Tudo bem?"
    assert out1["current_node"] == "qualificacao"  # saudacao has no collects → exit ok → advances
    assert out1["completed"] is False

    # turn 2: lead replies "faturo 50k"
    out1["last_user_input"] = "faturo 50k"
    out2 = await graph.ainvoke(out1)
    assert out2["last_agent_response"] == "Anotado: 50k."
    assert out2["collected"]["faturamento"] == 50000
    assert out2["current_node"] == "premium"  # 50000 >= 30000 → premium

    # turn 3: lead waits for the offer
    out2["last_user_input"] = ""
    out3 = await graph.ainvoke(out2)
    assert out3["last_agent_response"] == "Te apresento a Mentoria."
    assert out3["current_node"] == "END"
    assert out3["completed"] is True


async def test_routes_to_basica_when_faturamento_low() -> None:
    tf = TreeFlow.model_validate(DEMO_YAML)
    per_node = {
        "saudacao": {"response_text": "Oi!"},
        "qualificacao": {"response_text": "Anotado: 5k.", "faturamento": 5000},
        "basica": {"response_text": "Te apresento a Aceleradora."},
    }
    graph = compile_treeflow(
        tf,
        tenant_llm=TENANT_LLM,
        secrets={"anthropic_key": "fake"},
        llm_factory=_stub_llm_factory(per_node),
    )

    state: TalkFlowState = {
        "tenant_id": "t",
        "lead_id": "l",
        "treeflow_id": "demo",
        "treeflow_version": "0.1.0",
        "current_node": "saudacao",
        "collected": {},
        "messages": [],
        "last_user_input": "",
        "last_agent_response": "",
        "completed": False,
    }
    s1 = await graph.ainvoke(state)
    s1["last_user_input"] = "5 mil"
    s2 = await graph.ainvoke(s1)
    assert s2["current_node"] == "basica"


async def test_stays_on_node_when_exit_condition_not_met() -> None:
    tf = TreeFlow.model_validate(DEMO_YAML)
    per_node = {
        "saudacao": {"response_text": "Oi!"},
        # qualificacao receives nothing extractable
        "qualificacao": {"response_text": "Pode repetir?", "faturamento": None},
    }
    graph = compile_treeflow(
        tf,
        tenant_llm=TENANT_LLM,
        secrets={"anthropic_key": "fake"},
        llm_factory=_stub_llm_factory(per_node),
    )
    state: TalkFlowState = {
        "tenant_id": "t",
        "lead_id": "l",
        "treeflow_id": "demo",
        "treeflow_version": "0.1.0",
        "current_node": "saudacao",
        "collected": {},
        "messages": [],
        "last_user_input": "",
        "last_agent_response": "",
        "completed": False,
    }
    s1 = await graph.ainvoke(state)
    s1["last_user_input"] = "sei lá"
    s2 = await graph.ainvoke(s1)
    assert s2["current_node"] == "qualificacao"  # did NOT advance
    assert s2["completed"] is False
