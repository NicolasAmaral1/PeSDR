"""process_sandbox_turn — arq worker job for sandbox Talks.

V2 (post-#26 review): runs the production `run_turn` pipeline. The ONLY
differences vs `_run_v2_inbox` are the edges:

  - `adapter` = SandboxMessagingAdapter (returns fake SendResult, no Meta).
  - `llm` is resolved from `talk.sandbox_llm_mode`:
      * 'real' → `main_llm_for_tenant` (same as production).
      * 'fake' → constant-response Runnable returning a valid TurnDecision.
        Useful for CI / dev work that must not spend tokens.

Everything else — guardrails, objection classifier, actions, critic, KB,
voice stack, audit, advisory locks, transaction shape — runs exactly as
production. This is the "same motor, different edges" contract Nicolas
called out in the PR #26 review.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.runnables import Runnable, RunnableLambda
from sqlalchemy import select, text

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.llm_client import main_llm_for_tenant
from ai_sdr.flowengine.pipeline import run_turn
from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
from ai_sdr.guardrails.validator import GuardrailConfig
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
    WindowExpiredError,
)
from ai_sdr.messaging.sandbox import SandboxMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader

log = logging.getLogger(__name__)


def _build_fake_llm() -> Runnable:
    """Constant-response Runnable that satisfies the run_turn contract.

    Returns a TurnDecision with a generic prompt. Use 'real' mode for any
    serious validation; this exists so CI and dev work don't spend tokens.
    """

    async def _decide(_messages: object, _config: object = None) -> TurnDecision:
        return TurnDecision(
            response_text=(
                "[sandbox fake mode] Selecione 'real' no Talk pra rodar com "
                "Anthropic e validar o fluxo completo."
            ),
            collected_fields={},
            reasoning="sandbox fake LLM — constant response, no provider call",
        )

    return RunnableLambda(_decide)


def _build_llm_for_sandbox(
    mode: str, tenant_cfg: Any, secrets: dict[str, str]
) -> Runnable:
    if mode == "real":
        if tenant_cfg.llm is None or tenant_cfg.llm.default is None:
            raise ValueError(
                "sandbox 'real' mode requires tenant.llm.default block"
            )
        return main_llm_for_tenant(tenant_cfg.llm.default, secrets=secrets)
    return _build_fake_llm()


async def _mark_inbound_error(
    db: Any, inbound: InboundMessageRow, detail: str
) -> None:
    inbound.status = "error"
    inbound.error_detail = detail
    inbound.processed_at = datetime.now(UTC)
    await db.commit()


async def process_sandbox_turn(
    ctx: dict[str, Any], tenant_id_str: str, talk_id_str: str
) -> None:
    """Drain ONE queued inbound for a sandbox Talk through the production pipeline."""
    tenant_id = uuid.UUID(tenant_id_str)
    talk_id = uuid.UUID(talk_id_str)
    session_factory = ctx["session_factory"]

    async with session_factory() as db:
        # Cross-tenant lookup of the Tenant + Talk needs RLS off, same as
        # other worker jobs. Tenant context is then re-set for all subsequent
        # reads/writes inside the run_turn pipeline.
        await db.execute(text("SET LOCAL row_security = off"))

        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            log.warning("sandbox.turn.tenant_not_found tenant_id=%s", tenant_id_str)
            return

        await set_tenant_context(db, tenant.id)

        talk = (
            await db.execute(
                select(Talk).where(
                    Talk.id == talk_id,
                    Talk.tenant_id == tenant_id,
                    Talk.is_sandbox.is_(True),
                )
            )
        ).scalar_one_or_none()
        if talk is None:
            log.warning(
                "sandbox.turn.talk_not_found talk_id=%s", talk_id_str
            )
            return

        if talk.status != "active":
            log.info(
                "sandbox.turn.talk_inactive talk=%s status=%s",
                talk_id_str,
                talk.status,
            )
            return

        if not talk.sandbox_llm_mode:
            log.warning(
                "sandbox.turn.missing_llm_mode talk=%s", talk_id_str
            )
            return

        lead = (
            await db.execute(
                select(Lead).where(
                    Lead.id == talk.lead_id,
                    Lead.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if lead is None:
            log.warning(
                "sandbox.turn.lead_not_found talk=%s lead=%s",
                talk_id_str,
                talk.lead_id,
            )
            return

        tfv = (
            await db.execute(
                select(TreeflowVersion).where(
                    TreeflowVersion.id == talk.treeflow_version_id,
                    TreeflowVersion.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if tfv is None:
            log.error(
                "sandbox.turn.treeflow_version_missing talk=%s tfv=%s",
                talk_id_str,
                talk.treeflow_version_id,
            )
            return

        # Fetch the head queued inbound for THIS Talk's lead.
        inbound = (
            await db.execute(
                select(InboundMessageRow)
                .where(
                    InboundMessageRow.lead_id == lead.id,
                    InboundMessageRow.tenant_id == tenant_id,
                    InboundMessageRow.status == "queued",
                )
                .order_by(InboundMessageRow.received_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if inbound is None:
            log.info("sandbox.turn.no_inbound talk=%s", talk_id_str)
            return

        # Load tenant config + secrets + parsed TreeFlow.
        tdir = Path(get_settings().tenants_dir)
        tenant_cfg = TenantLoader(tdir).load(tenant.slug)
        secrets = SopsLoader(tdir).load(tenant.slug)

        try:
            treeflow = load_treeflow_v2(tfv.content_yaml)
        except Exception as exc:
            log.error(
                "sandbox.turn.treeflow_load_failed talk=%s err=%s",
                talk_id_str,
                exc,
            )
            await _mark_inbound_error(
                db, inbound, f"treeflow_load_failed: {exc}"
            )
            return

        # Build LLM per sandbox mode.
        try:
            llm = _build_llm_for_sandbox(talk.sandbox_llm_mode, tenant_cfg, secrets)
        except (KeyError, ValueError) as exc:
            log.error(
                "sandbox.turn.llm_build_failed talk=%s mode=%s err=%s",
                talk_id_str,
                talk.sandbox_llm_mode,
                exc,
            )
            await _mark_inbound_error(db, inbound, f"llm_build_failed: {exc}")
            return

        # Production guardrail config, verbatim.
        gcfg = tenant_cfg.guardrails
        guardrail_cfg = GuardrailConfig(
            disallowed_price_pattern=(gcfg.disallowed_price_pattern if gcfg else ""),
            allowed_prices=[str(p) for p in (gcfg.allowed_prices if gcfg else [])],
            allowed_products=list(gcfg.allowed_products) if gcfg else [],
            fallback_text=(gcfg.fallback_text if gcfg else "Vou validar com a equipe."),
        )

        adapter = SandboxMessagingAdapter()
        opt_out_keywords = (
            list(tenant_cfg.conversation.optout_stop_words)
            if tenant_cfg.conversation
            else []
        )

        # Voice stack stays off for sandbox MVP — FE-05 sandbox wiring later.
        synth, _trans, storage = None, None, None

        try:
            result = await run_turn(
                db,
                tenant=tenant,
                tenant_cfg=tenant_cfg,
                treeflow=treeflow,
                treeflow_version=tfv,
                inbound=inbound,
                llm=llm,
                adapter=adapter,
                opt_out_keywords=opt_out_keywords,
                guardrail_cfg=guardrail_cfg,
                voice_cfg=None,
                synthesizer=synth,
                storage=storage,
            )
        except (
            RecipientUnreachable,
            AuthError,
            PolicyError,
            WindowExpiredError,
            MessagingError,
        ) as exc:
            # Sandbox adapter never raises these — but defensive logging in
            # case someone wires it to send_text against a real provider.
            log.error(
                "sandbox.turn.messaging_error talk=%s err=%s",
                talk_id_str,
                exc,
            )
            await _mark_inbound_error(
                db, inbound, f"{type(exc).__name__}: {exc}"
            )
            return
        except Exception as exc:
            log.exception(
                "sandbox.turn.run_turn_unhandled talk=%s err=%s",
                talk_id_str,
                exc,
            )
            await _mark_inbound_error(
                db, inbound, f"{type(exc).__name__}: {exc}"
            )
            return

        now_ts = datetime.now(UTC)
        if result.outcome == "sent":
            inbound.status = "processed"
            inbound.processed_at = now_ts
        elif result.outcome in ("opt_out", "lead_banned", "escalated"):
            inbound.status = "processed"
            inbound.processed_at = now_ts
            inbound.error_detail = f"v2_outcome: {result.outcome}"
        else:
            inbound.status = "error"
            inbound.error_detail = f"v2_outcome: {result.outcome}"

        await db.commit()

        log.info(
            "sandbox.turn.completed talk=%s outcome=%s mode=%s node=%s",
            talk_id_str,
            result.outcome,
            talk.sandbox_llm_mode,
            result.current_node_after,
        )
