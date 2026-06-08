"""accumulate_tokens reads LangChain usage metadata and adds to a counter dict."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from ai_sdr.flowengine.usage import accumulate_tokens, extract_usage


def _msg_with_usage(input_t: int, cached: int, output: int) -> AIMessage:
    return AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": input_t,
            "output_tokens": output,
            "total_tokens": input_t + output,
            "input_token_details": {"cache_read": cached, "cache_creation": 0},
        },
    )


def test_extract_usage_returns_zero_on_empty_metadata() -> None:
    msg = AIMessage(content="x")
    u = extract_usage(msg)
    assert u == {"input": 0, "input_cached": 0, "output": 0}


def test_extract_usage_reads_token_counts() -> None:
    msg = _msg_with_usage(input_t=100, cached=70, output=30)
    u = extract_usage(msg)
    assert u == {"input": 100, "input_cached": 70, "output": 30}


def test_accumulate_into_empty_running_total() -> None:
    running: dict[str, int] = {}
    accumulate_tokens(running, {"input": 100, "input_cached": 70, "output": 30})
    assert running["input"] == 100
    assert running["input_cached"] == 70
    assert running["output"] == 30
    assert running["total_cost_usd"] == 0  # slot reserved


def test_accumulate_sums_across_turns() -> None:
    running = {"input": 50, "input_cached": 30, "output": 20, "total_cost_usd": 0}
    accumulate_tokens(running, {"input": 100, "input_cached": 70, "output": 30})
    assert running["input"] == 150
    assert running["input_cached"] == 100
    assert running["output"] == 50
