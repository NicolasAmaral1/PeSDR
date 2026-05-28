"""WindowExpiredError + reengagement_template configured → 2 outbound rows
(1 failed text + 1 success template)."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import WindowExpiredError
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


def _tenant_yaml_with_reengagement(slug: str) -> str:
    return f"""id: {slug}
display_name: {slug.title()}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: anthropic_key
messaging:
  provider: fake
  reengagement_template:
    template_ref: reengagement_v1
    language: pt_BR
    params: ["amigo"]
"""


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_window_expired_writes_failed_text_and_sent_template(
    db_session, isolated_tenants_dir, session_factory, monkeypatch
) -> None:
    from ai_sdr.settings import get_settings as _gs
    monkeypatch.setattr(_gs(), "tenants_dir", str(isolated_tenants_dir))

    tenant = Tenant(slug=f"wer_{uuid.uuid4().hex[:6]}", display_name="WER")
    db_session.add(tenant)
    await db_session.flush()

    (isolated_tenants_dir / tenant.slug).mkdir()
    (isolated_tenants_dir / tenant.slug / "tenant.yaml").write_text(
        _tenant_yaml_with_reengagement(tenant.slug)
    )

    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0", content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.flush()
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="oi",
        received_at=datetime.now(UTC), raw={},
    )
    db_session.add(inbound)
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(WindowExpiredError("24h expired"))

    runtime = MagicMock()
    async def step_stub(*a, **kw):
        return MagicMock(response_text="Olá")
    runtime.step = step_stub
    registry = MagicMock()
    registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(OutboundMessage)
        .where(OutboundMessage.lead_id == lead.id)
        .order_by(OutboundMessage.sent_at.asc())
    )).scalars().all()

    # Expect 2 rows: the failed text + the successful template recovery
    assert len(rows) == 2

    text_row = next(r for r in rows if r.message_type == "text")
    template_row = next(r for r in rows if r.message_type == "template")

    assert text_row.status == "failed"
    assert text_row.body_text == "Olá"
    assert text_row.triggered_by == "inbound"
    assert "WindowExpired" in (text_row.error_detail or "")

    assert template_row.status == "sent"
    assert template_row.template_ref == "reengagement_v1"
    assert template_row.template_language == "pt_BR"
    assert template_row.template_params == ["amigo"]
    assert template_row.triggered_by == "window_expired_recovery"
    assert template_row.inbound_message_id == inbound.id
