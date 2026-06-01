"""Pilot harness — DB-touching helpers + end-to-end loop."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.cli.pilot import _run_loop, _seed_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
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


async def test_seed_session_handles_yaml_edit_between_runs(db_session, tmp_path: Path) -> None:
    """Editing the YAML between two pilot runs must not blow up on the
    TreeflowVersion unique constraint (tenant_id, treeflow_id, version)."""
    tenant = Tenant(slug=f"pilot_{uuid.uuid4().hex[:6]}", display_name="Pilot")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    yaml_dir = tmp_path / tenant.slug / "treeflows"
    yaml_dir.mkdir(parents=True)
    yaml_path = yaml_dir / "pilot_test.yaml"
    yaml_path.write_text(_YAML)
    await db_session.commit()

    # First run with content A.
    _, _, tf_a = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990aaa111",
    )

    # Edit the YAML — content B.
    yaml_path.write_text(_YAML + "# edited\n")

    # Second run must succeed (different content_hash → different version slot).
    _, _, tf_b = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990bbb222",
    )
    assert tf_a.treeflow_version_id != tf_b.treeflow_version_id


async def _make_eco_pool(session_factory, tenant_id, lead_id):
    """Build a MagicMock pool whose enqueue_job simulates the worker by
    reading the latest inbound and writing an eco outbound row. Returns
    the pool and a list that captures every text the 'agent' produced."""
    pool = MagicMock()
    agent_replies: list[str] = []

    async def fake_enqueue(name, *args, **kwargs):
        # name == "process_lead_inbox"; args == (str(tenant.id), str(lead.id))
        async with session_factory() as db:
            await set_tenant_context(db, tenant_id)
            latest = (
                await db.execute(
                    select(InboundMessageRow)
                    .where(InboundMessageRow.lead_id == lead_id)
                    .order_by(InboundMessageRow.received_at.desc())
                    .limit(1)
                )
            ).scalar_one()
            reply = f"eco: {latest.text}"
            agent_replies.append(reply)
            tf = (
                await db.execute(select(TalkFlow).where(TalkFlow.lead_id == lead_id))
            ).scalar_one()
            db.add(
                OutboundMessage(
                    tenant_id=tenant_id,
                    talkflow_id=tf.id,
                    lead_id=lead_id,
                    provider="fake",
                    message_type="text",
                    body_text=reply,
                    status="sent",
                    external_id=f"fake_{uuid.uuid4().hex[:8]}",
                    triggered_by="inbound",
                    sent_at=datetime.now(UTC),
                )
            )
            await db.commit()

    pool.enqueue_job = fake_enqueue
    return pool, agent_replies


async def test_run_loop_quit_immediately(db_session, tmp_path) -> None:
    # User types :quit on the very first prompt — no turns happen.
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990quit01",
    )

    # session_factory wraps a fresh session per loop tick.
    # Use the test fixture's engine via the existing db_session machinery.
    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pool, _ = await _make_eco_pool(sf, tenant.id, lead.id)

    outputs: list[str] = []
    inputs = iter([":quit"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    assert any("encerrado" in o for o in outputs)


async def test_run_loop_two_turn_eco(db_session, tmp_path) -> None:
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990two001",
    )

    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pool, agent_replies = await _make_eco_pool(sf, tenant.id, lead.id)

    outputs: list[str] = []
    inputs = iter(["Oi", "Tudo bem?", ":quit"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    assert agent_replies == ["eco: Oi", "eco: Tudo bem?"]
    # Each agent reply appears in outputs, prefixed with "agente:".
    assert sum(1 for o in outputs if o.startswith("agente:")) == 2


async def test_run_loop_handles_status_command(db_session, tmp_path) -> None:
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990stat01",
    )

    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pool, _ = await _make_eco_pool(sf, tenant.id, lead.id)

    outputs: list[str] = []
    inputs = iter([":status", ":quit"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    # :status output contains the marker fields.
    assert any("turns=0" in o for o in outputs)
    assert any("lead.status=active" in o for o in outputs)


async def test_run_loop_handoff_ends_conversation(db_session, tmp_path) -> None:
    """When lead.status becomes 'pending_assignment', loop ends with code 0."""
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990hand01",
    )

    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    # Custom pool: write outbound AND flip lead.status to pending_assignment.
    pool = MagicMock()

    async def handoff_enqueue(name, *args, **kwargs):
        async with sf() as db:
            await set_tenant_context(db, tenant.id)
            tf = (
                await db.execute(select(TalkFlow).where(TalkFlow.lead_id == lead.id))
            ).scalar_one()
            db.add(
                OutboundMessage(
                    tenant_id=tenant.id,
                    talkflow_id=tf.id,
                    lead_id=lead.id,
                    provider="fake",
                    message_type="text",
                    body_text="Vou te conectar com um humano.",
                    status="sent",
                    external_id=f"fake_{uuid.uuid4().hex[:8]}",
                    triggered_by="inbound",
                    sent_at=datetime.now(UTC),
                )
            )
            db_lead = (await db.execute(select(Lead).where(Lead.id == lead.id))).scalar_one()
            db_lead.status = "pending_assignment"
            await db.commit()

    pool.enqueue_job = handoff_enqueue

    outputs: list[str] = []
    inputs = iter(["Quero falar com humano"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    assert any("pending_assignment" in o for o in outputs)


async def test_main_runs_end_to_end_with_stubbed_pool(db_session, tmp_path, monkeypatch) -> None:
    """End-to-end smoke: _main creates engine, opens pool, runs loop, tears down."""
    from ai_sdr.cli import pilot as pilot_mod

    tenant = Tenant(slug=f"e2e_{uuid.uuid4().hex[:6]}", display_name="E2E")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    # Point settings.tenants_dir at our tmp_path.
    settings = pilot_mod.get_settings()
    monkeypatch.setattr(settings, "tenants_dir", str(tmp_path))

    # Stub create_pool — production opens a Redis connection; tests don't need it.
    pool_inst = MagicMock()
    pool_inst.enqueue_job = AsyncMock()
    pool_inst.aclose = AsyncMock()

    async def fake_create_pool(*args, **kwargs):
        return pool_inst

    monkeypatch.setattr(pilot_mod, "create_pool", fake_create_pool)

    # Inject :quit so the loop exits immediately.
    inputs = iter([":quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    with pytest.raises(typer.Exit) as exc:
        await pilot_mod._main(tenant.slug, None, "+5511990e2e000")

    assert exc.value.exit_code == 0
    pool_inst.aclose.assert_awaited()  # cleanup happened
