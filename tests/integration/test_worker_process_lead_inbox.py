"""Worker job tests — advisory lock + status transitions + error taxonomy.

These tests construct a real DB session + a FakeMessagingAdapter, then
invoke `process_lead_inbox` directly (no arq runtime needed) by passing
a minimal ctx dict."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():
    engine = build_engine(get_settings().database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def _ctx(session_factory, adapter, runtime_stub):
    registry = MagicMock()
    registry.get.return_value = adapter
    return {
        "session_factory": session_factory,
        "adapter_registry": registry,
        "runtime": runtime_stub,
    }


async def _setup_tenant_with_lead(db_session, status: str) -> tuple[Tenant, Lead]:
    tenant = Tenant(slug=f"w_{uuid.uuid4().hex[:6]}", display_name="W")
    db_session.add(tenant)
    await db_session.flush()
    # treeflow_versions has RLS — set context before inserting.
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.commit()

    # Re-set after commit (transaction-local).
    await set_tenant_context(db_session, tenant.id)
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999", status=status)
    db_session.add(lead)
    await db_session.flush()

    if status == "active":
        tf = TalkFlow(
            tenant_id=tenant.id,
            lead_id=lead.id,
            treeflow_version_id=tv.id,
            thread_id=f"{tenant.id}:{uuid.uuid4()}",
        )
        db_session.add(tf)
    await db_session.commit()
    return tenant, lead


async def _enqueue_inbound(db_session, tenant, lead, text: str) -> InboundMessageRow:
    await set_tenant_context(db_session, tenant.id)
    row = InboundMessageRow(
        tenant_id=tenant.id,
        provider="whatsapp_cloud",
        external_id=f"ext_{uuid.uuid4().hex[:8]}",
        lead_id=lead.id,
        from_address=lead.whatsapp_e164 or "+x",
        text=text,
        received_at=datetime.now(UTC),
        raw={"text": {"body": text}},
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_pending_lead_does_not_run_step(db_session, session_factory) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="pending_assignment")
    await _enqueue_inbound(db_session, tenant, lead, "first")

    adapter = FakeMessagingAdapter()
    runtime_calls = []

    async def runtime_step_stub(*args, **kwargs):
        runtime_calls.append((args, kwargs))
        raise AssertionError("step() must not be called for pending lead")

    runtime = MagicMock()
    runtime.step = runtime_step_stub

    await process_lead_inbox(
        _ctx(session_factory, adapter, runtime),
        str(tenant.id),
        str(lead.id),
    )
    assert runtime_calls == []
    assert adapter.sent_messages == []


async def test_active_lead_replays_all_queued_in_order(db_session, session_factory) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="active")
    await _enqueue_inbound(db_session, tenant, lead, "first")
    await _enqueue_inbound(db_session, tenant, lead, "second")
    await _enqueue_inbound(db_session, tenant, lead, "third")

    adapter = FakeMessagingAdapter()
    seen_inputs: list[str] = []

    async def runtime_step_stub(session, tenant_arg, talkflow_id, user_input):
        seen_inputs.append(user_input)
        return MagicMock(response_text=f"echo:{user_input}")

    runtime = MagicMock()
    runtime.step = runtime_step_stub

    await process_lead_inbox(
        _ctx(session_factory, adapter, runtime),
        str(tenant.id),
        str(lead.id),
    )
    assert seen_inputs == ["first", "second", "third"]
    assert adapter.sent_messages == [
        ("+5511999999999", "echo:first"),
        ("+5511999999999", "echo:second"),
        ("+5511999999999", "echo:third"),
    ]


async def test_recipient_unreachable_marks_lead_and_stops(db_session, session_factory) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="active")
    msg1 = await _enqueue_inbound(db_session, tenant, lead, "first")
    msg2 = await _enqueue_inbound(db_session, tenant, lead, "second")

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(RecipientUnreachable("number not on WA"))

    async def runtime_step_stub(*args, **kwargs):
        return MagicMock(response_text="hi")

    runtime = MagicMock()
    runtime.step = runtime_step_stub

    await process_lead_inbox(
        _ctx(session_factory, adapter, runtime),
        str(tenant.id),
        str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    # Worker updated rows in a separate session; expire our identity map so
    # the next reads see the freshly-committed values, not session-cached state.
    db_session.expire_all()
    await db_session.refresh(lead)
    assert lead.status == "unreachable"
    assert "unreachable" in (lead.unreachable_reason or "").lower()

    rows = (
        (
            await db_session.execute(
                select(InboundMessageRow).where(InboundMessageRow.lead_id == lead.id)
            )
        )
        .scalars()
        .all()
    )
    statuses = {r.id: r.status for r in rows}
    assert statuses[msg1.id] == "error"
    assert statuses[msg2.id] == "queued"  # loop stopped after first failure


async def test_concurrent_jobs_serialized_by_advisory_lock(db_session, session_factory) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="active")
    await _enqueue_inbound(db_session, tenant, lead, "x")

    # First job acquires the lock in a long-held session; second job should
    # see lock contention and return immediately without processing.
    import asyncio

    adapter = FakeMessagingAdapter()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_runtime_step(*args, **kwargs):
        started.set()
        await asyncio.sleep(0.5)  # hold the lock
        return MagicMock(response_text="x")

    runtime_slow = MagicMock()
    runtime_slow.step = slow_runtime_step

    runtime_fast = MagicMock()
    runtime_fast.step = MagicMock(side_effect=AssertionError("second job should not call step"))

    async def first():
        await process_lead_inbox(
            _ctx(session_factory, adapter, runtime_slow),
            str(tenant.id),
            str(lead.id),
        )
        finished.set()

    async def second():
        await started.wait()  # ensure first acquired the lock
        await process_lead_inbox(
            _ctx(session_factory, adapter, runtime_fast),
            str(tenant.id),
            str(lead.id),
        )

    await asyncio.gather(first(), second())
    assert finished.is_set()
    assert len(adapter.sent_messages) == 1
