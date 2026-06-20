"""Audio inbound transcribed → run_turn → voice outbound, all via fakes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.pipeline import run_turn
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.schemas.tenant_yaml import TenantConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeSynthesizer

from tests.integration.avelum_helpers import seed_avelum_v2

pytestmark = pytest.mark.integration


def _stub_tenant_cfg(slug: str) -> TenantConfig:
    return TenantConfig.model_validate(
        {
            "id": slug,
            "display_name": "Voice E2E stub",
            "timezone": "America/Sao_Paulo",
            "schedule": {"mon-fri": "08:00-22:00"},
            "conversation": {"optout_stop_words": ["sair"]},
            "llm": {
                "default": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "api_key_ref": "secrets/openai_key",
                },
            },
            "guardrails": {
                "allowed_products": ["sdr_smoke"],
                "disallowed_price_pattern": r"R\$\s?\d+",
                "fallback_text": "Vou validar com a equipe.",
            },
            "voice": {
                "response_mode": "match_lead",
                "synthesis": {
                    "provider": "fake",
                    "credentials_ref": "secrets/k",
                    "voice_id": "v1",
                },
            },
        }
    )


class _StubLLM:
    """Runnable-like: returns a fixed TurnDecision regardless of input."""

    async def ainvoke(self, messages):
        return TurnDecision(
            response_text="claro, posso te ajudar com isso",
            response_format=None,
            collected_fields={},
            reasoning="stub",
        )


@pytest.mark.asyncio
async def test_match_lead_audio_inbound_yields_voice_outbound(
    db_session: AsyncSession,
) -> None:
    """audio inbound with preset transcription → run_turn → voice outbound.

    We isolate the outbound path: the normalizer is unit-tested separately,
    so we just preset .transcription on the row to skip that step.
    """
    tenant, tfv = await seed_avelum_v2(db_session)
    treeflow = load_treeflow_v2(tfv.content_yaml)
    tenant_cfg = _stub_tenant_cfg(tenant.slug)

    gcfg = GuardrailConfig(
        disallowed_price_pattern=r"R\$\d+",
        allowed_prices=[],
        allowed_products=["sdr_smoke"],
        fallback_text="Vou validar com a equipe.",
    )

    # Build an audio inbound row with the transcription already populated
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="",  # audio rows have no text body; NOT NULL col requires empty string
        raw={"type": "audio"},
        media_type="audio",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound)
    await db_session.flush()
    # Preset transcription to isolate the outbound voice path
    inbound.transcription = "qual o valor?"

    messaging = FakeMessagingAdapter()

    result = await run_turn(
        db_session,
        tenant=tenant,
        tenant_cfg=tenant_cfg,
        treeflow=treeflow,
        treeflow_version=tfv,
        inbound=inbound,
        llm=_StubLLM(),
        adapter=messaging,
        opt_out_keywords=["sair"],
        guardrail_cfg=gcfg,
        now=datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc),
        voice_cfg=tenant_cfg.voice,
        synthesizer=FakeSynthesizer(),
        storage=FakeStorageAdapter(),
    )

    assert result.outcome == "sent"
    assert messaging.sent_audio, "expected a voice message because last inbound was audio"
    assert not messaging.sent_messages, "text send_text should NOT be called for audio inbound"
