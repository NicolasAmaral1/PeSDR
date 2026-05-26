"""Smoke test for `ai-sdr reindex-kb` — runs the CLI via subprocess."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


def _write_tenant_dir(tenants_root: Path, slug: str) -> Path:
    tdir = tenants_root / slug
    (tdir / "treeflows").mkdir(parents=True)
    tenant_yaml = {
        "id": slug,
        "display_name": slug.upper(),
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_ref": "secrets/anthropic_key",
            },
            "embeddings": {"provider": "openai"},
        },
    }
    (tdir / "tenant.yaml").write_text(yaml.safe_dump(tenant_yaml))
    # No secrets file written — the CLI test sets AI_SDR_TEST_FAKE_EMBEDDER=1,
    # which short-circuits the sops_loader.load() call entirely.
    return tdir


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_reindex_kb_cli_smoke(session: AsyncSession, tmp_path: Path) -> None:
    slug = f"t-{uuid.uuid4().hex[:8]}"
    tenants_root = tmp_path / "tenants"
    _write_tenant_dir(tenants_root, slug)

    kb_root = tmp_path / "kb"
    (kb_root / slug / "kb_x").mkdir(parents=True)
    (kb_root / slug / "kb_x" / "precos.md").write_text(
        "## Preços\n\nMentoria custa R$ 6000.", encoding="utf-8"
    )

    async with session.begin():
        t = Tenant(slug=slug, display_name=slug.upper())
        session.add(t)
        await session.flush()
        tenant_id = t.id

    env = dict(os.environ)
    env["AI_SDR_TEST_FAKE_EMBEDDER"] = "1"
    env["AI_SDR_TENANTS_ROOT"] = str(tenants_root)

    cmd = [
        "uv",  # noqa: S607
        "run",
        "ai-sdr",
        "reindex-kb",
        "--tenant",
        slug,
        "--kb-root",
        str(kb_root),
    ]
    result = subprocess.run(  # noqa: S603, ASYNC221
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr={result.stderr}\nstdout={result.stdout}"
    assert "indexed" in result.stdout.lower()

    async with session.begin():
        await set_tenant_context(session, tenant_id)
        chunks = (await session.execute(select(KbChunk))).scalars().all()
        assert len(chunks) >= 1


@pytest.mark.integration
async def test_reindex_kb_cli_unknown_tenant_exits_nonzero(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["AI_SDR_TENANTS_ROOT"] = str(tmp_path / "tenants_empty")
    result = subprocess.run(  # noqa: S603, ASYNC221
        ["uv", "run", "ai-sdr", "reindex-kb", "--tenant", "ghost"],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode != 0
    assert "ghost" in result.stderr.lower() or "not found" in result.stderr.lower()
