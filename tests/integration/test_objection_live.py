"""Live Haiku classification test (Plan 4a, spec §4.4).

Requires a real ANTHROPIC_API_KEY in the example tenant's secrets file (or
in the ANTHROPIC_API_KEY env var). Run via:
    uv run pytest tests/integration/test_objection_live.py -v -m live_llm
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from ai_sdr.llm.factory import build_llm
from ai_sdr.schemas.llm_yaml import LLMConfig
from ai_sdr.schemas.treeflow_yaml import GlobalObjection
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.treeflow.classifier import classify

pytestmark = [pytest.mark.live_llm, pytest.mark.integration]


def _load_secrets() -> dict[str, str]:
    """Load tenant secrets, falling back to env var if SOPS isn't available."""
    try:
        return SopsLoader(Path("tenants")).load("example")
    except Exception:
        # Fall back to env var (useful in CI or when SOPS isn't installed)
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            pytest.skip(
                "Neither tenants/example/secrets.enc.yaml decryptable nor ANTHROPIC_API_KEY set"
            )
        return {"anthropic_key": key}


def _haiku_llm() -> object:
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5",
        api_key_ref="secrets/anthropic_key",
    )
    return build_llm(cfg, _load_secrets())


def _example_objections() -> list[GlobalObjection]:
    return [
        GlobalObjection(
            id="preco",
            kb="kb_obj_preco",
            description=(
                "Lead questiona o valor do investimento, acha caro, ou compara com alternativas"
            ),
        ),
        GlobalObjection(
            id="falta_tempo",
            kb="kb_obj_tempo",
            description=(
                "Lead diz que está sem tempo, agenda cheia, ou que esse não é o momento certo"
            ),
        ),
        GlobalObjection(
            id="preciso_pensar",
            kb="kb_obj_pensar",
            description=("Lead pede tempo pra pensar, decidir depois, falar com terceiros antes"),
        ),
    ]


async def test_classifier_detects_price_objection() -> None:
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="tá muito caro pra mim")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id == "preco"
    assert result.confidence >= 0.6


async def test_classifier_detects_time_or_decision_for_ambiguous_message() -> None:
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="não sei se é a hora certa")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id in {"falta_tempo", "preciso_pensar"}


async def test_classifier_returns_null_for_unrelated_message() -> None:
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="qual o whatsapp de vocês?")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id is None


async def test_classifier_picks_one_for_compound_message() -> None:
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="tá muito caro E também preciso pensar")],
        previously_handled=[],
        history_window=4,
    )
    # We accept either — multi-objection-per-turn is V2.
    assert result.objection_id in {"preco", "preciso_pensar"}
