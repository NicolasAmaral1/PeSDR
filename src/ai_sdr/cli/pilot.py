"""ai-sdr pilot — multi-turn REPL driving the worker pipeline via FakeAdapter.

Drives process_lead_inbox end-to-end with a real LLM and real DB/Redis,
but no Meta Cloud API. Each REPL turn: INSERT inbound row → enqueue arq job
→ poll outbound_messages for a new row → print body_text. End signals
(handoff, cold, failed audit, timeout, :quit, Ctrl+C) exit cleanly.

Scope and non-goals: see docs/superpowers/specs/2026-06-01-pilot-harness-design.md.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow


pilot_app = typer.Typer(help="Drive the worker pipeline via terminal — fake adapter, real LLM.")
console = Console()


# --- Pure helpers (no I/O) ---


def generate_whatsapp_e164() -> str:
    """Random E.164-style number for a fresh pilot lead. Format: +5511990 + 6 hex."""
    return f"+5511990{secrets.token_hex(3)}"


def resolve_treeflow(tenants_dir: Path, slug: str, requested: str | None) -> str:
    """Determine which treeflow id to seed.

    Explicit `requested` always wins. Otherwise scan
    `tenants/<slug>/treeflows/*.yaml`: if exactly 1 file, return its stem;
    if 0 or >1, raise with a helpful message.
    """
    if requested:
        return requested
    tf_dir = tenants_dir / slug / "treeflows"
    if not tf_dir.is_dir():
        raise FileNotFoundError(
            f"treeflows directory not found: {tf_dir}. "
            f"Ensure tenants/{slug}/treeflows/ exists with at least one .yaml file."
        )
    files = sorted(tf_dir.glob("*.yaml"))
    if len(files) == 1:
        return files[0].stem
    if len(files) == 0:
        raise FileNotFoundError(
            f"No treeflow YAML in {tf_dir}. "
            f"Add one or pass --treeflow <id>."
        )
    names = ", ".join(f.stem for f in files)
    raise ValueError(
        f"Multiple treeflows in {tf_dir}: {names}. "
        f"Pass --treeflow <id> to disambiguate."
    )


def format_status_line(lead: Lead, talkflow: TalkFlow, turn_count: int) -> str:
    """One-line summary printed by the `:status` REPL command."""
    return (
        f"lead_id={str(lead.id)[:8]}… "
        f"lead.status={lead.status} · "
        f"talkflow.status={talkflow.status} · "
        f"turns={turn_count}"
    )
