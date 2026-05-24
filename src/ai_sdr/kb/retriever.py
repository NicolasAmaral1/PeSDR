"""KB retriever — embed query, query pgvector, filter by min_score per KBRef.

Spec §4.4. Logs `kb.retrieved` / `kb.no_match` / `kb.embed_error`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.schemas.treeflow_yaml import KBRef

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    heading_path: str | None
    kb_id: str
    score: float


def _vec_to_pg_literal(vec: list[float]) -> str:
    """pgvector expects a string like '[0.1,0.2,...]' when passed via text()."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def retrieve(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kb_refs: list[KBRef],
    query: str,
    embedder: Embedder,
) -> list[RetrievedChunk]:
    """Retrieve top-k chunks across kb_refs, filtered by each ref's min_score."""
    if not kb_refs:
        return []

    await set_tenant_context(session, tenant_id)

    try:
        query_vec = await embedder.embed_query(query)
    except Exception as e:  # noqa: BLE001 — never let retrieval failure nuke a turn
        logger.error("kb.embed_error", tenant=str(tenant_id), error=str(e))
        return []

    kb_ids = [ref.id for ref in kb_refs]
    top_k_max = max(ref.top_k for ref in kb_refs)

    sql = text(
        """
        SELECT content, heading_path, kb_id,
               1 - (embedding <=> CAST(:qvec AS vector)) AS score
        FROM kb_chunks
        WHERE tenant_id = CAST(:tid AS uuid)
          AND kb_id = ANY(:kb_ids)
        ORDER BY embedding <=> CAST(:qvec AS vector) ASC
        LIMIT :limit
        """
    )
    rows = (
        (
            await session.execute(
                sql,
                {
                    "qvec": _vec_to_pg_literal(query_vec),
                    "tid": str(tenant_id),
                    "kb_ids": kb_ids,
                    "limit": top_k_max,
                },
            )
        )
        .mappings()
        .all()
    )

    ref_by_id = {ref.id: ref for ref in kb_refs}
    out: list[RetrievedChunk] = []
    for r in rows:
        ref = ref_by_id.get(r["kb_id"])
        if ref is None:
            continue
        if r["score"] < ref.min_score:
            continue
        out.append(
            RetrievedChunk(
                content=r["content"],
                heading_path=r["heading_path"],
                kb_id=r["kb_id"],
                score=float(r["score"]),
            )
        )

    out.sort(key=lambda c: c.score, reverse=True)

    if not out:
        logger.info(
            "kb.no_match",
            tenant=str(tenant_id),
            kb_ids=kb_ids,
            query_preview=query[:80],
        )
    else:
        logger.info(
            "kb.retrieved",
            tenant=str(tenant_id),
            chunks_count=len(out),
            top_score=out[0].score,
            kb_ids=kb_ids,
            query_preview=query[:80],
        )
    return out
