"""Compile a `TreeFlow` into a LangGraph `CompiledStateGraph` with KB + guardrails."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.guardrails.runner import GuardrailsRunResult, run_with_guardrails
from ai_sdr.kb.embeddings import Embedder, build_embedder
from ai_sdr.kb.retriever import RetrievedChunk, retrieve
from ai_sdr.llm.extractor import build_structured_model, extract
from ai_sdr.llm.factory import build_llm as _default_build_llm
from ai_sdr.llm.messages import build_system_messages
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.schemas.treeflow_yaml import NodeSpec, TreeFlow
from ai_sdr.treeflow.expressions import eval_bool
from ai_sdr.treeflow.state import Message, TalkFlowState

CLASSIFIER_SUFFIX = "__classifier"
INLINE_SUFFIX = "__inline"

LLMFactory = Callable[[LLMConfig, dict[str, str], str], BaseChatModel]
"""(node_llm_cfg, secrets, current_node_id) -> BaseChatModel.

The `current_node_id` arg is purely for test stubs; the production factory ignores it."""

EmbedderFactory = Callable[[dict[str, str], Any], Awaitable[Embedder]]
"""(secrets, embeddings_cfg) -> Embedder."""

KbSessionFactory = Callable[[], Awaitable[AsyncSession]]
"""() -> AsyncSession. Runtime owns one DB session per step; tests can inject any."""


def _default_llm_factory(cfg: LLMConfig, secrets: dict[str, str], _node_id: str) -> BaseChatModel:
    return _default_build_llm(cfg, secrets)


async def _default_embedder_factory(secrets: dict[str, str], cfg: Any) -> Embedder:
    return build_embedder(secrets, cfg)


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


def _render_kb_block(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        header = f"[{i}] {c.heading_path or '(sem heading)'} (score {c.score:.2f}) [{c.kb_id}]"
        parts.append(f"{header}\n{c.content}")
    return "<knowledge_base>\n" + "\n\n".join(parts) + "\n</knowledge_base>"


def compile_treeflow(
    tf: TreeFlow,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    *,
    guardrails: GuardrailsConfig | None = None,
    tenant_id: uuid.UUID | None = None,
    llm_factory: LLMFactory | None = None,
    embedder_factory: EmbedderFactory | None = None,
    kb_session_factory: KbSessionFactory | None = None,
    checkpointer: Any = None,
) -> Any:
    """Compile a TreeFlow into a LangGraph StateGraph.

    Keyword-only args:
      guardrails: tenant.guardrails block; when None the runner is a passthrough.
      tenant_id, embedder_factory, kb_session_factory: required when any node has
        a non-empty `knowledge_base`; raise ValueError at compile time if missing.
      checkpointer: pass to enable per-thread state persistence.
    """
    llm_fn: LLMFactory = llm_factory or _default_llm_factory
    emb_fn: EmbedderFactory = embedder_factory or _default_embedder_factory

    any_node_has_kb = any(n.knowledge_base for n in tf.nodes)
    if any_node_has_kb:
        if tenant_id is None or kb_session_factory is None:
            raise ValueError(
                "compile_treeflow: tenant_id + kb_session_factory are required "
                "when any node has knowledge_base"
            )
        if tenant_llm.embeddings is None:
            raise ValueError(
                "compile_treeflow: tenant_llm.embeddings is required when any "
                "node has knowledge_base"
            )

    by_id = {n.id: n for n in tf.nodes}

    def _make_node_fn(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        async def node_fn(state: TalkFlowState) -> dict[str, Any]:
            llm_cfg = node.llm or tenant_llm.default
            llm = llm_fn(llm_cfg, secrets, node.id)

            user_input = state.get("last_user_input", "")

            # 1) Retrieve KB chunks (only when node declares KB AND we have input)
            kb_chunks: list[RetrievedChunk] = []
            if node.knowledge_base and user_input:
                assert tenant_id is not None and kb_session_factory is not None
                assert tenant_llm.embeddings is not None
                embedder = await emb_fn(secrets, tenant_llm.embeddings)
                kb_session = await kb_session_factory()
                kb_chunks = await retrieve(
                    kb_session,
                    tenant_id=tenant_id,
                    kb_refs=node.knowledge_base,
                    query=user_input,
                    embedder=embedder,
                )

            dynamic_blocks: list[str] = []
            if kb_chunks:
                dynamic_blocks.append(_render_kb_block(kb_chunks))

            # 2) Build messages with cache control
            system_msgs = build_system_messages(
                static_prompt=node.prompt,
                dynamic_blocks=dynamic_blocks,
                provider=llm_cfg.provider,
                cache_enabled=tenant_llm.cache_enabled,
            )
            history_msgs: list[Any] = []
            for m in state.get("messages", []):
                if m["role"] == "user":
                    history_msgs.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    history_msgs.append(AIMessage(content=m["content"]))

            base_messages: list[Any] = list(system_msgs) + history_msgs
            if user_input:
                base_messages.append(HumanMessage(content=user_input))

            # 3) Build structured model + inner caller
            model = build_structured_model(node.collects, guardrails=guardrails)

            async def _invoke_inner(msgs: list[Any]) -> Any:
                return await extract(llm, model, msgs)

            # 4) Run with guardrails (passthrough when guardrails is None)
            recent_history: list[Message] = state.get("messages", [])[-4:]
            result: GuardrailsRunResult = await run_with_guardrails(
                inner=_invoke_inner,
                base_messages=base_messages,
                guardrails=guardrails,
                critical=node.critical,
                kb_chunks=kb_chunks,
                recent_history=recent_history,
                tenant_llm=tenant_llm,
                secrets=secrets,
                llm_factory=llm_fn,
            )

            collected_after = {**state.get("collected", {}), **result.collected}
            response_text = result.response_text
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

    def _make_passthrough_classifier(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        async def classifier_fn(state: TalkFlowState) -> Command[str]:  # noqa: ARG001
            return Command(goto=node.id)

        return classifier_fn

    sg: StateGraph[Any, Any, Any, Any] = StateGraph(TalkFlowState)
    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))  # type: ignore[call-overload]
        sg.add_node(n.id + CLASSIFIER_SUFFIX, _make_passthrough_classifier(n))  # type: ignore[call-overload]

    def _start_router(state: TalkFlowState) -> str:
        nid = state.get("current_node") or tf.entry_node
        if nid == "END":
            return END
        if nid not in by_id:
            raise ValueError(f"state.current_node={nid!r} not in TreeFlow")
        return nid + CLASSIFIER_SUFFIX

    sg.add_conditional_edges(
        START,
        _start_router,
        {**{n.id + CLASSIFIER_SUFFIX: n.id + CLASSIFIER_SUFFIX for n in tf.nodes}, END: END},
    )
    for n in tf.nodes:
        sg.add_edge(n.id, END)

    if checkpointer is not None:
        return sg.compile(checkpointer=checkpointer)
    return sg.compile()
