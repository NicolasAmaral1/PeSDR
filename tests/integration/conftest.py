"""Integration-level fixtures shared across inbox test files."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.passwords import hash_password


def _make_tenant_yaml(tmpdir: Path, slug: str) -> None:
    (tmpdir / slug).mkdir(parents=True, exist_ok=True)
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
async def authed_inbox_client(app, db_session, isolated_tenants_dir, monkeypatch):
    """Signed-in httpx client + seeded tenant with console enabled, instance, lead, inbound.

    Yields (client, ctx) where ctx contains:
      - slug: tenant slug
      - tenant: Tenant ORM object
      - user: User ORM object
      - lead: Lead ORM object
      - lead_id: UUID of the seeded lead (convenience alias)
      - instance: Instance ORM object
    """
    _patch_settings(monkeypatch, isolated_tenants_dir)

    tenant = Tenant(slug=f"inbox-{uuid.uuid4().hex[:6]}", display_name="InboxTest")
    db_session.add(tenant)
    await db_session.flush()
    _make_tenant_yaml(isolated_tenants_dir, tenant.slug)

    await set_tenant_context(db_session, tenant.id)

    user = User(username=f"u_{uuid.uuid4().hex[:6]}", password_hash=hash_password("pw"))
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role="operator"))

    instance = Instance(tenant_id=tenant.id, channel_label="main", display_name="Main")
    db_session.add(instance)
    await db_session.flush()

    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511988887777",
        status="pending_assignment",
        inbound_channel_label="main",
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

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        login_resp = await client.post(
            "/console/login",
            data={"username": user.username, "password": "pw"},
        )
        assert login_resp.status_code == 303, f"Login failed: {login_resp.status_code} {login_resp.text}"
        cookie = login_resp.cookies["pesdr_session"]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
        cookies={"pesdr_session": cookie},
    ) as client:
        yield client, {
            "slug": tenant.slug,
            "tenant": tenant,
            "user": user,
            "lead": lead,
            "lead_id": lead.id,
            "instance": instance,
        }


@pytest.fixture
def seeded_talk_factory(db_session):
    """Async factory fixture that seeds Tenant + Lead + TreeflowVersion + Talk.

    Usage::

        talk, tenant = await seeded_talk_factory(handling_mode="ai")
        talk, tenant = await seeded_talk_factory(handling_mode="human", status="active")
        talk, tenant = await seeded_talk_factory(lead_id=existing_lead.id)

    Mirrors Talk-seeding in tests/integration/test_inbox_filters.py
    (test_multi_lead_active_talk_lateral) — sets: treeflow_id,
    treeflow_version_id, status, handling_mode, last_message_at.

    Args:
        handling_mode: "ai" | "human" | "auto_with_approval" (default "ai").
        status: Talk status string (default "active").
        lead_id: If provided, attach the Talk to this existing lead instead of
                 creating a new one. The Lead's tenant_id must match the seeded
                 tenant (caller's responsibility).
    """

    async def _factory(
        handling_mode: str = "ai",
        status: str = "active",
        lead_id: Optional[uuid.UUID] = None,
    ) -> tuple[Talk, Tenant]:
        if lead_id is not None:
            # --- lead_id path: attach Talk to an existing lead's tenant ---
            existing_lead = await db_session.get(Lead, lead_id)
            if existing_lead is None:
                raise ValueError(f"seeded_talk_factory: lead {lead_id} not found")

            tenant = await db_session.get(Tenant, existing_lead.tenant_id)
            if tenant is None:
                raise ValueError(
                    f"seeded_talk_factory: tenant {existing_lead.tenant_id} not found"
                )

            # Set RLS tenant context to the lead's tenant.
            await set_tenant_context(db_session, tenant.id)

            # Reuse an existing TreeflowVersion for this tenant if available,
            # otherwise create one.
            tfv_result = await db_session.execute(
                select(TreeflowVersion)
                .where(TreeflowVersion.tenant_id == tenant.id)
                .limit(1)
            )
            tfv = tfv_result.scalars().first()
            if tfv is None:
                tfv = TreeflowVersion(
                    tenant_id=tenant.id,
                    treeflow_id="tf-seeded",
                    version="1",
                    content_hash=uuid.uuid4().hex,
                    content_yaml="nodes: []",
                )
                db_session.add(tfv)
                await db_session.flush()

        else:
            # --- no lead_id path: create a fresh Tenant + Lead + TreeflowVersion ---
            tenant = Tenant(slug=f"talk-{uuid.uuid4().hex[:6]}", display_name="TalkTest")
            db_session.add(tenant)
            await db_session.flush()

            # Set RLS tenant context (required by tenant-scoped tables).
            await set_tenant_context(db_session, tenant.id)

            # Seed a TreeflowVersion so Talk FK is satisfied.
            tfv = TreeflowVersion(
                tenant_id=tenant.id,
                treeflow_id="tf-seeded",
                version="1",
                content_hash=uuid.uuid4().hex,
                content_yaml="nodes: []",
            )
            db_session.add(tfv)
            await db_session.flush()

            lead = Lead(
                tenant_id=tenant.id,
                whatsapp_e164=f"+551{uuid.uuid4().int % 10**10:010d}",
                status="pending_assignment",
                inbound_channel_label="main",
            )
            db_session.add(lead)
            await db_session.flush()
            lead_id = lead.id

        talk = Talk(
            tenant_id=tenant.id,
            lead_id=lead_id,
            treeflow_id="tf-seeded",
            treeflow_version_id=tfv.id,
            status=status,
            handling_mode=handling_mode,
            last_message_at=datetime.now(UTC),
        )
        db_session.add(talk)
        await db_session.flush()

        return talk, tenant

    return _factory
