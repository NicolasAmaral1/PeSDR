"""process_sandbox_turn — happy-path + error-path (PR #26 review).

Nicolas's bloqueador #2: "Worker happy-path + error-path (LLM exception →
inbound `status="error"`, sem outbound fantasma)".

These tests exercise the worker job directly with a real DB session, a
seeded sandbox Talk, and a monkeypatched `run_turn` so we don't need a
live Anthropic key or a full treeflow YAML on disk. The point is to
verify the worker's *contract* around the pipeline:

  - Happy: run_turn returns outcome='sent' → inbound goes to 'processed',
    talk row reflects the post-call state.
  - Error: run_turn raises → inbound goes to 'error' with a typed detail,
    and NO OutboundMessage row is left dangling (the worker must not
    half-commit a fantôme outbound when run_turn blew up).

Module-level `run_turn` is the patch target (import shape in the worker
module is `from ai_sdr.flowengine.pipeline import run_turn`, so patching
the worker-side symbol is sufficient).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import ai_sdr.worker.jobs.process_sandbox_turn as worker_mod
from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.pipeline import RunTurnResult
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.process_sandbox_turn import process_sandbox_turn
from tests.integration.avelum_helpers import seed_avelum_v2

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():
    engine = build_engine(get_settings().database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def _ctx(session_factory) -> dict[str, Any]:
    return {"session_factory": session_factory}


async def _seed_sandbox_talk(
    db_session, *, llm_mode: str = "fake"
) -> tuple[Tenant, Lead, Talk, InboundMessageRow]:
    tenant, tfv = await seed_avelum_v2(db_session)
    await set_tenant_context(db_session, tenant.id)

    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164=f"+555099{uuid.uuid4().hex[:8]}",
        status="active",
        is_sandbox=True,
    )
    db_session.add(lead)
    await db_session.flush()

    now = datetime.now(UTC)
    talk = Talk(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id=tfv.treeflow_id,
        treeflow_version_id=tfv.id,
        status="active",
        handling_mode="ai",
        created_at=now,
        last_message_at=now,
        turn_count=0,
        tokens_consumed={},
        is_sandbox=True,
        sandbox_llm_mode=llm_mode,
    )
    db_session.add(talk)
    await db_session.flush()

    state = TalkFlowState(
        tenant_id=tenant.id,
        talk_id=talk.id,
        current_node="qualificacao",  # any node id present in the fixture treeflow
        collected={},
        extracted_facts={},
        objections_handled=[],
        decision_audit={},
        updated_at=now,
    )
    db_session.add(state)

    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        provider="sandbox",
        external_id=f"sb_{uuid.uuid4().hex[:12]}",
        from_address=lead.whatsapp_e164 or "sandbox",
        text="oi! tô interessado",
        received_at=now,
        status="queued",
        raw={"sandbox": True},
    )
    db_session.add(inbound)

    await db_session.commit()
    return tenant, lead, talk, inbound


def _patch_tenant_yaml(monkeypatch, tdir: Path, tenant_slug: str) -> None:
    """Write a tenant.yaml the worker can load + point Settings at the temp dir."""
    yaml = f"""id: {tenant_slug}
display_name: {tenant_slug}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
console:
  enabled: true
"""
    (tdir / tenant_slug).mkdir(parents=True, exist_ok=True)
    (tdir / tenant_slug / "tenant.yaml").write_text(yaml)
    (tdir / tenant_slug / "secrets.enc.yaml").write_text("anthropic_key: stub")

    s = get_settings()
    monkeypatch.setattr(s, "tenants_dir", str(tdir))


@pytest.mark.asyncio
async def test_sandbox_worker_happy_path(
    db_session, session_factory, monkeypatch, tmp_path
):
    """run_turn returns outcome='sent' → inbound 'processed', no dangling errors."""
    tenant, _lead, talk, inbound = await _seed_sandbox_talk(db_session, llm_mode="fake")
    _patch_tenant_yaml(monkeypatch, tmp_path, tenant.slug)

    # Stub SopsLoader so we don't need real encrypted secrets.
    from ai_sdr.secrets.sops_loader import SopsLoader

    monkeypatch.setattr(
        SopsLoader, "load", lambda self, slug: {"anthropic_key": "sk-stub"}
    )

    captured: dict[str, Any] = {}

    async def fake_run_turn(session, **kwargs):
        captured["adapter_class"] = type(kwargs["adapter"]).__name__
        captured["llm"] = kwargs["llm"]
        return RunTurnResult(
            outcome="sent",
            current_node_after="qualificacao",
            response_text="oi tudo bem!",
        )

    monkeypatch.setattr(worker_mod, "run_turn", fake_run_turn)

    await process_sandbox_turn(_ctx(session_factory), str(tenant.id), str(talk.id))

    # The worker opens its own session — reload via the test session.
    async with session_factory() as fresh:
        await set_tenant_context(fresh, tenant.id)
        inbound_after = (
            await fresh.execute(
                select(InboundMessageRow).where(InboundMessageRow.id == inbound.id)
            )
        ).scalar_one()
        assert inbound_after.status == "processed"
        assert inbound_after.processed_at is not None
        assert inbound_after.error_detail is None

    # Adapter contract: it was SandboxMessagingAdapter, not whatever
    # `tenant.messaging.provider` would have produced.
    assert captured["adapter_class"] == "SandboxMessagingAdapter"


@pytest.mark.asyncio
async def test_sandbox_worker_error_path_marks_inbound_no_fantom_outbound(
    db_session, session_factory, monkeypatch, tmp_path
):
    """run_turn raises → inbound 'error', NO OutboundMessage row leaks."""
    tenant, _lead, talk, inbound = await _seed_sandbox_talk(db_session, llm_mode="fake")
    _patch_tenant_yaml(monkeypatch, tmp_path, tenant.slug)

    from ai_sdr.secrets.sops_loader import SopsLoader

    monkeypatch.setattr(
        SopsLoader, "load", lambda self, slug: {"anthropic_key": "sk-stub"}
    )

    async def boom_run_turn(session, **kwargs):
        raise RuntimeError("LLM provider timed out")

    monkeypatch.setattr(worker_mod, "run_turn", boom_run_turn)

    await process_sandbox_turn(_ctx(session_factory), str(tenant.id), str(talk.id))

    async with session_factory() as fresh:
        await set_tenant_context(fresh, tenant.id)
        inbound_after = (
            await fresh.execute(
                select(InboundMessageRow).where(InboundMessageRow.id == inbound.id)
            )
        ).scalar_one()
        assert inbound_after.status == "error"
        assert inbound_after.error_detail is not None
        assert "RuntimeError" in inbound_after.error_detail

        # The critical assertion: NO outbound row was created for this Talk.
        # A "fantôme" outbound would suggest a half-committed turn.
        outbounds = (
            await fresh.execute(
                select(OutboundMessage).where(OutboundMessage.talkflow_id == talk.id)
            )
        ).scalars().all()
        assert outbounds == []
