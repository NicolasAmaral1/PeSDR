"""Integration test: compiler runs a TreeFlow whose node has KB + critic with retry."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.llm_yaml import EmbeddingsConfig, LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.settings import get_settings
from ai_sdr.treeflow.compiler import compile_treeflow


class _OneHotEmbedder(Embedder):
    """Deterministic 1-hot vectors so the retriever has predictable similarity."""

    def __init__(self) -> None:
        # Intentionally skip super().__init__ — no LangChain wrapper needed.
        pass

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 1536
        for word in text.lower().split():
            v[hash(word) % 1536] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_query(self, t: str) -> list[float]:
        return self._vec(t)

    async def embed_documents(self, ts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in ts]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


def _tf_yaml() -> dict[str, Any]:
    return {
        "id": "demo",
        "version": "0.1.0",
        "display_name": "Demo",
        "entry_node": "oferta",
        "nodes": [
            {
                "id": "oferta",
                "prompt": "Você apresenta a Mentoria. Use a KB pra preços.",
                "knowledge_base": [{"id": "kb_x", "top_k": 2, "min_score": 0.0}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }


def _llm_defaults() -> LLMDefaults:
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
        embeddings=EmbeddingsConfig(),
        cache_enabled=False,  # avoid asserting on cache_control shape in this test
    )


def _guardrails() -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=True,
        allowed_prices=[6000],
        allowed_products=["Mentoria"],
        fallback_text="Confirmo já já, ok?",
        max_retries=2,
        critic_enabled=False,  # this test isolates whitelist path; critic is its own suite
    )


@pytest.mark.integration
async def test_compiler_injects_kb_and_runs_guardrails_clean(
    session: AsyncSession, tmp_path: Path
) -> None:
    # Seed tenant + KB
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    kb_root = tmp_path / "kb"
    (kb_root / t.slug / "kb_x").mkdir(parents=True)
    (kb_root / t.slug / "kb_x" / "precos.md").write_text(
        "## Preços\n\nMentoria custa R$ 6000.", encoding="utf-8"
    )
    async with session.begin():
        await reindex_tenant_kb(session, t, kb_root, _OneHotEmbedder(), MarkdownChunker())

    tf = TreeFlow.model_validate(_tf_yaml())

    async def fake_call(_messages: list[Any]) -> dict[str, Any]:
        return {
            "response_text": "A Mentoria custa R$ 6000.",
            "prices_mentioned": [6000],
            "products_mentioned": ["Mentoria"],
        }

    class StubLLM:
        def with_structured_output(self, model: type) -> Any:
            async def _run(msgs: list[Any]) -> Any:
                return model.model_validate(await fake_call(msgs))

            return RunnableLambda(_run)

    def llm_factory(_cfg: LLMConfig, _secrets: dict[str, str], _node_id: str) -> Any:
        return StubLLM()

    async def embedder_factory(_secrets: dict[str, str], _cfg: Any) -> Embedder:
        return _OneHotEmbedder()

    async def session_factory() -> AsyncSession:
        return session

    graph = compile_treeflow(
        tf,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk", "openai_key": "sk"},
        guardrails=_guardrails(),
        tenant_id=t.id,
        llm_factory=llm_factory,
        embedder_factory=embedder_factory,
        kb_session_factory=session_factory,
    )

    state_in: dict[str, Any] = {
        "tenant_id": str(t.id),
        "lead_id": "lead-1",
        "treeflow_id": tf.id,
        "treeflow_version": tf.version,
        "current_node": "oferta",
        "collected": {},
        "messages": [],
        "last_user_input": "qual o preço da mentoria?",
        "last_agent_response": "",
        "completed": False,
    }
    out = await graph.ainvoke(state_in)
    assert out["last_agent_response"] == "A Mentoria custa R$ 6000."
    assert out["completed"] is True


@pytest.mark.integration
async def test_compiler_fallback_on_repeated_whitelist_violation(
    session: AsyncSession, tmp_path: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    kb_root = tmp_path / "kb"
    (kb_root / t.slug / "kb_x").mkdir(parents=True)
    (kb_root / t.slug / "kb_x" / "x.md").write_text("## X\n\nmentoria", encoding="utf-8")
    async with session.begin():
        await reindex_tenant_kb(session, t, kb_root, _OneHotEmbedder(), MarkdownChunker())

    tf = TreeFlow.model_validate(_tf_yaml())
    call_count = {"n": 0}

    async def fake_call(_msgs: list[Any]) -> dict[str, Any]:
        call_count["n"] += 1
        return {
            "response_text": "A Mentoria custa R$ 9999.",
            "prices_mentioned": [9999],
            "products_mentioned": ["Mentoria"],
        }

    class StubLLM:
        def with_structured_output(self, model: type) -> Any:
            async def _run(msgs: list[Any]) -> Any:
                return model.model_validate(await fake_call(msgs))

            return RunnableLambda(_run)

    def llm_factory(_cfg: LLMConfig, _secrets: dict[str, str], _node_id: str) -> Any:
        return StubLLM()

    async def embedder_factory(_secrets: dict[str, str], _cfg: Any) -> Embedder:
        return _OneHotEmbedder()

    async def session_factory() -> AsyncSession:
        return session

    graph = compile_treeflow(
        tf,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk", "openai_key": "sk"},
        guardrails=_guardrails(),
        tenant_id=t.id,
        llm_factory=llm_factory,
        embedder_factory=embedder_factory,
        kb_session_factory=session_factory,
    )

    state_in: dict[str, Any] = {
        "tenant_id": str(t.id),
        "lead_id": "lead-1",
        "treeflow_id": tf.id,
        "treeflow_version": tf.version,
        "current_node": "oferta",
        "collected": {},
        "messages": [],
        "last_user_input": "preço?",
        "last_agent_response": "",
        "completed": False,
    }
    out = await graph.ainvoke(state_in)
    assert call_count["n"] == 3  # 1 initial + 2 retries
    assert out["last_agent_response"] == "Confirmo já já, ok?"
