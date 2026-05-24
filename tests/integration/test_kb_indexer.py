"""Integration tests for reindex_tenant_kb — idempotent indexer."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.kb.indexer import IndexResult, reindex_tenant_kb
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


class _FakeEmbedder(Embedder):
    """Returns deterministic per-text vectors so we never call OpenAI."""

    def __init__(self) -> None:
        # Intentionally skip super().__init__ — we don't need the LangChain wrapper.
        self.calls = 0

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 1536

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(hash(t) % 100) / 100.0] * 1536 for t in texts]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    return tmp_path / "kb"


def _write_md(root: Path, tenant_slug: str, kb_id: str, name: str, body: str) -> Path:
    p = root / tenant_slug / kb_id / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.integration
async def test_indexer_creates_documents_and_chunks(session: AsyncSession, kb_root: Path) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(
        kb_root,
        t.slug,
        "kb_x",
        "precos.md",
        "## Preços\n\nMentoria custa R$ 6000.\n\n## Garantia\n\n7 dias.",
    )

    result = await reindex_tenant_kb(
        session, t, kb_root, embedder=_FakeEmbedder(), chunker=MarkdownChunker()
    )

    assert isinstance(result, IndexResult)
    assert len(result.indexed) == 1
    assert result.skipped == [] and result.failed == [] and result.pruned == []

    async with session.begin():
        await set_tenant_context(session, t.id)
        docs = (await session.execute(select(KbDocument))).scalars().all()
        assert len(docs) == 1
        chunks = (await session.execute(select(KbChunk))).scalars().all()
        assert len(chunks) == 2  # one per section


@pytest.mark.integration
async def test_indexer_is_idempotent_when_content_unchanged(
    session: AsyncSession, kb_root: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(kb_root, t.slug, "kb_x", "a.md", "## A\n\nbody")

    embedder = _FakeEmbedder()
    first = await reindex_tenant_kb(session, t, kb_root, embedder, MarkdownChunker())
    assert len(first.indexed) == 1 and embedder.calls == 1

    second = await reindex_tenant_kb(session, t, kb_root, embedder, MarkdownChunker())
    assert second.indexed == [] and len(second.skipped) == 1
    assert embedder.calls == 1  # no re-embedding


@pytest.mark.integration
async def test_indexer_reindexes_when_content_changes(session: AsyncSession, kb_root: Path) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    p = _write_md(kb_root, t.slug, "kb_x", "a.md", "## A\n\nold body")
    await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())

    p.write_text("## A\n\nNEW body with more content for chunking", encoding="utf-8")
    second = await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())
    assert len(second.indexed) == 1

    async with session.begin():
        await set_tenant_context(session, t.id)
        chunks = (await session.execute(select(KbChunk))).scalars().all()
        # old chunks were deleted; new ones inserted
        assert all("NEW body" in c.content for c in chunks)


@pytest.mark.integration
async def test_indexer_prune_removes_deleted_docs(session: AsyncSession, kb_root: Path) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(kb_root, t.slug, "kb_x", "a.md", "## A\n\nbody1")
    p2 = _write_md(kb_root, t.slug, "kb_x", "b.md", "## B\n\nbody2")
    await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())

    p2.unlink()
    result = await reindex_tenant_kb(
        session, t, kb_root, _FakeEmbedder(), MarkdownChunker(), prune=True
    )
    assert any("b.md" in path for path in result.pruned)

    async with session.begin():
        await set_tenant_context(session, t.id)
        docs = (await session.execute(select(KbDocument))).scalars().all()
        assert len(docs) == 1
        assert "a.md" in docs[0].doc_path


@pytest.mark.integration
async def test_indexer_kb_id_filter(session: AsyncSession, kb_root: Path) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(kb_root, t.slug, "kb_a", "x.md", "## X\n\nA")
    _write_md(kb_root, t.slug, "kb_b", "y.md", "## Y\n\nB")

    result = await reindex_tenant_kb(
        session, t, kb_root, _FakeEmbedder(), MarkdownChunker(), kb_id="kb_a"
    )
    assert len(result.indexed) == 1
    assert "kb_a" in result.indexed[0]

    async with session.begin():
        await set_tenant_context(session, t.id)
        docs = (await session.execute(select(KbDocument))).scalars().all()
        kb_ids = sorted(d.kb_id for d in docs)
        assert kb_ids == ["kb_a"]


@pytest.mark.integration
async def test_indexer_skips_invalid_utf8(session: AsyncSession, kb_root: Path) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    p = kb_root / t.slug / "kb_x" / "bad.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe\x00\x00not utf-8")
    _write_md(kb_root, t.slug, "kb_x", "good.md", "## G\n\nok")

    result = await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())
    assert any("bad.md" in path for path, _ in result.failed)
    assert any("good.md" in path for path in result.indexed)
