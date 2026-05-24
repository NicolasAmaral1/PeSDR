"""`ai-sdr reindex-kb` — idempotent KB indexer driver.

Walks kb/<tenant.slug>/[<kb_id>]/**/*.md, chunks + embeds + upserts via
content_hash. Use --prune to delete rows for files that disappeared.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder, build_embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader, TenantNotFoundError

reindex_kb_app = typer.Typer(help="KB management subcommands")


class _FakeEmbedder(Embedder):
    """Used only when AI_SDR_TEST_FAKE_EMBEDDER=1 (test smoke runs)."""

    def __init__(self) -> None:
        # Intentionally skip super().__init__ — we don't need the LangChain wrapper.
        pass

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 1536

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


async def _run(tenant_slug: str, kb_root: Path, kb_id: str | None, prune: bool) -> int:
    tenants_root = Path(os.getenv("AI_SDR_TENANTS_ROOT", "tenants"))
    tenant_loader = TenantLoader(tenants_dir=tenants_root)
    sops_loader = SopsLoader(tenants_dir=tenants_root)

    try:
        tenant_cfg = tenant_loader.load(tenant_slug)
    except (TenantNotFoundError, FileNotFoundError):
        print(
            f"ERROR: tenant {tenant_slug!r} not found under {tenants_root}",
            file=sys.stderr,
        )
        return 2

    if tenant_cfg.llm is None or tenant_cfg.llm.embeddings is None:
        print(
            f"ERROR: tenant {tenant_slug!r} has no llm.embeddings config in tenant.yaml",
            file=sys.stderr,
        )
        return 3

    settings = get_settings()
    eng = create_async_engine(settings.database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)

    try:
        async with sm() as session:
            async with session.begin():
                tenant = (
                    await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
                ).scalar_one_or_none()
            if tenant is None:
                print(
                    f"ERROR: tenant {tenant_slug!r} has no row in tenants table; insert one first",
                    file=sys.stderr,
                )
                return 4

            if os.getenv("AI_SDR_TEST_FAKE_EMBEDDER") == "1":
                embedder: Embedder = _FakeEmbedder()
            else:
                secrets = sops_loader.load(tenant_slug)
                embedder = build_embedder(secrets, tenant_cfg.llm.embeddings)

            async with session.begin():
                result = await reindex_tenant_kb(
                    session,
                    tenant,
                    kb_root,
                    embedder=embedder,
                    chunker=MarkdownChunker(),
                    prune=prune,
                    kb_id=kb_id,
                )
            print(
                f"indexed: {len(result.indexed)}  skipped: {len(result.skipped)}  "
                f"pruned: {len(result.pruned)}  failed: {len(result.failed)}"
            )
            for path in result.indexed:
                print(f"  + {path}")
            for path in result.skipped:
                print(f"  = {path}")
            for path in result.pruned:
                print(f"  - {path}")
            for path, err in result.failed:
                print(f"  ! {path}: {err}")
            return 0
    finally:
        await eng.dispose()


@reindex_kb_app.callback(invoke_without_command=True)
def reindex_kb(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    kb: Annotated[str | None, typer.Option("--kb", help="Limit to a specific kb_id")] = None,
    prune: Annotated[
        bool, typer.Option("--prune", help="Delete rows for docs removed from disk")
    ] = False,
    kb_root: Annotated[
        Path,
        typer.Option("--kb-root", help="Root directory containing kb/<slug>/<kb_id>/"),
    ] = Path("kb"),
) -> None:
    exit_code = asyncio.run(_run(tenant, kb_root, kb, prune))
    raise typer.Exit(code=exit_code)
