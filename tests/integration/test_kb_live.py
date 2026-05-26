"""Live KB test — uses real OpenAI embeddings. Requires OPENAI_API_KEY.

Skipped by default (live_llm marker). Run explicitly:
    uv run pytest tests/integration/test_kb_live.py -v -m live_llm
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import build_embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.kb.retriever import retrieve
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.llm_yaml import EmbeddingsConfig
from ai_sdr.schemas.treeflow_yaml import KBRef
from ai_sdr.settings import get_settings

pytestmark = [pytest.mark.live_llm, pytest.mark.integration]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_live_embed_and_retrieve_finds_relevant_chunk(
    session: AsyncSession, tmp_path: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="LiveT")
        session.add(t)
        await session.flush()

    kb_root = tmp_path / "kb"
    (kb_root / t.slug / "kb_x").mkdir(parents=True)
    (kb_root / t.slug / "kb_x" / "precos.md").write_text(
        "## Mentoria\n\nA Mentoria custa R$ 6000 à vista.\n\n"
        "## Bonus\n\nComunidade fechada com mais de 200 alunas.",
        encoding="utf-8",
    )

    secrets = {"openai_key": os.environ["OPENAI_API_KEY"]}
    embedder = build_embedder(secrets, EmbeddingsConfig())

    async with session.begin():
        await reindex_tenant_kb(session, t, kb_root, embedder, MarkdownChunker())

    chunks = await retrieve(
        session,
        tenant_id=t.id,
        kb_refs=[KBRef(id="kb_x", top_k=2, min_score=0.0)],
        query="quanto custa a mentoria?",
        embedder=embedder,
    )
    assert chunks, "expected at least one chunk back from live retrieval"
    # The Mentoria chunk should rank above Bonus for this query
    top = chunks[0]
    assert top.heading_path is not None and "Mentoria" in top.heading_path
    assert top.score > 0.3, f"unexpectedly low score: {top.score}"
