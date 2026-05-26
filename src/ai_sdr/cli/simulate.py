"""Interactive REPL for stepping a TalkFlow against a real LLM."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime


def simulate(
    tenant: Annotated[
        str,
        typer.Option("--tenant", help="Tenant slug (must exist in DB and tenants/<slug>/)"),
    ],
    treeflow: Annotated[
        str, typer.Option("--treeflow", help="TreeFlow id (yaml filename without .yaml)")
    ],
    lead: Annotated[
        str, typer.Option("--lead", help="Lead identifier (free-form; per-tenant unique)")
    ],
    show_extracted: Annotated[bool, typer.Option("--show-extracted/--no-show-extracted")] = False,
    no_classifier: Annotated[
        bool,
        typer.Option(
            "--no-classifier",
            help="Disable the objection classifier for this run (debug).",
        ),
    ] = False,
    tenants_dir: Annotated[Path, typer.Option("--tenants-dir")] = Path("tenants"),
) -> None:
    """Run a TalkFlow in the terminal — real Postgres, real LLM, no WhatsApp/CRM."""
    # `lead` is used as `external_label` — find-or-create happens inside _run
    asyncio.run(_run(tenant, treeflow, lead, show_extracted, no_classifier, tenants_dir))


async def _run(
    tenant_slug: str,
    treeflow_id: str,
    lead_label: str,
    show_extracted: bool,
    no_classifier: bool,
    tenants_dir: Path,
) -> None:
    await ensure_checkpointer_schema()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tenants_dir=tenants_dir),
        treeflow_loader=TreeFlowLoader(tenants_dir=tenants_dir),
        sops_loader=SopsLoader(tenants_dir=tenants_dir),
    )

    async with sm() as session:
        async with session.begin():
            t = (
                await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
            ).scalar_one_or_none()
            if t is None:
                typer.secho(
                    f"tenant {tenant_slug!r} not found in DB — "
                    "INSERT INTO tenants (slug, display_name) ...",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)

            await set_tenant_context(session, t.id)

            # Find-or-create a dev Lead by external_label so the foreign-key in
            # talkflows.lead_id can be satisfied. Simulate marks the lead 'active'
            # so the worker (if running) won't lock waiting on assignment.
            dev_lead = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == t.id,
                        Lead.external_label == lead_label,
                    )
                )
            ).scalar_one_or_none()
            if dev_lead is None:
                dev_lead = Lead(
                    tenant_id=t.id,
                    external_label=lead_label,
                    status="active",
                )
                session.add(dev_lead)

            await runtime.publish_version(session, t, treeflow_id)
            tf = await runtime.create(session, t, lead_id=dev_lead.id, treeflow_id=treeflow_id)
        tf_id = tf.id
        tenant_slug_final = t.slug

    typer.secho(f"[talkflow:{tf_id}] type a message, /quit to exit.\n", fg=typer.colors.GREEN)

    objections_override = ObjectionsConfig(enabled=False) if no_classifier else None

    user_msg = ""
    while True:
        async with sm() as session:
            t = (
                await session.execute(select(Tenant).where(Tenant.slug == tenant_slug_final))
            ).scalar_one()
            result = await runtime.step(
                session,
                t,
                tf_id,
                user_input=user_msg,
                objections_override=objections_override,
            )

        typer.secho(f"[node:{result.current_node}] > {result.response_text}", fg=typer.colors.CYAN)
        if show_extracted:
            if result.collected:
                typer.secho(f"  collected: {result.collected}", fg=typer.colors.BRIGHT_BLACK)
            if result.objections_handled:
                typer.secho("  objections_handled:", fg=typer.colors.BRIGHT_BLACK)
                for r in result.objections_handled:
                    typer.secho(
                        f"    - {r.get('objection_id')} @ {r.get('detected_at_node')} "
                        f"(t={r.get('turn_index')}): {(r.get('quote') or '')[:60]!r}",
                        fg=typer.colors.BRIGHT_BLACK,
                    )
        if result.completed:
            typer.secho("\n[talkflow completed]", fg=typer.colors.GREEN)
            break

        try:
            user_msg = typer.prompt("you", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            break
        if user_msg.strip() == "/quit":
            break
        if user_msg.strip() == "/restart":
            async with sm() as session:
                await session.execute(delete(TalkFlow).where(TalkFlow.id == tf_id))
                await session.commit()
            typer.secho("[restarted — exiting; re-run the command]", fg=typer.colors.YELLOW)
            break

    await engine.dispose()
