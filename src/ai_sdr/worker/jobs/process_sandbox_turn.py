"""process_sandbox_turn — worker job pra Talks sandbox (PR #24).

VERSÃO MVP (2026-06-23): roda LLM diretamente sobre histórico de mensagens,
SEM passar pelo pipeline completo do FlowEngine v2.

Trade-off explícito: NÃO testa guardrails, objection classifier, actions,
critic pass, off-topic detection. Testa SÓ "interface + LLM responde".

Por que essa simplificação: a assinatura completa do run_turn exige session
+ adapter + voice_stack + guardrail_cfg + ... — complexo de configurar
corretamente pra sandbox sem tocar produção. Pro MVP "mostrar pra Lana
hoje", chat com LLM já entrega valor enorme.

Próximas iterações (S2-S4 da spec): integrar run_turn completo quando o
contract de injeção de adapter sandbox-aware estiver maduro.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select, text

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.db.session import session_factory
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

log = structlog.get_logger()


_SANDBOX_FAKE_RESPONSES = [
    "Oi! Tudo bem? 👋 Aqui é a SDR sandbox. Pra começar, qual seu nome?",
    "Prazer em te conhecer! Pra te ajudar melhor, qual o faturamento mensal aproximado da sua operação?",
    "Show, com esse faturamento faz total sentido a Mentoria. Quer que eu te apresente?",
    "Perfeito! Vou te passar pra Manoela então.",
    "Ok, qualquer coisa estamos por aqui!",
]


def _build_llm(mode: str, tenant_cfg, secrets):
    """Resolve LLM conforme sandbox_llm_mode ('real'|'fake')."""
    if mode == "real":
        # Anthropic real via init_chat_model
        from langchain.chat_models import init_chat_model

        api_key = secrets[tenant_cfg.llm.default.api_key_ref.removeprefix("secrets/")]
        kwargs: dict[str, Any] = {"api_key": api_key}
        if tenant_cfg.llm.default.temperature is not None:
            kwargs["temperature"] = tenant_cfg.llm.default.temperature
        if tenant_cfg.llm.default.max_tokens is not None:
            kwargs["max_tokens"] = tenant_cfg.llm.default.max_tokens
        return init_chat_model(
            f"{tenant_cfg.llm.default.provider}:{tenant_cfg.llm.default.model}",
            **kwargs,
        )

    # Fake mode — scripted responses
    from langchain_core.language_models import FakeListChatModel

    return FakeListChatModel(responses=_SANDBOX_FAKE_RESPONSES)


def _build_persona_prompt(tenant_cfg, treeflow_version: TreeflowVersion) -> str:
    """Constrói system prompt mínimo baseado no tenant + treeflow."""
    persona_parts = [
        f"Você é uma SDR (Sales Development Representative) virtual da {tenant_cfg.display_name}.",
        "Sua função é qualificar leads via WhatsApp em tom amigável, brasileiro, sem formalismo.",
        "Mensagens curtas (1-2 frases). Sempre cumprimente pelo nome quando souber.",
    ]

    if tenant_cfg.guardrails:
        if tenant_cfg.guardrails.allowed_products:
            prods = ", ".join(tenant_cfg.guardrails.allowed_products)
            persona_parts.append(f"Produtos que você pode mencionar: {prods}.")
        if tenant_cfg.guardrails.allowed_prices:
            prices = ", ".join(f"R$ {p:,}".replace(",", ".") for p in tenant_cfg.guardrails.allowed_prices)
            persona_parts.append(f"Valores permitidos: {prices}.")

    persona_parts.append(
        "Você está em MODO SANDBOX (teste). Responda como se fosse uma conversa real "
        "mas saiba que isso é um teste interno."
    )

    return "\n".join(persona_parts)


async def process_sandbox_turn(ctx: dict[str, Any], tenant_id_str: str, talk_id_str: str) -> None:
    """Processa 1 turno de Talk sandbox — versão MVP simplificada."""
    tenant_id = uuid.UUID(tenant_id_str)
    talk_id = uuid.UUID(talk_id_str)

    async with session_factory() as db:
        await db.execute(text("SET LOCAL row_security = off"))
        await set_tenant_context(db, tenant_id)

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
            log.warning("sandbox.turn.talk_not_found", talk_id=talk_id_str)
            return

        if talk.status != "active":
            log.info("sandbox.turn.talk_inactive", talk_id=talk_id_str, status=talk.status)
            return

        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        lead = (await db.execute(select(Lead).where(Lead.id == talk.lead_id))).scalar_one()
        tfv = (
            await db.execute(
                select(TreeflowVersion).where(TreeflowVersion.id == talk.treeflow_version_id)
            )
        ).scalar_one()

        # Drena 1 inbound queued
        inbound_row = (
            await db.execute(
                select(InboundMessageRow)
                .where(
                    InboundMessageRow.lead_id == lead.id,
                    InboundMessageRow.status == "queued",
                )
                .order_by(InboundMessageRow.received_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if inbound_row is None:
            log.info("sandbox.turn.no_inbound", talk_id=talk_id_str)
            return

        # Carrega tenant config + secrets
        from ai_sdr.secrets.sops_loader import SopsLoader
        from ai_sdr.settings import get_settings
        from ai_sdr.tenant_loader.loader import TenantLoader

        tdir = Path(get_settings().tenants_dir)
        tenant_cfg = TenantLoader(tdir).load(tenant.slug)
        secrets = SopsLoader(tdir).load(tenant.slug)

        # Constrói history das mensagens anteriores (inbound + outbound interleaved)
        inbound_history = (
            (
                await db.execute(
                    select(InboundMessageRow)
                    .where(
                        InboundMessageRow.lead_id == lead.id,
                        InboundMessageRow.id != inbound_row.id,
                        InboundMessageRow.status == "processed",
                    )
                    .order_by(InboundMessageRow.received_at.asc())
                    .limit(20)
                )
            ).scalars().all()
        )
        outbound_history = (
            (
                await db.execute(
                    select(OutboundMessage)
                    .where(OutboundMessage.talk_id == talk.id)
                    .order_by(OutboundMessage.sent_at.asc())
                    .limit(20)
                )
            ).scalars().all()
        )

        messages = [SystemMessage(content=_build_persona_prompt(tenant_cfg, tfv))]

        # Interleave por timestamp (simplificado)
        timeline = []
        for m in inbound_history:
            timeline.append((m.received_at, "user", m.text))
        for m in outbound_history:
            timeline.append((m.sent_at, "assistant", m.body_text or ""))
        timeline.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=UTC))

        for _, role, txt in timeline:
            if role == "user":
                messages.append(HumanMessage(content=txt))
            else:
                messages.append(AIMessage(content=txt))

        # Adiciona inbound atual
        messages.append(HumanMessage(content=inbound_row.text))

        # Chama LLM
        mode = talk.sandbox_llm_mode or "fake"
        try:
            llm = _build_llm(mode, tenant_cfg, secrets)
            response = await llm.ainvoke(messages)
            outbound_text = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            log.error(
                "sandbox.turn.llm_failed",
                talk_id=talk_id_str,
                mode=mode,
                err=str(exc),
                err_type=type(exc).__name__,
            )
            inbound_row.status = "error"
            inbound_row.error_detail = f"LLM error: {type(exc).__name__}: {exc}"
            await db.commit()
            return

        # Persiste outbound + marca inbound processed + bump turn
        now = datetime.now(UTC)
        outbound = OutboundMessage(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            lead_id=lead.id,
            talk_id=talk.id,
            provider="sandbox",
            message_type="text",
            body_text=str(outbound_text),
            external_id=str(uuid.uuid4()),
            sent_at=now,
            triggered_by="sandbox",
            status="sent",
        )
        db.add(outbound)

        inbound_row.status = "processed"
        inbound_row.processed_at = now

        talk.turn_count = (talk.turn_count or 0) + 1
        talk.last_message_at = now

        await db.commit()

        log.info(
            "sandbox.turn.completed",
            talk_id=talk_id_str,
            turn=talk.turn_count,
            mode=mode,
            response_chars=len(str(outbound_text)),
        )
