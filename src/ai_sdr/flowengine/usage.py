"""Token usage extraction + accumulation.

Reads `usage_metadata` from the LangChain AIMessage when available
(works for langchain-anthropic + langchain-openai >= 0.3). The
running total lives on Talk.tokens_consumed JSONB.

Cost computation is reserved for FE-06 (needs a pricing table per
provider/model). FE-01b records token counts only.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


def extract_usage(message: Any) -> dict[str, int]:
    """Return {input, input_cached, output} from an AIMessage.

    Handles missing metadata gracefully (returns zeros).
    """
    out = {"input": 0, "input_cached": 0, "output": 0}
    if not isinstance(message, AIMessage):
        return out
    meta = getattr(message, "usage_metadata", None) or {}
    out["input"] = int(meta.get("input_tokens", 0) or 0)
    out["output"] = int(meta.get("output_tokens", 0) or 0)
    details = meta.get("input_token_details") or {}
    out["input_cached"] = int(details.get("cache_read", 0) or 0)
    return out


def accumulate_tokens(
    running: dict[str, Any], increment: dict[str, int]
) -> None:
    """Add token increments into a running counter dict in place."""
    for key in ("input", "input_cached", "output"):
        running[key] = int(running.get(key, 0) or 0) + int(increment.get(key, 0) or 0)
    # total_cost_usd is a reserved slot; FE-06 populates it from a pricing table.
    running.setdefault("total_cost_usd", 0)
