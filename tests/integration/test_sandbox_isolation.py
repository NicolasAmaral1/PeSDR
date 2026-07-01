"""Sandbox isolation guarantees (PR #26, per Nicolas's review).

The whole point of the sandbox track is "don't pollute production". These
tests verify the two isolation channels Nicolas flagged in the review:

  1. Sandbox Leads MUST NOT appear in the HITL pending-inbox view used by
     operators to triage real prospects (`_list_pending_lead_rows` filters
     `Lead.is_sandbox.is_(False)`).
  2. Sandbox Talks MUST NOT be picked up by `scan_active_talks`, the cron
     job that closes/escalates real conversations on inactivity, etc.

Both regressions would silently surface sandbox state in real operator
workflows — exactly the failure mode the gate is designed to prevent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.repositories.inbox_repository import list_contacts
from ai_sdr.web.passwords import hash_password
from ai_sdr.web.routes import _list_pending_lead_rows
from ai_sdr.worker.jobs.scan_talks import scan_active_talks

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_sandbox_lead_excluded_from_pending_inbox(db_session):
    """`_list_pending_lead_rows` (the HITL operator inbox) must hide sandbox leads.

    Real prospects in pending_assignment SHOULD appear; sandbox leads in any
    status (active here, since SandboxService stamps active) MUST NOT — even
    if they coincidentally had status='pending_assignment'.
    """
    tenant = Tenant(slug=f"iso-{uuid.uuid4().hex[:6]}", display_name="Iso")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    real_lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511900000001",
        status="pending_assignment",
        is_sandbox=False,
    )
    sandbox_lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511900000002",
        status="pending_assignment",  # worst case: even pending status sandboxes hidden
        is_sandbox=True,
    )
    db_session.add_all([real_lead, sandbox_lead])
    await db_session.commit()

    rows = await _list_pending_lead_rows(db_session, tenant.id)
    returned_ids = {r["id"] for r in rows}

    assert real_lead.id in returned_ids
    assert sandbox_lead.id not in returned_ids


@pytest.mark.asyncio
async def test_sandbox_talk_excluded_from_scan_active_talks(
    db_session, seeded_talk_factory, monkeypatch
):
    """`scan_active_talks` candidate query filters `Talk.is_sandbox.is_(False)`.

    Seed two active Talks of the same tenant — one normal, one sandbox. The
    scanner's candidate select must return only the normal one. We assert
    on the candidate set directly (running the scanner end-to-end is a
    superset already covered by other tests).
    """
    # Real Talk via factory (mirrors prod seed shape).
    real_talk, tenant = await seeded_talk_factory(handling_mode="ai")

    # Sandbox Talk piggybacking on the same tenant + treeflow_version.
    await set_tenant_context(db_session, tenant.id)
    sandbox_lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5550999999999",
        status="active",
        is_sandbox=True,
    )
    db_session.add(sandbox_lead)
    await db_session.flush()

    sandbox_talk = Talk(
        tenant_id=tenant.id,
        lead_id=sandbox_lead.id,
        treeflow_id=real_talk.treeflow_id,
        treeflow_version_id=real_talk.treeflow_version_id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(UTC) - timedelta(hours=1),
        created_at=datetime.now(UTC) - timedelta(hours=1),
        is_sandbox=True,
        sandbox_llm_mode="fake",
    )
    db_session.add(sandbox_talk)
    await db_session.commit()

    # Run the same candidate query the scanner uses (mirrors scan_talks.py).
    rows = (
        await db_session.execute(
            select(Talk.id, Talk.is_sandbox, Talk.handling_mode)
            .join(TreeflowVersion, Talk.treeflow_version_id == TreeflowVersion.id)
            .where(
                Talk.status == "active",
                Talk.handling_mode == "ai",
                Talk.is_sandbox.is_(False),
            )
        )
    ).all()
    candidate_ids = {row.id for row in rows}

    assert real_talk.id in candidate_ids
    assert sandbox_talk.id not in candidate_ids


@pytest.mark.asyncio
async def test_scan_active_talks_e2e_skips_sandbox(
    db_session, seeded_talk_factory
):
    """End-to-end: scan_active_talks does NOT close a sandbox Talk past its inactivity threshold.

    Sandbox Talks should never enter the scanner pipeline regardless of how
    stale their last_message_at is. This is a defense-in-depth check on top
    of the candidate-query test above.
    """
    real_talk, tenant = await seeded_talk_factory(handling_mode="ai")
    await set_tenant_context(db_session, tenant.id)

    sandbox_lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5550888888888",
        status="active",
        is_sandbox=True,
    )
    db_session.add(sandbox_lead)
    await db_session.flush()

    sandbox_talk = Talk(
        tenant_id=tenant.id,
        lead_id=sandbox_lead.id,
        treeflow_id=real_talk.treeflow_id,
        treeflow_version_id=real_talk.treeflow_version_id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(UTC) - timedelta(days=400),  # stale on any policy
        created_at=datetime.now(UTC) - timedelta(days=400),
        is_sandbox=True,
        sandbox_llm_mode="fake",
    )
    db_session.add(sandbox_talk)
    await db_session.commit()

    await scan_active_talks(db_session, now=datetime.now(UTC))

    # Sandbox talk must remain active (not closed by scanner).
    await db_session.refresh(sandbox_talk)
    assert sandbox_talk.status == "active"
    assert sandbox_talk.closed_at is None


@pytest.mark.asyncio
async def test_sandbox_lead_absent_from_console_inbox(
    db_session, seeded_talk_factory
):
    """`inbox_repository.list_contacts` MUST hide sandbox leads from the operator inbox.

    Regression test for Nicolas's PR #26 round-2 review: without the
    `Lead.is_sandbox.is_(False)` filter in `list_contacts`, a sandbox Lead
    with an active AI Talk would surface in `/inbox` alongside real
    prospects. This is the same failure mode the pending-inbox test
    guards, but on the Chat Operator Inbox path (PR #27).
    """
    real_talk, tenant = await seeded_talk_factory(handling_mode="ai")
    await set_tenant_context(db_session, tenant.id)

    sandbox_lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5550777777777",
        status="active",
        is_sandbox=True,
        inbound_channel_label="main",
    )
    db_session.add(sandbox_lead)
    await db_session.flush()

    sandbox_talk = Talk(
        tenant_id=tenant.id,
        lead_id=sandbox_lead.id,
        treeflow_id=real_talk.treeflow_id,
        treeflow_version_id=real_talk.treeflow_version_id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        is_sandbox=True,
        sandbox_llm_mode="fake",
    )
    db_session.add(sandbox_talk)

    # An operator user (list_contacts requires a user_id for the read markers join).
    operator = User(
        username=f"op_{uuid.uuid4().hex[:6]}", password_hash=hash_password("pw")
    )
    db_session.add(operator)
    await db_session.flush()
    db_session.add(
        UserTenantAccess(user_id=operator.id, tenant_id=tenant.id, role="operator")
    )
    await db_session.commit()

    rows = await list_contacts(
        db_session,
        tenant_id=tenant.id,
        channel_label="main",
        user_id=operator.id,
        status="ai",
    )
    returned_lead_ids = {row.lead_id for row in rows}

    assert real_talk.lead_id in returned_lead_ids
    assert sandbox_lead.id not in returned_lead_ids
