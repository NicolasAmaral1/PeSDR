"""ai-sdr outbound — query the outbound_messages audit table."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings

outbound_app = typer.Typer(help="Outbound messages audit query")
console = Console()


def _make_session() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _load_tenant(session: AsyncSession, slug: str) -> Tenant:
    t = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if t is None:
        console.print(f"[red]tenant not found: {slug}[/red]")
        raise typer.Exit(1)
    return t


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


@outbound_app.command("list")
def list_(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug (required)")],
    lead: Annotated[str | None, typer.Option("--lead", help="Filter by lead UUID")] = None,
    status: Annotated[
        str,
        typer.Option("--status", help="Filter: sent | failed | all (default all)"),
    ] = "all",
    limit: Annotated[int, typer.Option("--limit", help="Max rows to display (default 50)")] = 50,
) -> None:
    """List outbound messages for a tenant, ordered by most recent first."""
    asyncio.run(_list_async(tenant, lead, status, limit))


async def _list_async(
    tenant_slug: str, lead_filter: str | None, status_filter: str, limit: int
) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        t = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, t.id)
        stmt = select(OutboundMessage).order_by(OutboundMessage.sent_at.desc())
        if status_filter != "all":
            if status_filter not in ("sent", "failed"):
                console.print(
                    f"[red]invalid --status: {status_filter!r} (use sent|failed|all)[/red]"
                )
                raise typer.Exit(1)
            stmt = stmt.where(OutboundMessage.status == status_filter)
        if lead_filter:
            stmt = stmt.where(OutboundMessage.lead_id == uuid.UUID(lead_filter))
        stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            console.print(f"[yellow]no outbound messages (status={status_filter!r})[/yellow]")
            await engine.dispose()
            return

        table = Table(title=f"Outbound — {tenant_slug} ({status_filter}, last {limit})")
        table.add_column("Sent At", no_wrap=True)
        table.add_column("Type")
        table.add_column("Lead", no_wrap=True)
        table.add_column("Trigger", no_wrap=True)
        table.add_column("Status")
        table.add_column("Content / Template")
        table.add_column("External ID", no_wrap=True)
        for r in rows:
            content = (
                _truncate(r.body_text, 40)
                if r.message_type == "text"
                else f"{r.template_ref} {r.template_params or []}"
            )
            content = _truncate(content, 60)
            if r.status == "failed":
                content = f"{content} :: {_truncate(r.error_detail, 30)}"
            table.add_row(
                r.sent_at.strftime("%Y-%m-%d %H:%M:%S"),
                r.message_type,
                str(r.lead_id)[:8] + "…",
                r.triggered_by,
                ("[green]sent[/green]" if r.status == "sent" else "[red]failed[/red]"),
                content,
                _truncate(r.external_id, 18),
            )
        console.print(table)
    await engine.dispose()
