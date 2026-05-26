"""Lead assignment routes — pending list + assign endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _install_default_arq_pool(app) -> None:
    """The assign endpoint declares an arq_pool dep that runs BEFORE the
    route handler (FastAPI evaluates all deps first). Without a default
    pool on app.state, even 404/409 tests fail because the dep raises
    RuntimeError. Install a no-op pool; tests that need to observe enqueue
    calls overwrite it with their own FakePool."""

    class NoopPool:
        async def enqueue_job(self, name, *args, **kwargs):  # noqa: ARG002
            return None

        async def aclose(self) -> None:
            pass

    app.state.arq_pool = NoopPool()


@pytest.fixture
async def tenant_with_treeflow(db_session) -> tuple[Tenant, TreeflowVersion]:
    t = Tenant(slug=f"lead_{uuid.uuid4().hex[:6]}", display_name="L")
    db_session.add(t)
    await db_session.flush()
    # Set tenant context BEFORE inserting the RLS-protected treeflow_versions row
    await set_tenant_context(db_session, t.id)
    tv = TreeflowVersion(
        tenant_id=t.id,
        treeflow_id="mentoria",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: mentoria\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.commit()
    return t, tv


async def test_pending_list_returns_only_pending(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, _ = tenant_with_treeflow
    await set_tenant_context(db_session, tenant.id)
    db_session.add_all([
        Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="pending_assignment"),
        Lead(tenant_id=tenant.id, whatsapp_e164="+2", status="active"),
        Lead(tenant_id=tenant.id, whatsapp_e164="+3", status="pending_assignment"),
    ])
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(f"/tenants/{tenant.slug}/leads/pending")
    assert r.status_code == 200
    bodies = r.json()
    assert len(bodies) == 2
    for b in bodies:
        assert b["status"] == "pending_assignment"


async def test_assign_404_on_unknown_lead(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, _ = tenant_with_treeflow
    bogus = uuid.uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{bogus}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 404


async def test_assign_409_when_lead_not_pending(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, _ = tenant_with_treeflow
    await set_tenant_context(db_session, tenant.id)
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+9", status="active")
    db_session.add(lead)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{lead.id}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 409


async def test_assign_happy_path_creates_talkflow_and_enqueues(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, tv = tenant_with_treeflow
    await set_tenant_context(db_session, tenant.id)
    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511999999999",
        status="pending_assignment",
    )
    db_session.add(lead)
    await db_session.flush()
    # Queue two inbound messages (replay-all)
    for i in range(2):
        db_session.add(
            InboundMessageRow(
                tenant_id=tenant.id,
                provider="whatsapp_cloud",
                external_id=f"ext_{i}",
                lead_id=lead.id,
                from_address="+5511999999999",
                text=f"msg{i}",
                received_at=datetime.now(timezone.utc),
                raw={},
            )
        )
    await db_session.commit()

    enqueued: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args))

    app.state.arq_pool = FakePool()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{lead.id}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 202
    body = r.json()
    assert "talkflow_id" in body
    assert body["queued_messages_to_replay"] == 2

    # Re-set tenant context after the route's commit (transaction-local)
    await set_tenant_context(db_session, tenant.id)
    await db_session.refresh(lead)
    assert lead.status == "active"

    tfs = (
        await db_session.execute(
            select(TalkFlow).where(TalkFlow.lead_id == lead.id)
        )
    ).scalars().all()
    assert len(tfs) == 1
    assert str(tfs[0].id) == body["talkflow_id"]

    assert enqueued == [
        ("process_lead_inbox", (str(tenant.id), str(lead.id))),
    ]
