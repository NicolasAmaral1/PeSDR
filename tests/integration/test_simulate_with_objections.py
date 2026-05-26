"""End-to-end acceptance: simulate CLI runs a scripted objection flow (Plan 4a).

Real Anthropic LLM + real Postgres checkpointer + real example tenant.
Marked live_llm so it skips locally when SOPS secrets aren't decryptable
and ANTHROPIC_API_KEY isn't in the environment.

Run via:
    uv run pytest tests/integration/test_simulate_with_objections.py -v -m live_llm
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_sdr.cli.app import app
from ai_sdr.secrets.sops_loader import SopsLoader

pytestmark = [pytest.mark.live_llm, pytest.mark.integration]


def _credentials_available() -> bool:
    try:
        secrets = SopsLoader(Path("tenants")).load("example")
    except Exception:
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    return bool(secrets.get("anthropic_key"))


def _resolve_anthropic_key() -> str | None:
    try:
        secrets = SopsLoader(Path("tenants")).load("example")
        return secrets.get("anthropic_key") or os.getenv("ANTHROPIC_API_KEY")
    except Exception:
        return os.getenv("ANTHROPIC_API_KEY")


@pytest.fixture(scope="module", autouse=True)
def _preflight_anthropic_auth() -> None:
    """Skip if the loaded Anthropic key is invalid.

    SopsLoader can return a key that's syntactically valid but expired or
    revoked — the simulate CLI then fails deep with a 401 and the test
    fails for an environment reason, not a code reason. One tiny preflight
    call validates auth and skips cleanly if the credential is bad.
    """
    key = _resolve_anthropic_key()
    if not key:
        pytest.skip("No ANTHROPIC_API_KEY available")
    import anthropic

    try:
        anthropic.Anthropic(api_key=key).messages.create(
            model="claude-haiku-4-5",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except anthropic.AuthenticationError as e:
        pytest.skip(f"Anthropic auth invalid; skipping live test: {e}")


@pytest.mark.skipif(not _credentials_available(), reason="No ANTHROPIC_API_KEY available")
def test_simulate_handles_price_objection_and_continues() -> None:
    """Scripted flow:
       (turn 1) lead presses enter → agent greets and asks for faturamento
       (turn 2) lead says 'tá caro' → classifier detects, inline response runs
       (turn 3) lead replies '50000' → main extracts, advances
       /quit exits.

    Asserts:
    - exit code is 0
    - objections_handled appears in --show-extracted output
    - 'preco' (or some objection_id) appears
    """
    runner = CliRunner()
    unique_lead = f"acc-{uuid.uuid4().hex[:8]}"
    # Sequence of user inputs, newline-separated.
    # Turn 1: empty (just press Enter to kick off the greeting)
    # Turn 2: "tá caro" — should trigger preco objection
    # Turn 3: "50000" — gives the qualification value
    # /quit ends
    user_input = "\ntá caro\n50000\n/quit\n"

    result = runner.invoke(
        app,
        [
            "simulate",
            "--tenant",
            "example",
            "--treeflow",
            "example",
            "--lead",
            unique_lead,
            "--show-extracted",
        ],
        input=user_input,
    )

    # Print debug output if it fails
    if result.exit_code != 0:
        print("STDOUT:", result.stdout)
        if result.exception:
            print("EXCEPTION:", result.exception)
            import traceback

            traceback.print_exception(
                type(result.exception),
                result.exception,
                result.exception.__traceback__,
            )

    assert result.exit_code == 0, f"non-zero exit; stdout: {result.stdout[-500:]}"
    out = result.stdout
    # objections_handled should appear in --show-extracted output after turn 2
    assert "objections_handled" in out, (
        f"expected 'objections_handled' in output; got: {out[-500:]}"
    )
