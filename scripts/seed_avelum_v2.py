"""Idempotent seed for the Avelum v2 tenant + TreeflowVersion.

Run: `uv run python scripts/seed_avelum_v2.py`

Inserts (or updates) the Avelum tenant with architecture_version=2 and
publishes the TreeFlow YAML at tenants/avelum/treeflows/avelum_sdr.yaml
as a TreeflowVersion. Safe to re-run — checks for existing rows by slug
and (tenant_id, treeflow_id, version).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
TREEFLOW_PATH = REPO_ROOT / "tenants" / "avelum" / "treeflows" / "avelum_sdr.yaml"


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as s:
        async with s.begin():
            tenant = (
                await s.execute(select(Tenant).where(Tenant.slug == "avelum"))
            ).scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(
                    slug="avelum",
                    display_name="Avelum",
                    architecture_version=2,
                )
                s.add(tenant)
                await s.flush()
                print(f"[+] created tenant id={tenant.id} slug=avelum arch_v=2")
            else:
                if tenant.architecture_version != 2:
                    tenant.architecture_version = 2
                    print(f"[~] tenant arch_v -> 2 (was {tenant.architecture_version})")
                else:
                    print(f"[=] tenant exists id={tenant.id} arch_v=2 already")

            await s.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant.id)},
            )

            yaml_text = TREEFLOW_PATH.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(yaml_text.encode()).hexdigest()

            existing_tfv = (
                await s.execute(
                    select(TreeflowVersion).where(
                        TreeflowVersion.tenant_id == tenant.id,
                        TreeflowVersion.treeflow_id == "avelum_sdr",
                        TreeflowVersion.version == "1.0.0",
                    )
                )
            ).scalar_one_or_none()

            if existing_tfv is None:
                tfv = TreeflowVersion(
                    tenant_id=tenant.id,
                    treeflow_id="avelum_sdr",
                    version="1.0.0",
                    content_hash=content_hash,
                    content_yaml=yaml_text,
                )
                s.add(tfv)
                await s.flush()
                print(f"[+] created TreeflowVersion id={tfv.id} treeflow=avelum_sdr v=1.0.0")
            elif existing_tfv.content_hash != content_hash:
                existing_tfv.content_hash = content_hash
                existing_tfv.content_yaml = yaml_text
                print(f"[~] updated TreeflowVersion id={existing_tfv.id} (hash drift)")
            else:
                print(f"[=] TreeflowVersion exists id={existing_tfv.id} no drift")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
