"""assemble_prompt produces correctly-ordered messages with cache_control."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ai_sdr.flowengine.system_prompt import (
    CachedLayer,
    FreshLayer,
    assemble_prompt,
)


def test_assemble_returns_three_messages() -> None:
    cached = CachedLayer(text="CACHED")
    fresh = FreshLayer(text="FRESH")
    msgs = assemble_prompt(cached, fresh, inbound_text="oi mira")
    assert len(msgs) == 3


def test_first_message_is_cached_system_with_cache_control() -> None:
    cached = CachedLayer(text="CACHED")
    fresh = FreshLayer(text="FRESH")
    msgs = assemble_prompt(cached, fresh, inbound_text="oi")
    assert isinstance(msgs[0], SystemMessage)
    # content must be a list of blocks with cache_control on the text block.
    assert isinstance(msgs[0].content, list)
    assert msgs[0].content[0]["type"] == "text"
    assert msgs[0].content[0]["text"] == "CACHED"
    assert msgs[0].content[0]["cache_control"] == {"type": "ephemeral"}


def test_second_message_is_fresh_system_without_cache_control() -> None:
    cached = CachedLayer(text="CACHED")
    fresh = FreshLayer(text="FRESH")
    msgs = assemble_prompt(cached, fresh, inbound_text="oi")
    assert isinstance(msgs[1], SystemMessage)
    # Fresh layer can be a plain string (no cache_control needed).
    assert msgs[1].content == "FRESH" or (
        isinstance(msgs[1].content, list) and "cache_control" not in msgs[1].content[0]
    )


def test_third_message_is_human_inbound() -> None:
    cached = CachedLayer(text="CACHED")
    fresh = FreshLayer(text="FRESH")
    msgs = assemble_prompt(cached, fresh, inbound_text="oi mira")
    assert isinstance(msgs[2], HumanMessage)
    assert msgs[2].content == "oi mira"
