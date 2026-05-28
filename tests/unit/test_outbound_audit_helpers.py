"""record_outbound_sent + record_outbound_failed — shape correctness."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)


def _stub_session() -> MagicMock:
    s = MagicMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    return s


def _tenant():
    return SimpleNamespace(id=uuid.uuid4(), slug="t")


def _talkflow():
    return SimpleNamespace(id=uuid.uuid4())


def _lead():
    return SimpleNamespace(id=uuid.uuid4())


async def test_record_sent_text_fills_body_only() -> None:
    db = _stub_session()
    row = await record_outbound_sent(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="text",
        triggered_by="inbound",
        body_text="Olá",
        external_id="wamid.X",
        sent_at=datetime.now(UTC),
        inbound_message_id=uuid.uuid4(),
    )
    assert row.status == "sent"
    assert row.message_type == "text"
    assert row.body_text == "Olá"
    assert row.template_ref is None
    assert row.template_params is None
    assert row.external_id == "wamid.X"
    assert row.error_detail is None
    assert row.triggered_by == "inbound"
    db.add.assert_called_once_with(row)
    db.flush.assert_awaited_once()


async def test_record_sent_template_fills_template_only() -> None:
    db = _stub_session()
    row = await record_outbound_sent(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="template",
        triggered_by="follow_up_scanner",
        template_ref="followup_24h_v1",
        template_language="pt_BR",
        template_params=["amigo"],
        external_id="wamid.Y",
        sent_at=datetime.now(UTC),
        follow_up_job_id=uuid.uuid4(),
    )
    assert row.status == "sent"
    assert row.message_type == "template"
    assert row.template_ref == "followup_24h_v1"
    assert row.template_language == "pt_BR"
    assert row.template_params == ["amigo"]
    assert row.body_text is None


async def test_record_failed_carries_error_detail() -> None:
    db = _stub_session()
    row = await record_outbound_failed(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="text",
        triggered_by="inbound",
        body_text="Olá",
        error_detail="RecipientUnreachable: number not on WA",
        sent_at=datetime.now(UTC),
        inbound_message_id=uuid.uuid4(),
    )
    assert row.status == "failed"
    assert row.error_detail == "RecipientUnreachable: number not on WA"
    assert row.external_id is None


async def test_record_failed_template_carries_template_fields() -> None:
    db = _stub_session()
    row = await record_outbound_failed(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="template",
        triggered_by="follow_up_scanner",
        template_ref="x_v1",
        template_language="pt_BR",
        template_params=["v"],
        error_detail="PolicyError: ...",
        sent_at=datetime.now(UTC),
        follow_up_job_id=uuid.uuid4(),
    )
    assert row.message_type == "template"
    assert row.template_ref == "x_v1"
    assert row.template_params == ["v"]
    assert row.body_text is None
