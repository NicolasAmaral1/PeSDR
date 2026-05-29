"""End-to-end console flow: login → list → detail → assign."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.passwords import hash_password

pytestmark = pytest.mark.integration


def _make_tenant_yaml(tmpdir: Path, slug: str) -> None:
    """Write tenant.yaml (without `treeflows:` block — not a schema field)
    and create a treeflow YAML file so the dropdown enumeration finds it."""
    (tmpdir / slug).mkdir(parents=True, exist_ok=True)
    (tmpdir / slug / "treeflows").mkdir(parents=True, exist_ok=True)
    yaml = f"""id: {slug}
display_name: {slug.title()}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
console:
  enabled: true
"""
    (tmpdir / slug / "tenant.yaml").write_text(yaml)
    # Create a treeflow YAML so the detail endpoint's filesystem
    # enumeration finds 'mentoria'. The content is minimal — the dropdown
    # only uses the filename stem; runtime.create reads TreeflowVersion
    # rows from DB, not this file.
    (tmpdir / slug / "treeflows" / "mentoria.yaml").write_text(
        "id: mentoria\nversion: 1.0.0\nentry_node: n1\nnodes:\n  - id: n1\n    prompt: hi\n"
    )


def _patch_settings(monkeypatch, tdir: Path, secret: str = "x" * 48) -> None:
    from ai_sdr.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "console_secret_key", secret)
    monkeypatch.setattr(s, "tenants_dir", str(tdir))


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
async def seeded(db_session, isolated_tenants_dir):
    """Full seed: tenant + tenant.yaml + treeflow_version + user + grant + lead + queued msg."""
    tenant = Tenant(slug=f"lp-{uuid.uuid4().hex[:6]}", display_name="LeadsPage")
    db_session.add(tenant)
    await db_session.flush()
    _make_tenant_yaml(isolated_tenants_dir, tenant.slug)

    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="mentoria",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml=(
            "id: mentoria\nversion: 1.0.0\nentry_node: n1\nnodes:\n  n1:\n    prompt: hi\n"
        ),
    )
    db_session.add(tv)

    user = User(username=f"u_{uuid.uuid4().hex[:6]}", password_hash=hash_password("pw"))
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role="operator"))

    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511988887777",
        status="pending_assignment",
    )
    db_session.add(lead)
    await db_session.flush()

    db_session.add(
        InboundMessageRow(
            tenant_id=tenant.id,
            provider="whatsapp_cloud",
            external_id=f"wamid.{uuid.uuid4().hex}",
            lead_id=lead.id,
            from_address="+5511988887777",
            text="oi, queria saber sobre a mentoria",
            received_at=datetime.now(UTC),
            raw={},
        )
    )
    await db_session.commit()
    return {"tenant": tenant, "user": user, "lead": lead}


async def test_full_flow(app, seeded, isolated_tenants_dir, monkeypatch) -> None:
    _patch_settings(monkeypatch, isolated_tenants_dir)

    # Install a NoopPool so the assign endpoint can enqueue.
    enqueued = []

    class NoopPool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args))

        async def aclose(self) -> None:
            pass

    app.state.arq_pool = NoopPool()

    tenant = seeded["tenant"]
    user = seeded["user"]
    lead = seeded["lead"]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        # 1. Login
        login_resp = await client.post(
            "/console/login",
            data={"username": user.username, "password": "pw"},
        )
        assert login_resp.status_code == 303
        cookie = login_resp.cookies["pesdr_session"]

        # 2. Full page
        page = await client.get(
            f"/console/{tenant.slug}/leads",
            cookies={"pesdr_session": cookie},
        )
        assert page.status_code == 200
        assert "leads-list" in page.text
        assert "lead-detail" in page.text

        # 3. List partial
        list_partial = await client.get(
            f"/console/{tenant.slug}/leads/list",
            cookies={"pesdr_session": cookie},
        )
        assert list_partial.status_code == 200
        assert "+55 11 98888-7777" in list_partial.text
        assert "queria saber sobre a mentoria" in list_partial.text

        # 4. Detail partial
        detail = await client.get(
            f"/console/{tenant.slug}/leads/{lead.id}/detail",
            cookies={"pesdr_session": cookie},
        )
        assert detail.status_code == 200
        assert "oi, queria saber sobre a mentoria" in detail.text
        assert 'name="treeflow_id"' in detail.text
        assert "mentoria" in detail.text

        # 5. Assign
        assign = await client.post(
            f"/console/{tenant.slug}/leads/{lead.id}/assign",
            data={"treeflow_id": "mentoria"},
            cookies={"pesdr_session": cookie},
        )
        assert assign.status_code == 200
        # The response replaces the master list — should not show the assigned lead anymore
        assert "+55 11 98888-7777" not in assign.text
        # OOB swap for detail panel
        assert 'id="lead-detail"' in assign.text
        assert "hx-swap-oob" in assign.text

    # 6. Verify side-effects (DB state confirmed indirectly by the list
    # partial no longer including the lead; the arq job confirms enqueue).
    assert len(enqueued) == 1
    assert enqueued[0][0] == "process_lead_inbox"
