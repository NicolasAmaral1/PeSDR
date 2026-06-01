"""Pilot harness — DB-touching helpers + end-to-end loop."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_sdr.cli.pilot import _seed_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


# Minimal valid treeflow YAML for the harness — same shape as production.
_YAML = "id: pilot_test\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n"


async def test_seed_session_creates_lead_and_talkflow(db_session, tmp_path: Path) -> None:
    # Set up: tenant in DB, treeflow YAML on disk.
    tenant = Tenant(slug=f"pilot_{uuid.uuid4().hex[:6]}", display_name="Pilot")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    tenant_out, lead_out, tf_out = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990abc123",
    )

    assert tenant_out.id == tenant.id
    assert lead_out.whatsapp_e164 == "+5511990abc123"
    assert lead_out.status == "active"
    assert tf_out.lead_id == lead_out.id
    assert tf_out.treeflow_version_id is not None

    # Verify TreeflowVersion was created with the expected content.
    tv = (
        await db_session.execute(
            select(TreeflowVersion).where(TreeflowVersion.id == tf_out.treeflow_version_id)
        )
    ).scalar_one()
    assert tv.treeflow_id == "pilot_test"
    assert tv.content_yaml == _YAML


async def test_seed_session_reuses_existing_treeflow_version(db_session, tmp_path: Path) -> None:
    # When YAML content matches an existing TreeflowVersion's content_hash, reuse it.
    tenant = Tenant(slug=f"pilot_{uuid.uuid4().hex[:6]}", display_name="Pilot")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    # First call creates the TreeflowVersion.
    _, _, tf1 = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990aaa111",
    )
    # Second call must find the same TreeflowVersion (no duplicate).
    _, _, tf2 = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990bbb222",
    )
    assert tf1.treeflow_version_id == tf2.treeflow_version_id


async def test_seed_session_fails_when_tenant_missing(db_session, tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        await _seed_session(
            db_session,
            tenants_dir=tmp_path,
            slug="does-not-exist",
            treeflow_id="x",
            from_address="+5511990aaaaaa",
        )
    assert "does-not-exist" in str(exc.value)
