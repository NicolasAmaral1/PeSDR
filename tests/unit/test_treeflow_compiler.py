from typing import Any

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.schemas.treeflow_yaml import (
    ExitCondition,
    GlobalObjection,
    NodeObjection,
    NodeSpec,
    Transition,
    TreeFlow,
)
from ai_sdr.treeflow.classifier import ClassifierResult
from ai_sdr.treeflow.compiler import CLASSIFIER_SUFFIX, compile_treeflow
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


def test_start_router_routes_to_classifier_synthetic_node() -> None:
    """After Plan 4a, current_node='na' must route to 'na__classifier'
    in the compiled graph, not directly to 'na'."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="na",
        nodes=[
            NodeSpec(
                id="na",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )
    tenant_llm = LLMDefaults(
        default=LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_ref="secrets/anthropic_key",
        )
    )
    graph = compile_treeflow(tf, tenant_llm, secrets={"anthropic_key": "x"})
    # The compiled graph should know about the synthetic classifier node.
    node_names = set(graph.get_graph().nodes.keys())
    assert "na" + CLASSIFIER_SUFFIX in node_names
    assert "na" in node_names  # main node still exists


# ---------------------------------------------------------------------------
# Plan 4a Task 8 — real classifier logic tests
# ---------------------------------------------------------------------------


def _tenant_llm_with_classifier() -> LLMDefaults:
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


def _tf_with_objection() -> TreeFlow:
    return TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        global_objections=[
            GlobalObjection(
                id="preco",
                kb="kb_obj_preco",
                description="Lead questiona o valor do investimento sempre nessa conversa",
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
                handles_objections=[
                    NodeObjection(
                        id="local_x",
                        kb="kb_local",
                        description="Objeção local que aparece só neste node, descrita bem",
                    )
                ],
            ),
        ],
    )


async def test_classifier_skips_when_no_objections_and_no_globals() -> None:
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="na",
        nodes=[
            NodeSpec(
                id="na",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            )
        ],
    )
    classify_calls: list[Any] = []

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        classify_calls.append(kwargs)
        return ClassifierResult(objection_id=None, confidence=0.0)

    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory({"na": {"response_text": "ok"}}),
    )
    state: TalkFlowState = {
        "tenant_id": "t",
        "lead_id": "l",
        "treeflow_id": "tf",
        "treeflow_version": "1.0.0",
        "current_node": "na",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
        "last_agent_response": "",
        "completed": False,
    }
    await graph.ainvoke(state)
    assert classify_calls == []  # classifier never called — no objections


async def test_classifier_skips_when_disabled_in_tenant_config() -> None:
    tf = _tf_with_objection()
    classify_calls: list[Any] = []

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        classify_calls.append(kwargs)
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="x")

    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=False),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory({"qualif": {"response_text": "ok"}}),
    )
    state: TalkFlowState = {
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
    }
    await graph.ainvoke(state)
    assert classify_calls == []  # kill switch


async def test_classifier_below_threshold_goes_to_main() -> None:
    tf = _tf_with_objection()

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.4, quote="x")

    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True, min_confidence=0.6),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory({"qualif": {"response_text": "ok"}}),
    )
    state: TalkFlowState = {
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
    }
    final = await graph.ainvoke(state)
    assert final.get("objections_handled", []) == []  # no deflect → main ran


async def test_classifier_exception_falls_through_to_main() -> None:
    tf = _tf_with_objection()

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        raise RuntimeError("haiku rate limit")

    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory({"qualif": {"response_text": "ok"}}),
    )
    state: TalkFlowState = {
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
    }
    final = await graph.ainvoke(state)
    assert final.get("objections_handled", []) == []  # exception → no deflect


async def test_classifier_hallucinated_id_falls_through_to_main() -> None:
    tf = _tf_with_objection()

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="nao_existe", confidence=0.9, quote="x")

    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory({"qualif": {"response_text": "ok"}}),
    )
    state: TalkFlowState = {
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
    }
    final = await graph.ainvoke(state)
    assert final.get("objections_handled", []) == []  # hallucinated → fall through


async def test_classifier_dispatches_to_inline_on_detection_above_threshold() -> None:
    """After T9: detection above threshold → __inline runs → response emitted, record appended,
    current_node unchanged, collected untouched."""
    tf = _tf_with_objection()  # 'preco' global has as_subnode=None → inline path

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.85, quote="tá caro")

    # Stub LLM factory must respond when called for the qualif node (inline reuses N's prompt/LLM).
    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True, min_confidence=0.6),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory(
            {
                "qualif": {"response_text": "ok, deixa eu explicar o valor"},
            }
        ),
    )
    state: TalkFlowState = {
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
    }
    final = await graph.ainvoke(state)

    # objections_handled appended
    handled = final.get("objections_handled", [])
    assert len(handled) == 1
    assert handled[0]["objection_id"] == "preco"
    assert handled[0]["detected_at_node"] == "qualif"

    # current_node UNCHANGED — inline doesn't advance
    assert final.get("current_node") == "qualif"

    # collected untouched
    assert final.get("collected", {}) == {}

    # Agent response is the inline response
    assert final.get("last_agent_response") == "ok, deixa eu explicar o valor"

    # Inline never marks the turn as completed — main may, but inline never does.
    assert final.get("completed") is False


# ---------------------------------------------------------------------------
# Plan 4a Task 10 — BACK_TO_ORIGIN resolution in _route
# ---------------------------------------------------------------------------


async def test_back_to_origin_resolves_via_origin_node_id() -> None:
    """Sub-node transitions BACK_TO_ORIGIN: current_node = origin id, _origin_node_id cleared."""
    tf = TreeFlow(
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
                handles_objections=[
                    NodeObjection(
                        id="preco",
                        kb="kb_obj_preco",
                        description="Lead questiona o valor do investimento sempre",
                        as_subnode="obj_preco_node",
                    )
                ],
            ),
            NodeSpec(
                id="obj_preco_node",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="BACK_TO_ORIGIN")],
            ),
        ],
    )

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="tá caro")

    graph = compile_treeflow(
        tf,
        tenant_llm=_tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=_stub_llm_factory(
            {
                "qualif": {"response_text": "qualif answer"},
                "obj_preco_node": {"response_text": "subnode answer"},
            }
        ),
    )

    # Turn 1: classifier detects preco → goto obj_preco_node__classifier
    # → passthrough (obj_preco_node has no objections in scope: it inherits NO globals
    #    because tf.global_objections is empty) → obj_preco_node main
    # → run, exit_condition true → transition BACK_TO_ORIGIN
    # → _route resolves to "qualif", clears _origin_node_id
    state: TalkFlowState = {
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
    }
    final = await graph.ainvoke(state)
    assert final["current_node"] == "qualif"
    assert final.get("_origin_node_id") is None
    handled = final.get("objections_handled", [])
    assert len(handled) == 1
    assert handled[0]["objection_id"] == "preco"
    # Agent response is from the subnode (BACK_TO_ORIGIN is the last action of the subnode's turn)
    assert final["last_agent_response"] == "subnode answer"


async def test_back_to_origin_orphan_falls_back_to_entry_node() -> None:
    """BACK_TO_ORIGIN with _origin_node_id=None falls back to entry_node and warns."""
    import warnings as _warnings

    # TreeFlow validator emits UserWarning for orphan BACK_TO_ORIGIN.
    # Suppress it here — we're testing runtime behaviour, not schema validation.
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", UserWarning)
        tf = TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="na",
            nodes=[
                NodeSpec(
                    id="na",
                    prompt="x",
                    exit_condition=ExitCondition(type="all_fields_filled"),
                    next_nodes=[Transition(condition="true", target="BACK_TO_ORIGIN")],
                ),
            ],
        )

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        llm_factory=_stub_llm_factory({"na": {"response_text": "ok"}}),
    )
    state: TalkFlowState = {
        "tenant_id": "t",
        "lead_id": "l",
        "treeflow_id": "tf",
        "treeflow_version": "1.0.0",
        "current_node": "na",
        "collected": {},
        "messages": [],
        "last_user_input": "oi",
        "last_agent_response": "",
        "completed": False,
        # _origin_node_id intentionally absent
    }
    final = await graph.ainvoke(state)
    # entry_node is "na" → resolution fallback is "na"
    assert final["current_node"] == "na"
