"""Idempotent seed for the manoela-mentora v2 tenant + TreeflowVersion.

Run: `uv run python scripts/seed_manoela_v2.py`

Sets the manoela-mentora tenant to architecture_version=2 and publishes
the TreeFlow YAML at tenants/manoela-mentora/treeflows/qualificacao_inicial.yaml
as a TreeflowVersion. The treeflow_id and version are read from the YAML
itself, so a version bump in the file publishes a new row automatically.
Safe to re-run — checks existing rows by slug and (tenant_id, treeflow_id,
version).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
TREEFLOW_PATH = (
    REPO_ROOT / "tenants" / "manoela-mentora" / "treeflows" / "qualificacao_inicial.yaml"
)
TENANT_SLUG = "manoela-mentora"
TENANT_DISPLAY = "Manoela Mentora"


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    yaml_text = TREEFLOW_PATH.read_text(encoding="utf-8")
    meta = yaml.safe_load(yaml_text)
    treeflow_id = str(meta["id"])
    version = str(meta["version"])
    content_hash = hashlib.sha256(yaml_text.encode()).hexdigest()

    async with sm() as s:
        async with s.begin():
            tenant = (
                await s.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
            ).scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(
                    slug=TENANT_SLUG,
                    display_name=TENANT_DISPLAY,
                    architecture_version=2,
                )
                s.add(tenant)
                await s.flush()
                print(f"[+] created tenant id={tenant.id} slug={TENANT_SLUG} arch_v=2")
            elif tenant.architecture_version != 2:
                old = tenant.architecture_version
                tenant.architecture_version = 2
                print(f"[~] tenant arch_v {old} -> 2")
            else:
                print(f"[=] tenant exists id={tenant.id} arch_v=2 already")

            await s.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant.id)},
            )

            existing_tfv = (
                await s.execute(
                    select(TreeflowVersion).where(
                        TreeflowVersion.tenant_id == tenant.id,
                        TreeflowVersion.treeflow_id == treeflow_id,
                        TreeflowVersion.version == version,
                    )
                )
            ).scalar_one_or_none()

            if existing_tfv is None:
                tfv = TreeflowVersion(
                    tenant_id=tenant.id,
                    treeflow_id=treeflow_id,
                    version=version,
                    content_hash=content_hash,
                    content_yaml=yaml_text,
                )
                s.add(tfv)
                await s.flush()
                print(f"[+] created TreeflowVersion id={tfv.id} treeflow={treeflow_id} v={version}")
            elif existing_tfv.content_hash != content_hash:
                existing_tfv.content_hash = content_hash
                existing_tfv.content_yaml = yaml_text
                print(f"[~] updated TreeflowVersion id={existing_tfv.id} (hash drift)")
            else:
                print(f"[=] TreeflowVersion exists id={existing_tfv.id} no drift")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
