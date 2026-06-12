"""Interactive REPL for stepping a TalkFlow against a real LLM."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.pipeline import run_turn
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime


def _llm_for_simulate(tenant_cfg, tenants_dir: Path = Path("tenants")):
    """Factory hook — tests patch this to inject a fake LLM."""
    from ai_sdr.flowengine.llm_client import main_llm_for_tenant

    secrets = SopsLoader(tenants_dir).load(tenant_cfg.id)
    return main_llm_for_tenant(tenant_cfg.llm.default, secrets=secrets)


def _adapter_for_simulate(tenant: Tenant) -> FakeMessagingAdapter:
    """Factory hook — tests patch this. Returns a FakeMessagingAdapter."""
    return FakeMessagingAdapter()


async def simulate_v2_turn(
    *,
    session: AsyncSession,
    tenant: Tenant,
    treeflow_version: TreeflowVersion,
    lead_phone: str,
    inbound_text: str,
    tenant_cfg=None,
    stdout=sys.stdout,
) -> None:
    """Drive one v2 turn for the simulate REPL.

    If tenant_cfg is None, loads it via TenantLoader('tenants') by tenant.slug.
    Tests inject a fake TenantConfig directly to avoid disk + provider auth.
    """
    if tenant_cfg is None:
        tenant_cfg = TenantLoader(Path("tenants")).load(tenant.slug)

    treeflow = load_treeflow_v2(treeflow_version.content_yaml)
    inbound = InboundMessageRow(
        tenant_id=tenant.id,
        provider="fake",
        external_id=f"sim-{uuid.uuid4().hex[:6]}",
        from_address=lead_phone,
        text=inbound_text,
        raw={"body": inbound_text},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    session.add(inbound)
    await session.flush()

    llm = _llm_for_simulate(tenant_cfg)
    adapter = _adapter_for_simulate(tenant)
    opt_out_keywords = (
        list(tenant_cfg.conversation.optout_stop_words)
        if tenant_cfg.conversation
        else ["sair", "parar"]
    )
    gcfg = tenant_cfg.guardrails
    guardrail_cfg = GuardrailConfig(
        disallowed_price_pattern=(gcfg.disallowed_price_pattern if gcfg else ""),
        allowed_prices=[str(p) for p in (gcfg.allowed_prices if gcfg else [])],
        allowed_products=list(gcfg.allowed_products) if gcfg else [],
        fallback_text=(gcfg.fallback_text if gcfg else "Vou validar com a equipe."),
    )
    result = await run_turn(
        session,
        tenant=tenant,
        tenant_cfg=tenant_cfg,
        treeflow=treeflow,
        treeflow_version=treeflow_version,
        inbound=inbound,
        llm=llm,
        adapter=adapter,
        opt_out_keywords=opt_out_keywords,
        guardrail_cfg=guardrail_cfg,
    )
    if result.response_text:
        print(result.response_text, file=stdout)


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
    arch_v2: Annotated[
        bool,
        typer.Option(
            "--arch-v2",
            help="Drive the FlowEngine v2 pipeline (run_turn) instead of legacy TalkFlowRuntime.",
        ),
    ] = False,
    tenants_dir: Annotated[Path, typer.Option("--tenants-dir")] = Path("tenants"),
) -> None:
    """Run a TalkFlow in the terminal — real Postgres, real LLM, no WhatsApp/CRM."""
    if arch_v2:
        asyncio.run(_run_v2(tenant, treeflow, lead, tenants_dir))
    else:
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


async def _run_v2(
    tenant_slug: str,
    treeflow_id: str,
    lead_label: str,
    tenants_dir: Path,
) -> None:
    """FlowEngine v2 REPL — drives run_turn per inbound, one message at a time."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        async with session.begin():
            t = (
                await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
            ).scalar_one_or_none()
            if t is None:
                typer.secho(
                    f"tenant {tenant_slug!r} not found in DB — "
                    "seed via scripts/seed_avelum_v2.py first",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)
            if t.architecture_version != 2:
                typer.secho(
                    f"tenant {tenant_slug!r} has architecture_version={t.architecture_version} "
                    "(expected 2 for --arch-v2)",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)

            tfv = (
                await session.execute(
                    select(TreeflowVersion)
                    .where(
                        TreeflowVersion.tenant_id == t.id,
                        TreeflowVersion.treeflow_id == treeflow_id,
                    )
                    .order_by(TreeflowVersion.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if tfv is None:
                typer.secho(
                    f"no TreeflowVersion found for tenant={tenant_slug!r} treeflow={treeflow_id!r}",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)

            await set_tenant_context(session, t.id)

            dev_lead = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == t.id,
                        Lead.external_label == lead_label,
                    )
                )
            ).scalar_one_or_none()
            if dev_lead is None:
                phone = f"+5511{uuid.uuid4().int % 10**9:09d}"
                dev_lead = Lead(
                    tenant_id=t.id,
                    external_label=lead_label,
                    status="active",
                    whatsapp_e164=phone,
                    # v2 preprocessing resolves leads by channel_identifiers
                    # (find_by_channel_identifier) — must match or run_turn
                    # will try to INSERT a duplicate and hit uq_leads_tenant_wa.
                    channel_identifiers={"whatsapp": phone},
                )
                session.add(dev_lead)
            elif not dev_lead.channel_identifiers and dev_lead.whatsapp_e164:
                # Backfill dev leads created before this fix.
                dev_lead.channel_identifiers = {"whatsapp": dev_lead.whatsapp_e164}

        tenant_id = t.id
        tfv_id = tfv.id
        lead_phone = dev_lead.whatsapp_e164 or f"+5511{uuid.uuid4().int % 10**9:09d}"

    typer.secho(
        f"[arch_v2 tenant={tenant_slug} treeflow={treeflow_id} lead={lead_label}] "
        "type a message, /quit to exit.\n",
        fg=typer.colors.GREEN,
    )

    while True:
        try:
            user_msg = typer.prompt("you", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            break
        if not user_msg.strip() or user_msg.strip() == "/quit":
            break

        async with sm() as session:
            # NO outer session.begin() here: run_turn manages its own
            # transaction (FE-03a T29 §9.1) — it commits any in-flight tx
            # and opens a fresh one, which would invalidate an enclosing
            # context manager. Autobegin covers the statements below;
            # run_turn commits them as the preprocessing boundary.
            await set_tenant_context(session, tenant_id)
            t = (
                await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            ).scalar_one()
            tfv = (
                await session.execute(
                    select(TreeflowVersion).where(TreeflowVersion.id == tfv_id)
                )
            ).scalar_one()
            await simulate_v2_turn(
                session=session,
                tenant=t,
                treeflow_version=tfv,
                lead_phone=lead_phone,
                inbound_text=user_msg,
            )

    await engine.dispose()
