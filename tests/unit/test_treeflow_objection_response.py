"""Unit tests for build_inline_objection_messages (Plan 4a, spec §4.4)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ai_sdr.schemas.treeflow_yaml import (
    ExitCondition,
    NodeObjection,
    NodeSpec,
    Transition,
)
from ai_sdr.treeflow.objection_response import build_inline_objection_messages


def _make_node() -> NodeSpec:
    return NodeSpec(
        id="qualif",
        prompt="Você é uma SDR amigável em PT-BR. Pergunte sobre faturamento.",
        exit_condition=ExitCondition(type="all_fields_filled"),
        next_nodes=[Transition(condition="true", target="END")],
    )


def _make_obj() -> NodeObjection:
    return NodeObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )


def test_anthropic_with_cache_emits_three_blocks():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="conteúdo do KB aqui",
        conversation=[HumanMessage(content="tá caro")],
        cache_enabled=True,
        provider="anthropic",
    )
    assert len(msgs) == 2  # SystemMessage + HumanMessage
    sm = msgs[0]
    assert isinstance(sm, SystemMessage)
    # content should be a list (Anthropic format)
    assert isinstance(sm.content, list)
    assert len(sm.content) == 3  # persona + objection prefix + KB
    assert sm.content[0]["text"] == node.prompt
    assert sm.content[0].get("cache_control") == {"type": "ephemeral"}
    assert "preco" in sm.content[1]["text"]
    assert obj.description in sm.content[1]["text"]
    assert sm.content[1].get("cache_control") == {"type": "ephemeral"}
    assert "conteúdo do KB aqui" in sm.content[2]["text"]
    # Block 3 (KB) is dynamic — NOT cached
    assert "cache_control" not in sm.content[2]


def test_non_anthropic_concatenates_to_single_string():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="KB content",
        conversation=[HumanMessage(content="tá caro")],
        cache_enabled=True,  # ignored for non-anthropic
        provider="openai",
    )
    sm = msgs[0]
    assert isinstance(sm, SystemMessage)
    assert isinstance(sm.content, str)
    assert node.prompt in sm.content
    assert "preco" in sm.content
    assert "KB content" in sm.content


def test_empty_kb_appends_defensive_instruction():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="",
        conversation=[HumanMessage(content="tá caro")],
        cache_enabled=True,
        provider="anthropic",
    )
    sm = msgs[0]
    # Block 3 is the defensive instruction (no KB content)
    block3_text = sm.content[2]["text"]
    assert "peça mais detalhes" in block3_text or "informações suficientes" in block3_text


def test_conversation_appended_after_system():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="x",
        conversation=[HumanMessage(content="oi"), HumanMessage(content="tá caro")],
        cache_enabled=False,
        provider="openai",
    )
    assert len(msgs) == 3  # 1 system + 2 history
    assert msgs[1].content == "oi"
    assert msgs[2].content == "tá caro"
