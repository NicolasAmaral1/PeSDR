"""build_cached_layer produces deterministic, stable-per-treeflow output."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.system_prompt import CachedLayer, build_cached_layer
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2


MINIMAL_TF = """
schema_version: 1
id: t
version: "1.0.0"
sdr_persona:
  voice: |
    Tom PT-BR informal, frases curtas.
  conduct: |
    1. Sempre reconheca antes de perguntar.
    2. Nunca invente precos.
  examples:
    - context: "lead pergunta preco antes da qualificacao"
      bad_response: "O investimento e R$2k"
      good_response: "Antes do preco, qual seu volume?"
      why: "preco sem contexto vira objecao imediata"
entry_node: saudacao
nodes:
  - id: saudacao
    objetivo: x
    bridge_instruction: ""
    collects: []
    exit_condition: {type: all_fields_filled}
    next_nodes: []
"""


def test_cached_layer_includes_persona_voice_and_conduct() -> None:
    tf = load_treeflow_v2(MINIMAL_TF)
    layer = build_cached_layer(tf)
    assert isinstance(layer, CachedLayer)
    assert "Tom PT-BR informal" in layer.text
    assert "Sempre reconheca" in layer.text
    assert "Nunca invente precos" in layer.text


def test_cached_layer_includes_examples_when_present() -> None:
    tf = load_treeflow_v2(MINIMAL_TF)
    layer = build_cached_layer(tf)
    assert "preco sem contexto" in layer.text
    assert "Antes do preco" in layer.text


def test_cached_layer_includes_operating_instructions() -> None:
    tf = load_treeflow_v2(MINIMAL_TF)
    layer = build_cached_layer(tf)
    assert "OPERATING INSTRUCTIONS" in layer.text
    assert "strict JSON" in layer.text or "TurnDecision" in layer.text
    assert "current_node" in layer.text


def test_cached_layer_includes_escalation_guidance() -> None:
    tf = load_treeflow_v2(MINIMAL_TF)
    layer = build_cached_layer(tf)
    assert "request_human_escalation" in layer.text
    assert "professional" in layer.text.lower()


def test_cached_layer_includes_sentinel_awareness() -> None:
    tf = load_treeflow_v2(MINIMAL_TF)
    layer = build_cached_layer(tf)
    assert "suspect_injection_attempt" in layer.text


def test_cached_layer_is_deterministic_per_treeflow() -> None:
    tf = load_treeflow_v2(MINIMAL_TF)
    a = build_cached_layer(tf).text
    b = build_cached_layer(tf).text
    assert a == b
