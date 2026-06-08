"""resolve_pipeline_context bootstraps TalkFlowState on a new Talk."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.preprocessing import resolve_pipeline_context
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository


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


@pytest.mark.asyncio
async def test_new_talk_bootstraps_state_with_first_message(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml=MINIMAL_TF_YAML,
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    treeflow = load_treeflow_v2(tfv.content_yaml)
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="oi mira",
        received_at=datetime.now(timezone.utc),
        raw={"body": "oi mira"},
    )
    db_session.add(inbound)
    await db_session.flush()

    ctx = await resolve_pipeline_context(
        db_session,
        tenant=tenant,
        inbound=inbound,
        treeflow=treeflow,
        treeflow_version=tfv,
        opt_out_keywords=[],
    )

    # The state must exist and carry the first inbound message.
    repo = TalkFlowStateRepository(db_session)
    state = await repo.load(ctx.talk.id)
    assert state is not None
    assert state.current_node == "saudacao"
    assert len(state.messages) == 1
    assert state.messages[0]["content"] == "oi mira"
    assert state.messages[0]["role"] == "user"
    assert state.messages[0]["source"] == "lead"
    assert state.messages[0]["turn_index"] == 1


@pytest.mark.asyncio
async def test_returning_talk_does_not_double_bootstrap(
    db_session: AsyncSession,
) -> None:
    """Existing Talk does not get its state re-initialized."""
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml=MINIMAL_TF_YAML,
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    treeflow = load_treeflow_v2(tfv.content_yaml)

    inbound1 = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"a-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999", text="oi 1",
        received_at=datetime.now(timezone.utc),
        raw={"body": "oi 1"},
    )
    db_session.add(inbound1)
    await db_session.flush()
    ctx1 = await resolve_pipeline_context(
        db_session, tenant=tenant, inbound=inbound1,
        treeflow=treeflow, treeflow_version=tfv, opt_out_keywords=[],
    )
    await db_session.flush()

    repo = TalkFlowStateRepository(db_session)
    state_before = await repo.load(ctx1.talk.id)
    assert state_before is not None and len(state_before.messages) == 1

    # Second inbound on same lead -> resolve again. State must NOT reset.
    inbound2 = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"b-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999", text="oi 2",
        received_at=datetime.now(timezone.utc),
        raw={"body": "oi 2"},
    )
    db_session.add(inbound2)
    await db_session.flush()
    ctx2 = await resolve_pipeline_context(
        db_session, tenant=tenant, inbound=inbound2,
        treeflow=treeflow, treeflow_version=tfv, opt_out_keywords=[],
    )
    assert ctx2.is_new_talk is False

    state_after = await repo.load(ctx1.talk.id)
    # FE-01b preprocessing does NOT append for returning Talks — that
    # happens during the main run_turn loop. So len stays 1.
    assert state_after is not None and len(state_after.messages) == 1
