"""Verify talkflows.lead_id is a UUID FK to leads.id after migration 0008."""

from __future__ import annotations

import uuid

import pytest

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


async def test_talkflow_lead_id_is_uuid_fk(db_session) -> None:
    tenant = Tenant(slug=f"t_{uuid.uuid4().hex[:6]}", display_name="T")
    db_session.add(tenant)
    await db_session.flush()
    # treeflow_versions has RLS — set tenant context before inserting.
    await set_tenant_context(db_session, tenant.id)

    # Create a treeflow version (TalkFlow needs it as FK target)
    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes:\n  n1: {prompt: hi}\n",
    )
    db_session.add(tv)
    await db_session.commit()

    # Re-set after commit (transaction-local).
    await set_tenant_context(db_session, tenant.id)
    lead = Lead(tenant_id=tenant.id, external_label="x", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,  # MUST accept uuid.UUID now
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    assert isinstance(tf.lead_id, uuid.UUID)
