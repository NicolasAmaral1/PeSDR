"""`ai-sdr follow-ups` — operator visibility + manual control of scheduled HSM templates.

Commands:
  list    --tenant <slug> [--lead <uuid>] [--status pending|all|...]
  cancel  --tenant <slug> --lead <uuid>
  dry-run --tenant <slug> --treeflow <id> --lead <uuid>

All commands open their own async engine (same pattern as ai-sdr users and
ai-sdr simulate). The CLI hits the DB directly — not via REST — because
follow_up ops are admin/dev surface, not user-facing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.duration import parse_duration
from ai_sdr.follow_up.jinja import render_params
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.settings import get_settings

follow_ups_app = typer.Typer(help="Follow-up scheduler ops")
console = Console()


def _make_session() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _load_tenant(session: AsyncSession, slug: str) -> Tenant:
    t = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if t is None:
        console.print(f"[red]tenant not found: {slug}[/red]")
        raise typer.Exit(1)
    assert isinstance(t, Tenant)
    return t


@follow_ups_app.command("list")
def list_(
    tenant: Annotated[str, typer.Option("--tenant")],
    lead: Annotated[str | None, typer.Option("--lead", help="Filter to one lead UUID")] = None,
    status: Annotated[
        str,
        typer.Option(
            "--status",
            help="pending | completed | cancelled | error | all",
        ),
    ] = "pending",
) -> None:
    asyncio.run(_list_async(tenant, lead, status))


async def _list_async(tenant_slug: str, lead_filter: str | None, status_filter: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        tenant = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, tenant.id)
        stmt = select(FollowUpJob).order_by(FollowUpJob.scheduled_at.asc())
        if status_filter != "all":
            stmt = stmt.where(FollowUpJob.status == status_filter)
        if lead_filter:
            stmt = stmt.where(FollowUpJob.lead_id == uuid.UUID(lead_filter))
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            console.print(f"[yellow]no follow-ups (status={status_filter!r})[/yellow]")
            await engine.dispose()
            return

        table = Table(title=f"Follow-up jobs — {tenant_slug} (status={status_filter})")
        table.add_column("ID", no_wrap=True)
        table.add_column("Lead")
        table.add_column("Attempt", justify="right")
        table.add_column("Scheduled")
        table.add_column("Status")
        table.add_column("Sent ID")
        for r in rows:
            sid = r.sent_external_id or ""
            sid_display = sid[:14] + ("…" if len(sid) > 14 else "")
            table.add_row(
                str(r.id)[:8] + "…",
                str(r.lead_id)[:8] + "…",
                str(r.attempt_number),
                r.scheduled_at.strftime("%Y-%m-%d %H:%M"),
                r.status,
                sid_display,
            )
        console.print(table)
    await engine.dispose()


@follow_ups_app.command("cancel")
def cancel(
    tenant: Annotated[str, typer.Option("--tenant")],
    lead: Annotated[str, typer.Option("--lead")],
) -> None:
    asyncio.run(_cancel_async(tenant, lead))


async def _cancel_async(tenant_slug: str, lead_id_str: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        tenant = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, tenant.id)
        lead_id = uuid.UUID(lead_id_str)
        result = await session.execute(
            update(FollowUpJob)
            .where(FollowUpJob.lead_id == lead_id, FollowUpJob.status == "pending")
            .values(status="cancelled", error_detail="manual cancel via CLI")
        )
        n = result.rowcount or 0  # type: ignore[attr-defined]
        await session.commit()
        if n == 0:
            console.print(f"[yellow]no pending follow-ups for lead {lead_id_str}[/yellow]")
        else:
            console.print(
                f"[green]cancelled {n} pending follow-up(s) for lead {lead_id_str}[/green]"
            )
    await engine.dispose()


@follow_ups_app.command("dry-run")
def dry_run(
    tenant: Annotated[str, typer.Option("--tenant")],
    treeflow: Annotated[str, typer.Option("--treeflow")],
    lead: Annotated[str, typer.Option("--lead")],
) -> None:
    asyncio.run(_dry_run_async(tenant, treeflow, lead))


async def _dry_run_async(tenant_slug: str, treeflow_id: str, lead_id_str: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        tenant = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, tenant.id)
        lead = await session.get(Lead, uuid.UUID(lead_id_str))
        if lead is None:
            console.print(f"[red]lead not found: {lead_id_str}[/red]")
            raise typer.Exit(1)

        talkflow = (
            await session.execute(
                select(TalkFlow).where(
                    TalkFlow.tenant_id == tenant.id,
                    TalkFlow.lead_id == lead.id,
                )
            )
        ).scalar_one_or_none()
        if talkflow is None:
            console.print(f"[red]no TalkFlow for this lead in tenant {tenant_slug}[/red]")
            raise typer.Exit(1)

        tv = (
            await session.execute(
                select(TreeflowVersion)
                .where(
                    TreeflowVersion.tenant_id == tenant.id,
                    TreeflowVersion.treeflow_id == treeflow_id,
                )
                .order_by(TreeflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if tv is None:
            console.print(f"[red]treeflow not found: {treeflow_id}[/red]")
            raise typer.Exit(1)

        parsed = TreeFlow.model_validate(yaml.safe_load(tv.content_yaml))
        cfg = parsed.follow_up
        if cfg is None or not cfg.enabled:
            console.print(f"[yellow]TreeFlow {treeflow_id} has no follow_up enabled[/yellow]")
            await engine.dispose()
            return

        next_attempt = talkflow.follow_up_attempt_number + 1
        if next_attempt > cfg.max_attempts:
            console.print(
                f"[yellow]talkflow already at attempt {talkflow.follow_up_attempt_number} "
                f"(max={cfg.max_attempts}) — would mark cold, no send[/yellow]"
            )
            await engine.dispose()
            return

        step = cfg.sequence[next_attempt - 1]
        params = render_params(step.params, lead=lead, tenant=tenant, collected={})
        scheduled_at = datetime.now(UTC) + parse_duration(step.after)

        table = Table(title=f"Dry-run — next follow-up for lead {lead_id_str}")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("attempt_number", str(next_attempt))
        table.add_row("template_ref", step.template_ref)
        table.add_row("language", step.language)
        table.add_row("params (rendered)", str(params))
        table.add_row(
            "scheduled_at (if scheduled now)",
            scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
        )
        console.print(table)
        console.print("[dim](dry-run — nothing sent, nothing inserted)[/dim]")
    await engine.dispose()
