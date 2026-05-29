"""load_treeflow_follow_up — reads TreeFlow.follow_up from TalkFlow's pinned version."""

from __future__ import annotations

import uuid

import pytest

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.treeflow_loader import load_treeflow_follow_up
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


_YAML_WITH_FOLLOWUP = """
id: t1
version: 1.0.0
display_name: T1
entry_node: n1
nodes:
  - id: n1
    prompt: hi
    exit_condition:
      type: all_fields_filled
    next_nodes:
      - condition: "true"
        target: END
follow_up:
  enabled: true
  max_attempts: 2
  sequence:
    - after: PT24H
      template_ref: followup_24h_v1
      language: pt_BR
      params: ["{{ collected.nome | default('amigo') }}"]
    - after: P3D
      template_ref: followup_72h_v1
"""

_YAML_NO_FOLLOWUP = """
id: t1
version: 1.0.0
display_name: T1
entry_node: n1
nodes:
  - id: n1
    prompt: hi
    exit_condition:
      type: all_fields_filled
    next_nodes:
      - condition: "true"
        target: END
"""


async def _make_talkflow(db_session, yaml_str: str) -> TalkFlow:
    tenant = Tenant(slug=f"l_{uuid.uuid4().hex[:6]}", display_name="L")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml=yaml_str,
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    return tf


async def test_returns_config_when_present(db_session) -> None:
    tf = await _make_talkflow(db_session, _YAML_WITH_FOLLOWUP)
    cfg = await load_treeflow_follow_up(db_session, tf)
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.max_attempts == 2
    assert len(cfg.sequence) == 2
    assert cfg.sequence[0].template_ref == "followup_24h_v1"


async def test_returns_none_when_absent(db_session) -> None:
    tf = await _make_talkflow(db_session, _YAML_NO_FOLLOWUP)
    cfg = await load_treeflow_follow_up(db_session, tf)
    assert cfg is None
