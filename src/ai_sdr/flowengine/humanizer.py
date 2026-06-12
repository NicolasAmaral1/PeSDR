"""Humanization post-processor for FlowEngine v2 sender (FE-03b §4).

Pure function. Splits the LLM's response_text into chunks (default by
paragraph delimiter \\n\\n) and computes a typing-style delay before each
non-first chunk. Voice mode short-circuits to a single chunk unless the
tenant opts into apply_to_voice.

The actual sleep + send loop lives in flowengine.sender. This module
only computes the (text, delay_before_ms) tuples.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class HumanizationConfig:
    """Per-tenant config from tenant.yaml > humanization."""

    enabled: bool = True
    chunk_delimiter: str = "\n\n"
    chars_per_second_min: float = 8.0
    chars_per_second_max: float = 15.0
    min_delay_ms: int = 800
    max_delay_ms: int = 4000
    apply_to_voice: bool = False


@dataclass(frozen=True)
class Chunk:
    """One outbound message in the humanized sequence."""

    text: str
    delay_before_ms: int  # 0 for first chunk


def humanize(
    response_text: str,
    config: HumanizationConfig,
    *,
    is_voice: bool = False,
) -> list[Chunk]:
    """Split response into chunks with typing-style delays.

    Voice mode short-circuits to single chunk (per spec §13.5) unless
    config.apply_to_voice. Humanization disabled → single chunk.
    Empty / whitespace-only input → empty list.
    """
    if is_voice and not config.apply_to_voice:
        return [Chunk(text=response_text, delay_before_ms=0)] if response_text.strip() else []

    if not config.enabled:
        return [Chunk(text=response_text, delay_before_ms=0)] if response_text.strip() else []

    raw_chunks = [c.strip() for c in response_text.split(config.chunk_delimiter) if c.strip()]
    if not raw_chunks:
        return []

    chunks = [Chunk(text=raw_chunks[0], delay_before_ms=0)]
    for next_chunk_text in raw_chunks[1:]:
        if config.chars_per_second_min == config.chars_per_second_max:
            typing_speed = config.chars_per_second_min
        else:
            typing_speed = random.uniform(
                config.chars_per_second_min,
                config.chars_per_second_max,
            )
        typing_ms = int(len(next_chunk_text) / typing_speed * 1000)
        delay = max(config.min_delay_ms, min(config.max_delay_ms, typing_ms))
        chunks.append(Chunk(text=next_chunk_text, delay_before_ms=delay))

    return chunks
