"""WindowExpiredError without reengagement_template configured -> marks error, no template sent."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import WindowExpiredError
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


def _tenant_yaml_no_reengagement(slug: str) -> str:
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
"""


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_window_expired_no_template_marks_error(
    db_session, isolated_tenants_dir, session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "tenants_dir", str(isolated_tenants_dir))

    tenant = Tenant(slug=f"wen_{uuid.uuid4().hex[:6]}", display_name="WEN")
    db_session.add(tenant)
    await db_session.flush()

    (isolated_tenants_dir / tenant.slug).mkdir()
    (isolated_tenants_dir / tenant.slug / "tenant.yaml").write_text(
        _tenant_yaml_no_reengagement(tenant.slug)
    )

    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml=(
            "id: t1\nversion: 1.0.0\ndisplay_name: T1\nentry_node: n1\n"
            "nodes:\n  - id: n1\n    prompt: hi\n"
            "    exit_condition:\n      type: all_fields_filled\n"
            '    next_nodes:\n      - condition: "true"\n        target: END\n'
        ),
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="whatsapp_cloud",
        external_id=f"x_{uuid.uuid4().hex}",
        lead_id=lead.id,
        from_address="+5511999",
        text="oi",
        received_at=datetime.now(UTC),
        raw={},
    )
    db_session.add(inbound)
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(WindowExpiredError("window expired"))
    runtime = MagicMock()

    async def step_stub(*a, **kw):
        return MagicMock(response_text="hi")

    runtime.step = step_stub
    registry = MagicMock()
    registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id),
        str(lead.id),
    )

    assert adapter.sent_messages == []
    assert adapter.sent_templates == []  # no template sent

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(inbound)
    assert inbound.status == "error"
    assert "window_expired" in (inbound.error_detail or "")
