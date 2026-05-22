"""Compile a `TreeFlow` into a LangGraph `CompiledStateGraph`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ai_sdr.llm.extractor import RESPONSE_FIELD, build_structured_model, extract
from ai_sdr.llm.factory import build_llm as _default_build_llm
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.treeflow_yaml import NodeSpec, TreeFlow
from ai_sdr.treeflow.expressions import eval_bool
from ai_sdr.treeflow.state import Message, TalkFlowState

LLMFactory = Callable[[LLMConfig, dict[str, str], str], BaseChatModel]
"""(node_llm_cfg, secrets, current_node_id) -> BaseChatModel.

The `current_node_id` arg is purely for test stubs; the production factory ignores it."""


def _default_factory(cfg: LLMConfig, secrets: dict[str, str], _node_id: str) -> BaseChatModel:
    return _default_build_llm(cfg, secrets)


def _exit_satisfied(node: NodeSpec, collected: dict[str, Any]) -> bool:
    ec = node.exit_condition
    if ec.type == "all_fields_filled":
        return all(
            c.field in collected and collected[c.field] is not None
            for c in node.collects
            if c.required
        )
    if ec.type == "rule_expression":
        assert ec.expression is not None
        return eval_bool(ec.expression, collected)
    if ec.type == "combined":
        assert ec.expression is not None
        all_filled = all(
            c.field in collected and collected[c.field] is not None
            for c in node.collects
            if c.required
        )
        return all_filled and eval_bool(ec.expression, collected)
    return False


def _route(node: NodeSpec, collected: dict[str, Any]) -> tuple[str, bool]:
    """Return (next_current_node, completed)."""
    if not _exit_satisfied(node, collected):
        return (node.id, False)
    for tr in node.next_nodes:
        if eval_bool(tr.condition, collected):
            if tr.target == "END":
                return ("END", True)
            return (tr.target, False)
    # nothing matched — stay (operator pebcak, but don't crash)
    return (node.id, False)


def compile_treeflow(
    tf: TreeFlow,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    llm_factory: LLMFactory | None = None,
    checkpointer: Any = None,
) -> Any:
    """Compile a TreeFlow into a LangGraph StateGraph.

    Pass `checkpointer` to enable per-thread state persistence.
    """
    factory: LLMFactory = llm_factory or _default_factory
    by_id = {n.id: n for n in tf.nodes}

    def _make_node_fn(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        async def node_fn(state: TalkFlowState) -> dict[str, Any]:
            llm_cfg = node.llm or tenant_llm.default
            llm = factory(llm_cfg, secrets, node.id)

            messages: list[Any] = [SystemMessage(content=node.prompt)]
            for m in state.get("messages", []):
                if m["role"] == "user":
                    messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    messages.append(AIMessage(content=m["content"]))
            user_input = state.get("last_user_input", "")
            if user_input:
                messages.append(HumanMessage(content=user_input))

            model = build_structured_model(node.collects)
            result = await extract(llm, model, messages)

            extracted: dict[str, Any] = {}
            for c in node.collects:
                val = getattr(result, c.field, None)
                if val is not None:
                    extracted[c.field] = val
            collected_after = {**state.get("collected", {}), **extracted}
            response_text: str = getattr(result, RESPONSE_FIELD)

            next_node, completed = _route(node, collected_after)

            new_msgs: list[Message] = []
            if user_input:
                new_msgs.append({"role": "user", "content": user_input})
            new_msgs.append({"role": "assistant", "content": response_text})

            return {
                "collected": collected_after,
                "messages": new_msgs,
                "last_agent_response": response_text,
                "last_user_input": "",  # consumed
                "current_node": next_node,
                "completed": completed,
            }

        return node_fn

    # Build graph: START → router → <picked node> → END
    sg: StateGraph[Any, Any, Any, Any] = StateGraph(TalkFlowState)

    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))  # type: ignore[call-overload]

    def _start_router(state: TalkFlowState) -> str:
        nid = state.get("current_node") or tf.entry_node
        if nid == "END":
            return END
        if nid not in by_id:
            raise ValueError(f"state.current_node={nid!r} not in TreeFlow")
        return nid

    sg.add_conditional_edges(
        START,
        _start_router,
        {**{n.id: n.id for n in tf.nodes}, END: END},
    )
    for n in tf.nodes:
        sg.add_edge(n.id, END)

    if checkpointer is not None:
        return sg.compile(checkpointer=checkpointer)
    return sg.compile()
