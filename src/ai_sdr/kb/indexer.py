"""Idempotent KB indexer — walks kb_root/<slug>/[<kb_id>]/**/*.md, hashes,
re-chunks + re-embeds only changed docs. Spec §4.3."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant

logger = structlog.get_logger(__name__)


@dataclass
class IndexResult:
    indexed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _list_md_files(kb_root: Path, slug: str, kb_id: str | None) -> list[Path]:
    base = kb_root / slug
    if not base.exists():
        return []
    if kb_id is not None:
        glob_base = base / kb_id
        if not glob_base.exists():
            return []
        return sorted(glob_base.rglob("*.md"))
    return sorted(base.rglob("*.md"))


def _rel_doc_path(kb_root: Path, fs_path: Path) -> str:
    """Return the path relative to the repo root (kb_root.parent) as a stable string."""
    try:
        return str(fs_path.relative_to(kb_root.parent))
    except ValueError:
        return str(fs_path)


def _kb_id_from_path(kb_root: Path, slug: str, fs_path: Path) -> str:
    """First path component under kb_root/<slug>/."""
    rel = fs_path.relative_to(kb_root / slug)
    return rel.parts[0]


async def reindex_tenant_kb(
    session: AsyncSession,
    tenant: Tenant,
    kb_root: Path,
    embedder: Embedder,
    chunker: MarkdownChunker,
    prune: bool = False,
    kb_id: str | None = None,
) -> IndexResult:
    """Reindex one tenant's KB tree. Idempotent via content_hash.

    If ``kb_id`` is given, only that KB is touched (and pruning is scoped to it).

    Callers must own the transaction — wrap calls in ``async with session.begin():``.
    """
    result = IndexResult()
    await set_tenant_context(session, tenant.id)

    fs_files = _list_md_files(kb_root, tenant.slug, kb_id)
    fs_paths_rel = {_rel_doc_path(kb_root, p) for p in fs_files}

    # Index/upsert
    for fs_path in fs_files:
        doc_rel = _rel_doc_path(kb_root, fs_path)
        try:
            content = fs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            logger.error("kb.failed", tenant=tenant.slug, doc=doc_rel, error=str(e))
            result.failed.append((doc_rel, f"unicode_decode_error: {e}"))
            continue

        digest = _hash(content)
        doc_kb_id = _kb_id_from_path(kb_root, tenant.slug, fs_path)

        existing = (
            await session.execute(
                select(KbDocument).where(
                    KbDocument.tenant_id == tenant.id,
                    KbDocument.kb_id == doc_kb_id,
                    KbDocument.doc_path == doc_rel,
                )
            )
        ).scalar_one_or_none()

        if existing is not None and existing.content_hash == digest:
            logger.info(
                "kb.skipped",
                tenant=tenant.slug,
                kb_id=doc_kb_id,
                doc=doc_rel,
                reason="hash_unchanged",
            )
            result.skipped.append(doc_rel)
            continue

        t0 = time.perf_counter()
        drafts = chunker.split(content)
        if not drafts:
            logger.warning("kb.empty_doc", tenant=tenant.slug, doc=doc_rel)
            # still index the document row (empty body); no chunks
            embeddings: list[list[float]] = []
        else:
            embeddings = await embedder.embed_documents([d.content for d in drafts])

        if existing is None:
            doc = KbDocument(
                tenant_id=tenant.id,
                kb_id=doc_kb_id,
                doc_path=doc_rel,
                content_hash=digest,
                content_md=content,
            )
            session.add(doc)
            await session.flush()
        else:
            existing.content_hash = digest
            existing.content_md = content
            await session.execute(delete(KbChunk).where(KbChunk.document_id == existing.id))
            await session.flush()
            doc = existing

        for draft, emb in zip(drafts, embeddings, strict=True):
            session.add(
                KbChunk(
                    document_id=doc.id,
                    tenant_id=tenant.id,
                    kb_id=doc_kb_id,
                    chunk_idx=draft.idx,
                    heading_path=draft.heading_path,
                    content=draft.content,
                    token_count=draft.token_count,
                    embedding=emb,
                )
            )
        await session.flush()

        took_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "kb.indexed",
            tenant=tenant.slug,
            kb_id=doc_kb_id,
            doc=doc_rel,
            chunks=len(drafts),
            took_ms=took_ms,
        )
        result.indexed.append(doc_rel)

    # Prune
    if prune:
        q = select(KbDocument).where(KbDocument.tenant_id == tenant.id)
        if kb_id is not None:
            q = q.where(KbDocument.kb_id == kb_id)
        db_docs = (await session.execute(q)).scalars().all()
        for d in db_docs:
            if d.doc_path not in fs_paths_rel:
                await session.execute(delete(KbDocument).where(KbDocument.id == d.id))
                logger.info("kb.pruned", tenant=tenant.slug, kb_id=d.kb_id, doc=d.doc_path)
                result.pruned.append(d.doc_path)
        await session.flush()

    return result
