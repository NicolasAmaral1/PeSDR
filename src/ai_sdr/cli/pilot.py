"""ai-sdr pilot — multi-turn REPL driving the worker pipeline via FakeAdapter.

Drives process_lead_inbox end-to-end with a real LLM and real DB/Redis,
but no Meta Cloud API. Each REPL turn: INSERT inbound row → enqueue arq job
→ poll outbound_messages for a new row → print body_text. End signals
(handoff, cold, failed audit, timeout, :quit, Ctrl+C) exit cleanly.

Scope and non-goals: see docs/superpowers/specs/2026-06-01-pilot-harness-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

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
        raise FileNotFoundError(f"No treeflow YAML in {tf_dir}. Add one or pass --treeflow <id>.")
    names = ", ".join(f.stem for f in files)
    raise ValueError(
        f"Multiple treeflows in {tf_dir}: {names}. Pass --treeflow <id> to disambiguate."
    )


def format_status_line(lead: Lead, talkflow: TalkFlow, turn_count: int) -> str:
    """One-line summary printed by the `:status` REPL command."""
    return (
        f"lead_id={str(lead.id)[:8]}… "
        f"lead.status={lead.status} · "
        f"talkflow.status={talkflow.status} · "
        f"turns={turn_count}"
    )


# --- Async DB helpers ---


async def poll_for_outbound(
    session: AsyncSession,
    lead_id: uuid.UUID,
    after: datetime,
    max_seconds: float = 30.0,
    interval_seconds: float = 0.5,
) -> OutboundMessage | None:
    """Poll outbound_messages for the first row with created_at > after.

    Returns the row when found, or None after max_seconds. The caller is
    responsible for setting tenant RLS context on the session before calling.
    """
    elapsed = 0.0
    while elapsed < max_seconds:
        result = await session.execute(
            select(OutboundMessage)
            .where(OutboundMessage.lead_id == lead_id)
            .where(OutboundMessage.created_at > after)
            .order_by(OutboundMessage.created_at.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds
    return None


async def _seed_session(
    session: AsyncSession,
    *,
    tenants_dir: Path,
    slug: str,
    treeflow_id: str,
    from_address: str,
) -> tuple[Tenant, Lead, TalkFlow]:
    """Set up a fresh pilot session: tenant lookup, treeflow_version, lead, talkflow.

    Caller is responsible for setting RLS context BEFORE this runs (the
    helper does its own commits but does not switch tenant). Returns
    (tenant, lead, talkflow) for the caller to use in the REPL loop.

    Raises:
        ValueError: tenant slug not found in DB.
        FileNotFoundError: treeflow YAML file missing.
    """
    # 1. Look up tenant.
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        # The INSERT snippet is operator guidance shown in the error message —
        # it is never executed as SQL.
        msg = f"tenant '{slug}' not in DB. Add it via psql before piloting: INSERT INTO tenants (slug, display_name) VALUES ('{slug}', '<name>');"  # noqa: S608, E501
        raise ValueError(msg)

    # 2. Load YAML, compute content_hash, find-or-create TreeflowVersion.
    yaml_path = tenants_dir / slug / "treeflows" / f"{treeflow_id}.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"treeflow YAML not found: {yaml_path}")
    content = yaml_path.read_text()
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    tv = (
        await session.execute(
            select(TreeflowVersion).where(
                TreeflowVersion.tenant_id == tenant.id,
                TreeflowVersion.treeflow_id == treeflow_id,
                TreeflowVersion.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if tv is None:
        tv = TreeflowVersion(
            tenant_id=tenant.id,
            treeflow_id=treeflow_id,
            version="pilot",
            content_hash=content_hash,
            content_yaml=content,
        )
        session.add(tv)
        await session.flush()

    # 3. Create fresh lead + talkflow.
    lead = Lead(tenant_id=tenant.id, whatsapp_e164=from_address, status="active")
    session.add(lead)
    await session.flush()

    talkflow = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    session.add(talkflow)
    await session.commit()
    return tenant, lead, talkflow
