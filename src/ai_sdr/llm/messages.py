"""build_system_messages — render the system portion with per-provider cache markers.

For Anthropic, the first block carries ``cache_control={"type": "ephemeral"}``
when ``cache_enabled=True`` (Plan 3, spec §8). For all other providers we
concatenate into a single string: OpenAI auto-caches prefixes ≥1024 tok without
an explicit marker, and other providers (gemini, deepseek, ollama, ...) simply
don't need a cache marker for MVP — the concat path is the safe fallback.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import SystemMessage


def build_system_messages(
    static_prompt: str,
    dynamic_blocks: list[str],
    provider: str,
    cache_enabled: bool = True,
) -> list[SystemMessage]:
    """Return the system messages for a single LLM turn.

    Always returns a one-element list — the compiler can append history +
    ``HumanMessage(user_input)`` downstream without worrying about provider.

    Args:
        static_prompt: Stable persona/instructions that should be cached.
        dynamic_blocks: Per-turn blocks (e.g., KB chunks, guardrails section)
            that change between turns and must NOT be cached.
        provider: LLM provider name (e.g. "anthropic", "openai", "google_genai").
            Only ``"anthropic"`` gets explicit ``cache_control`` markers; every
            other value falls through to the concat-string fallback.
        cache_enabled: When True and provider="anthropic", marks the static
            prompt as ephemeral-cacheable. No effect on other providers.
    """
    if provider == "anthropic":
        first: dict[str, Any] = {"type": "text", "text": static_prompt}
        if cache_enabled:
            first["cache_control"] = {"type": "ephemeral"}
        content: list[str | dict[Any, Any]] = [first]
        for block in dynamic_blocks:
            content.append({"type": "text", "text": block})
        return [SystemMessage(content=content)]

    # Fallback: concatenate into one string. Covers OpenAI (auto-caches prefixes)
    # and any other provider that doesn't need an explicit cache marker.
    parts = [static_prompt, *dynamic_blocks]
    return [SystemMessage(content="\n\n".join(parts))]
