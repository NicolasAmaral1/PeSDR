"""End-to-end LangSmith live test. Opt-in via LIVE_LANGSMITH=1 env var.

Sends a trivial LLM call with build_trace_metadata, polls the LangSmith
API for the trace, asserts metadata fields. Skipped by default to keep
the suite hermetic.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


def _skip_if_unconfigured() -> str:
    if os.getenv("LIVE_LANGSMITH") != "1":
        pytest.skip("LIVE_LANGSMITH=1 not set; live LangSmith test is opt-in")
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        pytest.skip("LANGSMITH_API_KEY not set")
    if os.getenv("LANGCHAIN_TRACING_V2") != "true":
        pytest.skip("LANGCHAIN_TRACING_V2=true required for live test")
    return os.getenv("LANGCHAIN_PROJECT", "pesdr-dev")


async def test_trace_arrives_with_metadata() -> None:
    project = _skip_if_unconfigured()

    # Use Anthropic Haiku for a tiny ping — cheap and fast.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY required for the live LLM ping")

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:
        pytest.skip("langchain_anthropic not installed; cannot run live LLM ping")

    from ai_sdr.observability.tracing import build_trace_metadata

    test_marker = f"langsmith-live-test-{uuid.uuid4().hex[:8]}"

    llm = ChatAnthropic(model="claude-haiku-4-5", api_key=api_key, max_tokens=20)
    metadata = build_trace_metadata(trace_origin="simulate")
    metadata["test_marker"] = test_marker  # we look this up in the API

    await llm.ainvoke(
        [{"role": "user", "content": f"Reply with the single word: pong ({test_marker})"}],
        config={"metadata": metadata},
    )

    # Wait a bit for LangSmith to ingest.
    await asyncio.sleep(3)

    # Poll the API for the trace.
    ls_api_key = os.getenv("LANGSMITH_API_KEY")
    async with httpx.AsyncClient(
        base_url="https://api.smith.langchain.com",
        headers={"X-API-Key": ls_api_key, "Content-Type": "application/json"},
        timeout=15.0,
    ) as client:
        # Find runs in the project filtered by our test_marker metadata.
        # LangSmith's runs.query endpoint accepts metadata filters.
        for _attempt in range(5):
            r = await client.post(
                "/runs/query",
                json={
                    "project_name": project,
                    "filter": f'eq(metadata.test_marker, "{test_marker}")',
                    "limit": 5,
                },
            )
            if r.status_code == 200 and r.json().get("runs"):
                runs = r.json()["runs"]
                first = runs[0]
                # Confirm metadata roundtripped
                run_meta = first.get("extra", {}).get("metadata", {})
                assert run_meta.get("test_marker") == test_marker
                assert run_meta.get("trace_origin") == "simulate"
                return
            await asyncio.sleep(2)

        pytest.fail(
            f"trace with test_marker={test_marker} did not appear in LangSmith "
            f"project={project} within ~10s"
        )
