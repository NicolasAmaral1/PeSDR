"""simulate --arch-v2 dispatches inbound text through run_turn."""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.cli.simulate import simulate_v2_turn

from tests.integration.avelum_helpers import seed_avelum_v2


@pytest.mark.asyncio
async def test_simulate_v2_turn_prints_response(db_session: AsyncSession) -> None:
    tenant, tfv = await seed_avelum_v2(db_session)

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
            stdout=buf,
        )
    assert "qual seu segmento" in buf.getvalue()
