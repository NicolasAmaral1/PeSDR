"""Tests for build_system_messages — provider-aware cache marker placement."""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from ai_sdr.llm.messages import build_system_messages


def test_anthropic_cache_enabled_marks_first_block() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=["<kb>chunk1</kb>"],
        provider="anthropic",
        cache_enabled=True,
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg, SystemMessage)
    assert isinstance(msg.content, list)
    assert len(msg.content) == 2
    assert msg.content[0]["type"] == "text"
    assert msg.content[0]["text"] == "static persona"
    assert msg.content[0]["cache_control"] == {"type": "ephemeral"}
    assert msg.content[1]["type"] == "text"
    assert msg.content[1]["text"] == "<kb>chunk1</kb>"
    assert "cache_control" not in msg.content[1]


def test_anthropic_cache_disabled_omits_marker() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=["<kb>chunk1</kb>"],
        provider="anthropic",
        cache_enabled=False,
    )
    msg = msgs[0]
    assert isinstance(msg.content, list)
    assert "cache_control" not in msg.content[0]
    assert "cache_control" not in msg.content[1]


def test_anthropic_no_dynamic_blocks_single_static_block() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=[],
        provider="anthropic",
        cache_enabled=True,
    )
    msg = msgs[0]
    assert isinstance(msg.content, list)
    assert len(msg.content) == 1
    assert msg.content[0]["cache_control"] == {"type": "ephemeral"}


def test_openai_concatenates_into_single_string() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=["block A", "block B"],
        provider="openai",
        cache_enabled=True,
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg.content, str)
    assert msg.content == "static persona\n\nblock A\n\nblock B"


def test_openai_no_dynamic_blocks_returns_static_only() -> None:
    msgs = build_system_messages(
        static_prompt="just the persona",
        dynamic_blocks=[],
        provider="openai",
        cache_enabled=True,
    )
    assert msgs[0].content == "just the persona"


def test_openai_cache_flag_is_noop_on_structure() -> None:
    """OpenAI doesn't expose a disable; cache_enabled has no effect on output shape."""
    a = build_system_messages("p", ["b"], provider="openai", cache_enabled=True)
    b = build_system_messages("p", ["b"], provider="openai", cache_enabled=False)
    assert a[0].content == b[0].content


def test_unknown_provider_falls_back_to_concat_string() -> None:
    """Any provider that's not 'anthropic' uses the concat fallback (no cache marker)."""
    msgs = build_system_messages("p", ["b"], provider="google_genai", cache_enabled=True)
    assert isinstance(msgs[0].content, str)
    assert msgs[0].content == "p\n\nb"
