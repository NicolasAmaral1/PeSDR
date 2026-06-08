"""simulate --arch-v2 dispatches inbound text through run_turn."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.cli.simulate import simulate_v2_turn
from ai_sdr.schemas.tenant_yaml import TenantConfig

from tests.integration.avelum_helpers import seed_avelum_v2


def _stub_tenant_cfg(slug: str) -> TenantConfig:
    """Build a minimal valid TenantConfig in-memory for the simulate v2 test."""
    return TenantConfig.model_validate({
        "id": slug,
        "display_name": "Avelum stub",
        "timezone": "America/Sao_Paulo",
        "schedule": {"mon-fri": "08:00-22:00"},
        "conversation": {"optout_stop_words": ["sair", "parar"]},
        "llm": {
            "default": {
                "provider": "openai",
                "model": "gpt-5-mini",
                "api_key_ref": "secrets/openai_key",
            },
        },
        "guardrails": {
            "allowed_products": ["sdr_avelum"],
            "disallowed_price_pattern": r"R\$\s?\d+",
            "fallback_text": "deixa eu confirmar com o time",
        },
    })


@pytest.mark.asyncio
async def test_simulate_v2_turn_prints_response(db_session: AsyncSession) -> None:
    tenant, tfv = await seed_avelum_v2(db_session)
    tenant_cfg = _stub_tenant_cfg(tenant.slug)

    fake_llm_response = AsyncMock(return_value=__import__(
        "tests.fixtures.canned_decisions", fromlist=["greeting_decision"]
    ).greeting_decision())

    with (
        patch("ai_sdr.cli.simulate._llm_for_simulate", return_value=AsyncMock(ainvoke=fake_llm_response)),
        patch("ai_sdr.cli.simulate._adapter_for_simulate") as adapter_factory,
    ):
        adapter = adapter_factory.return_value
        adapter.send_text = AsyncMock(return_value=type("R", (), {
            "external_id": "ext-sim", "status": "sent", "error_detail": None,
        })())
        buf = io.StringIO()
        await simulate_v2_turn(
            session=db_session, tenant=tenant, treeflow_version=tfv,
            lead_phone="+5511999999999", inbound_text="oi",
            tenant_cfg=tenant_cfg,
            stdout=buf,
        )
    assert "qual seu segmento" in buf.getvalue()
