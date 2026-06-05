"""Helpers for tests that build on the Avelum v2 fixture."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
TREEFLOW_YAML_PATH = FIXTURE_DIR / "avelum_treeflow_v2.yaml"


async def seed_avelum_v2(session: AsyncSession) -> tuple[Tenant, TreeflowVersion]:
    """Insert an Avelum-shaped tenant + a TreeflowVersion of the fixture.

    The tenant has architecture_version=2 so process_lead_inbox routes
    to the FlowEngine.
    """
    tenant = Tenant(
        slug=f"avelum-{uuid.uuid4().hex[:8]}",
        display_name="Avelum",
        architecture_version=2,
    )
    session.add(tenant)
    await session.flush()

    yaml_text = TREEFLOW_YAML_PATH.read_text()
    tfv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="avelum_sdr",
        version="1.0.0",
        content_hash=f"sha-{uuid.uuid4().hex[:12]}",
        content_yaml=yaml_text,
    )
    session.add(tfv)
    await session.flush()

    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    return tenant, tfv
