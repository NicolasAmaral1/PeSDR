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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from arq import create_pool
from arq.connections import RedisSettings
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings

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
            version=f"pilot-{content_hash[:8]}",
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


# --- Loop ---


async def _run_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    pool: Any,  # arq pool; duck-typed so tests can pass a MagicMock with .enqueue_job
    tenant: Tenant,
    lead: Lead,
    talkflow: TalkFlow,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> int:
    """REPL loop. Returns the exit code per spec §4.4.

    Test-friendly: input_fn(prompt) -> str (production wraps stdin's input)
    and output_fn(line) -> None (production wraps console.print). Per-turn
    flow per spec §4.2; end signals checked in the order from §4.4.
    """
    turn_count = 0

    while True:
        user_text = input_fn("> ").strip()

        if user_text == ":quit":
            output_fn("[encerrado]")
            return 0

        if user_text == ":status":
            async with session_factory() as db:
                await set_tenant_context(db, tenant.id)
                refreshed_lead = (
                    await db.execute(select(Lead).where(Lead.id == lead.id))
                ).scalar_one()
                refreshed_tf = (
                    await db.execute(select(TalkFlow).where(TalkFlow.id == talkflow.id))
                ).scalar_one()
                output_fn(format_status_line(refreshed_lead, refreshed_tf, turn_count))
            continue

        if not user_text:
            continue

        # 1. INSERT inbound, COMMIT, capture timestamp.
        before_send = datetime.now(UTC)
        async with session_factory() as db:
            await set_tenant_context(db, tenant.id)
            db.add(
                InboundMessageRow(
                    tenant_id=tenant.id,
                    provider="fake",
                    external_id=f"pilot_{uuid.uuid4().hex}",
                    lead_id=lead.id,
                    from_address=lead.whatsapp_e164,
                    text=user_text,
                    received_at=datetime.now(UTC),
                    raw={},
                )
            )
            await db.commit()

        # 2. Enqueue arq job. (Production: real arq pool. Tests: MagicMock that
        # simulates the worker by writing the outbound row directly.)
        await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead.id))

        # 3. Poll for the new outbound row + check end signals.
        async with session_factory() as db:
            await set_tenant_context(db, tenant.id)
            row = await poll_for_outbound(db, lead.id, before_send)

            if row is None:
                output_fn(
                    "[timeout — worker não respondeu em 30s. "
                    "Verifica `docker compose ps` e `docker compose logs worker`.]"
                )
                return 1

            if row.status == "failed":
                output_fn(
                    f"[falha no processamento — {row.error_detail}. Verifica logs do worker.]"
                )
                return 1

            # End-signal check order per spec §4.4:
            refreshed_lead = (await db.execute(select(Lead).where(Lead.id == lead.id))).scalar_one()
            refreshed_tf = (
                await db.execute(select(TalkFlow).where(TalkFlow.id == talkflow.id))
            ).scalar_one()

            output_fn(f"agente: {row.body_text}")
            turn_count += 1

            if refreshed_lead.status == "pending_assignment":
                output_fn("[lead encaminhado pro operador humano — status=pending_assignment]")
                return 0
            if refreshed_tf.status == "cold":
                output_fn("[talkflow esfriou — sem mais respostas]")
                return 0


# --- Entry point ---


@pilot_app.command("pilot")
def pilot(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug (required)")],
    treeflow: Annotated[
        str | None,
        typer.Option("--treeflow", help="Treeflow id (yaml basename, no .yaml)"),
    ] = None,
    from_address: Annotated[
        str | None,
        typer.Option("--from-address", help="Lead whatsapp_e164 (default: random)"),
    ] = None,
) -> None:
    """Run a multi-turn pilot conversation against the live worker pipeline."""
    asyncio.run(_main(tenant, treeflow, from_address))


async def _main(tenant_slug: str, treeflow_arg: str | None, from_address_arg: str | None) -> None:
    settings = get_settings()
    tenants_dir = Path(settings.tenants_dir)

    # Resolve treeflow id (filesystem only — no DB yet).
    try:
        treeflow_id = resolve_treeflow(tenants_dir, tenant_slug, treeflow_arg)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    from_address = from_address_arg or generate_whatsapp_e164()

    engine = create_async_engine(settings.database_url, future=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    pool = None
    try:
        # Seed and grab the rows.
        async with sf() as db:
            try:
                tenant_row, lead, talkflow = await _seed_session(
                    db,
                    tenants_dir=tenants_dir,
                    slug=tenant_slug,
                    treeflow_id=treeflow_id,
                    from_address=from_address,
                )
            except (ValueError, FileNotFoundError) as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1) from e

        # Open the arq pool (Redis must be reachable).
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

        # Header.
        console.print(
            f"[cyan]Piloto {tenant_slug} · lead {from_address} · treeflow={treeflow_id}[/cyan]"
        )
        console.print("[dim](:quit ou Ctrl+C pra sair, :status pra ver estado)[/dim]")

        # Run the REPL. KeyboardInterrupt is caught here for clean teardown.
        try:
            exit_code = await _run_loop(
                session_factory=sf,
                pool=pool,
                tenant=tenant_row,
                lead=lead,
                talkflow=talkflow,
                input_fn=input,
                output_fn=console.print,
            )
        except KeyboardInterrupt:
            console.print("\n[dim][encerrado][/dim]")
            exit_code = 0

        raise typer.Exit(exit_code)
    finally:
        if pool is not None:
            await pool.aclose()
        await engine.dispose()
