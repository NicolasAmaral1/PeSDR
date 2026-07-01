"""Form ingestion — RespondiFormAdapter + webhook route + ingest layer.

Integration tests for spec 2026-06-16 §3. These exercise the adapter's
parse + auth against a real Respondi-shaped payload (validated against
the actual form on 2026-06-25 via webhook.site) + the DB persistence
side (ingest → find_or_create Lead → dedup on retry).

The webhook FastAPI route is exercised separately (in future PR when
FastAPI dispatch through arq is wired end-to-end); for now the
adapter+ingest layers are the meaningful units to test.
"""

from __future__ import annotations

import json
import uuid

import pytest

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.forms.errors import IdentityResolutionError, MalformedPayload, SignatureError
from ai_sdr.forms.factory import build_form_adapter
from ai_sdr.forms.ingest import ingest_form_submission
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.tenant_yaml import FormProviderConfig, ProactiveFirstMessageConfig

pytestmark = pytest.mark.integration


# Real-shaped payload from Respondi webhook.site validation (2026-06-25).
# Phone masked; content otherwise verbatim.
RESPONDI_PAYLOAD = {
    "form": {"form_name": "Mentoria Icônica", "form_id": "QWHmKbnx"},
    "respondent": {
        "status": "completed",
        "date": "2026-06-25 23:46:22",
        "score": None,
        "respondent_id": "686080c8-a87d-47df-81a1-b6a1894150c0",
        "answers": {"...": "..."},
        "raw_answers": [
            {
                "question": {
                    "question_title": "nome?",
                    "question_id": "xlcbkl7s88q",
                    "question_type": "name",
                },
                "answer": "Pietra Teste",
            },
            {
                "question": {
                    "question_title": "email",
                    "question_id": "x6xfxr865en4",
                    "question_type": "email",
                },
                "answer": "Pietra@Test.com",
            },
            {
                "question": {
                    "question_title": "wpp",
                    "question_id": "xwdh1ovnyvsg",
                    "question_type": "phone",
                },
                "answer": {"country": "55", "phone": "11999999999"},
            },
            {
                "question": {
                    "question_title": "faixa fat",
                    "question_id": "x5f9e9l6a1a3",
                    "question_type": "radio",
                },
                "answer": ["De R$50.000 a R$100.000"],
            },
            {
                "question": {
                    "question_title": "nicho",
                    "question_id": "x242gwiqx96gj",
                    "question_type": "text",
                },
                "answer": "Tráfego Pago",
            },
        ],
    },
}


def _cfg(secret: str = "s3cr3t") -> FormProviderConfig:
    return FormProviderConfig(
        enabled=True,
        shared_secret_ref="secrets/respondi_webhook_secret",
        start_treeflow="qualificacao_inicial",
        field_mapping={
            "xlcbkl7s88q": "nome",
            "x6xfxr865en4": "email",
            "xwdh1ovnyvsg": "whatsapp_e164",
            "x5f9e9l6a1a3": "faturamento_mensal_faixa",
            "x242gwiqx96gj": "nicho",
        },
        proactive_first_message=ProactiveFirstMessageConfig(
            enabled=True,
            template_ref="saudacao_mentoria_v1",
            language="pt_BR",
            params=["{{ collected.nome | default('') }}"],
        ),
    )


@pytest.mark.asyncio
async def test_respondi_adapter_parses_real_payload():
    """RespondiFormAdapter extracts external_id, LeadIdentifier, and field_values."""
    secret = "s3cr3t"
    adapter = build_form_adapter("respondi", _cfg(secret), {"respondi_webhook_secret": secret})

    submission = await adapter.handle_submission(
        json.dumps(RESPONDI_PAYLOAD).encode(),
        headers={},
        url_params={"secret": secret},
    )

    assert submission.external_id == "686080c8-a87d-47df-81a1-b6a1894150c0"
    # phone was {"country": "55", "phone": "11999999999"} → normalized E.164.
    assert submission.lead_identifier.whatsapp_e164 == "+5511999999999"
    # email lowercased.
    assert submission.lead_identifier.email == "pietra@test.com"
    # radio answer stripped from single-element list.
    assert submission.field_values["faturamento_mensal_faixa"] == "De R$50.000 a R$100.000"
    # text passthrough.
    assert submission.field_values["nicho"] == "Tráfego Pago"


@pytest.mark.asyncio
async def test_respondi_adapter_rejects_wrong_secret():
    """SignatureError when ?secret= doesn't match. Route MUST return 401."""
    adapter = build_form_adapter("respondi", _cfg("real"), {"respondi_webhook_secret": "real"})

    with pytest.raises(SignatureError):
        await adapter.handle_submission(
            json.dumps(RESPONDI_PAYLOAD).encode(),
            headers={},
            url_params={"secret": "wrong"},
        )


@pytest.mark.asyncio
async def test_respondi_adapter_rejects_malformed_payload():
    """MalformedPayload when body is not valid JSON. Route MUST return 400."""
    adapter = build_form_adapter("respondi", _cfg(), {"respondi_webhook_secret": "s3cr3t"})

    with pytest.raises(MalformedPayload):
        await adapter.handle_submission(
            b"not-json",
            headers={},
            url_params={"secret": "s3cr3t"},
        )


@pytest.mark.asyncio
async def test_ingest_form_submission_creates_lead_and_row(db_session):
    """Happy path — new phone → creates Lead + InboundFormSubmission, status 'queued'."""
    tenant = Tenant(slug=f"form-{uuid.uuid4().hex[:6]}", display_name="Form")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    await db_session.commit()

    adapter = build_form_adapter("respondi", _cfg(), {"respondi_webhook_secret": "s3cr3t"})
    submission = await adapter.handle_submission(
        json.dumps(RESPONDI_PAYLOAD).encode(),
        headers={},
        url_params={"secret": "s3cr3t"},
    )

    await set_tenant_context(db_session, tenant.id)
    result = await ingest_form_submission(db_session, tenant, "respondi", submission)
    await db_session.commit()

    assert result.status == "queued"

    lead = await db_session.get(Lead, result.lead_id)
    assert lead is not None
    assert lead.whatsapp_e164 == "+5511999999999"

    row = await db_session.get(InboundFormSubmission, result.submission_id)
    assert row is not None
    assert row.status == "queued"
    assert row.provider == "respondi"


@pytest.mark.asyncio
async def test_ingest_form_submission_dedups_on_retry(db_session):
    """Same submission twice (Respondi retry) → 2nd call returns skipped_dedupe."""
    tenant = Tenant(slug=f"form-{uuid.uuid4().hex[:6]}", display_name="Form")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    await db_session.commit()

    adapter = build_form_adapter("respondi", _cfg(), {"respondi_webhook_secret": "s3cr3t"})
    submission = await adapter.handle_submission(
        json.dumps(RESPONDI_PAYLOAD).encode(),
        headers={},
        url_params={"secret": "s3cr3t"},
    )

    await set_tenant_context(db_session, tenant.id)
    first = await ingest_form_submission(db_session, tenant, "respondi", submission)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)
    second = await ingest_form_submission(db_session, tenant, "respondi", submission)
    await db_session.commit()

    assert first.status == "queued"
    assert second.status == "skipped_dedupe"
    assert first.submission_id == second.submission_id  # same row
    assert first.lead_id == second.lead_id


@pytest.mark.asyncio
async def test_ingest_form_submission_raises_when_no_phone(db_session):
    """Form without phone → IdentityResolutionError. Route returns 200 anyway."""
    tenant = Tenant(slug=f"form-{uuid.uuid4().hex[:6]}", display_name="Form")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    await db_session.commit()

    payload_no_phone = json.loads(json.dumps(RESPONDI_PAYLOAD))
    payload_no_phone["respondent"]["raw_answers"] = [
        a for a in payload_no_phone["respondent"]["raw_answers"]
        if a["question"]["question_type"] != "phone"
    ]

    adapter = build_form_adapter("respondi", _cfg(), {"respondi_webhook_secret": "s3cr3t"})
    submission = await adapter.handle_submission(
        json.dumps(payload_no_phone).encode(),
        headers={},
        url_params={"secret": "s3cr3t"},
    )

    await set_tenant_context(db_session, tenant.id)
    with pytest.raises(IdentityResolutionError):
        await ingest_form_submission(db_session, tenant, "respondi", submission)
