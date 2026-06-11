"""Preprocessing resolves Lead + Talk for incoming inbound."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.preprocessing import (
    OptOutDetected,
    PipelineContext,
    resolve_pipeline_context,
)
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


MINIMAL_TF_YAML = """
schema_version: 1
id: t
version: "1"
sdr_persona: {voice: "x", conduct: "x", examples: []}
entry_node: saudacao
nodes:
  - id: saudacao
    objetivo: x
    bridge_instruction: ""
    collects: []
    exit_condition: {type: all_fields_filled}
    next_nodes: []
"""


async def _seed_tenant_and_treeflow(
    db_session: AsyncSession,
) -> tuple[Tenant, TreeflowVersion]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    tfv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="tf",
        version="1",
        content_hash="x",
        content_yaml=MINIMAL_TF_YAML,
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    return tenant, tfv


async def _seed_inbound(
    db_session: AsyncSession,
    tenant: Tenant,
    from_address: str,
    body: str,
) -> InboundMessageRow:
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address=from_address,
        text=body,
        received_at=datetime.now(timezone.utc),
        raw={"body": body},
    )
    db_session.add(inbound)
    await db_session.flush()
    return inbound


@pytest.mark.asyncio
async def test_creates_lead_and_talk_for_new_sender(db_session: AsyncSession) -> None:
    tenant, tfv = await _seed_tenant_and_treeflow(db_session)
    inbound = await _seed_inbound(db_session, tenant, "+5511999999999", "oi")
    treeflow = load_treeflow_v2(tfv.content_yaml)

    ctx = await resolve_pipeline_context(
        db_session,
        tenant=tenant,
        inbound=inbound,
        treeflow=treeflow,
        treeflow_version=tfv,
        opt_out_keywords=["sair", "parar"],
    )

    assert isinstance(ctx, PipelineContext)
    assert ctx.lead.channel_identifiers == {"whatsapp": "+5511999999999"}
    assert ctx.talk.status == "active"
    assert ctx.talk.lead_id == ctx.lead.id
    assert ctx.is_new_talk is True


@pytest.mark.asyncio
async def test_reuses_existing_lead_and_talk(db_session: AsyncSession) -> None:
    tenant, tfv = await _seed_tenant_and_treeflow(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)

    # First inbound creates Lead + Talk.
    inbound1 = await _seed_inbound(db_session, tenant, "+5511999999999", "oi")
    ctx1 = await resolve_pipeline_context(
        db_session,
        tenant=tenant,
        inbound=inbound1,
        treeflow=treeflow,
        treeflow_version=tfv,
        opt_out_keywords=[],
    )
    await db_session.flush()

    # Second inbound reuses both.
    inbound2 = await _seed_inbound(db_session, tenant, "+5511999999999", "oi de novo")
    ctx2 = await resolve_pipeline_context(
        db_session,
        tenant=tenant,
        inbound=inbound2,
        treeflow=treeflow,
        treeflow_version=tfv,
        opt_out_keywords=[],
    )
    assert ctx2.lead.id == ctx1.lead.id
    assert ctx2.talk.id == ctx1.talk.id
    assert ctx2.is_new_talk is False


@pytest.mark.asyncio
async def test_opt_out_detected_short_circuits(db_session: AsyncSession) -> None:
    tenant, tfv = await _seed_tenant_and_treeflow(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)
    inbound = await _seed_inbound(db_session, tenant, "+5511999999999", "quero SAIR")

    with pytest.raises(OptOutDetected):
        await resolve_pipeline_context(
            db_session,
            tenant=tenant,
            inbound=inbound,
            treeflow=treeflow,
            treeflow_version=tfv,
            opt_out_keywords=["sair", "parar"],
        )
