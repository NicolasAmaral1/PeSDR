"""Sandbox console routes — /console/{slug}/sandbox/*.

Reusa auth + RBAC do console existente (require_tenant_access). Gated por
tenant.console.sandbox.enabled — retorna 404 se desligado.

Q1 do Nicolas: rotas NÃO chamam run_turn inline. Persistem em DB +
enfileiram process_sandbox_turn pro worker.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.web.auth import require_tenant_access
from ai_sdr.web.deps import templates
from ai_sdr.web.sandbox.service import SandboxService

router = APIRouter()


def _require_sandbox_enabled(tenant: Tenant) -> None:
    """Gate: tenant.console.sandbox.enabled deve ser true. 404 se não."""
    tenants_dir = Path(get_settings().tenants_dir)
    cfg = TenantLoader(tenants_dir).load(tenant.slug)
    sandbox_cfg = (cfg.console.sandbox if cfg.console else None) if cfg.console else None
    if sandbox_cfg is None or not sandbox_cfg.enabled:
        raise HTTPException(404, "sandbox not enabled for this tenant")


def _list_treeflows(tenant_slug: str) -> list[str]:
    """Enumera TreeFlows disponíveis (filesystem-based, igual o console)."""
    tenants_dir = Path(get_settings().tenants_dir)
    treeflow_dir = tenants_dir / tenant_slug / "treeflows"
    if not treeflow_dir.is_dir():
        return []
    return sorted(p.stem for p in treeflow_dir.glob("*.yaml"))


def _format_time(dt: datetime) -> str:
    now = datetime.now(UTC)
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%d/%m %H:%M")


@router.get("/console/{tenant_slug}/sandbox", response_class=HTMLResponse)
async def sandbox_dashboard(
    request: Request,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    """Dashboard inicial: lista talks sandbox ativos + botão "+ Nova Talk"."""
    tenant, user = access
    _require_sandbox_enabled(tenant)
    await set_tenant_context(db, tenant.id)

    svc = SandboxService(db)
    talks = await svc.list_sandbox_talks(tenant.id)
    treeflows = _list_treeflows(tenant.slug)

    talk_ctx = []
    for talk in talks:
        lead = (
            await db.execute(
                select(Lead).where(
                    Lead.id == talk.lead_id, Lead.tenant_id == tenant.id
                )
            )
        ).scalar_one_or_none()
        if lead is None:
            continue
        talk_ctx.append(
            {
                "id": str(talk.id),
                "id_short": str(talk.id)[:8] + "…",
                "lead_label": lead.display_name or lead.external_label or "Sandbox Lead",
                "treeflow_id": talk.treeflow_id,
                "llm_mode": talk.sandbox_llm_mode or "?",
                "turn_count": talk.turn_count,
                "last_message_at": _format_time(talk.last_message_at),
                "status": talk.status,
            }
        )

    return templates.TemplateResponse(
        request,
        "sandbox_dashboard.html",
        {
            "current_tenant": tenant,
            "current_user": user,
            "talks": talk_ctx,
            "treeflows": treeflows,
        },
    )


@router.post("/console/{tenant_slug}/sandbox/talks/new")
async def sandbox_create_talk(
    request: Request,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
    treeflow_id: Annotated[str, Form()],
    sandbox_llm_mode: Annotated[Literal["real", "fake"], Form()],
    display_name: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """Cria novo Talk sandbox. Retorna talk_id pra redirecionamento."""
    tenant, _user = access
    _require_sandbox_enabled(tenant)
    await set_tenant_context(db, tenant.id)

    svc = SandboxService(db)
    try:
        talk = await svc.create_talk(
            tenant=tenant,
            treeflow_id=treeflow_id,
            sandbox_llm_mode=sandbox_llm_mode,
            display_name=display_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return JSONResponse(
        {"talk_id": str(talk.id)},
        headers={"HX-Redirect": f"/console/{tenant.slug}/sandbox/talks/{talk.id}"},
    )


@router.get("/console/{tenant_slug}/sandbox/talks/{talk_id}", response_class=HTMLResponse)
async def sandbox_chat_view(
    request: Request,
    talk_id: uuid.UUID,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    """Renderiza chat UI (HTML completo). Histórico via HTMX partial."""
    tenant, user = access
    _require_sandbox_enabled(tenant)
    await set_tenant_context(db, tenant.id)

    talk = (
        await db.execute(
            select(Talk).where(
                Talk.id == talk_id, Talk.tenant_id == tenant.id, Talk.is_sandbox.is_(True)
            )
        )
    ).scalar_one_or_none()
    if talk is None:
        raise HTTPException(404, "sandbox talk not found")

    lead = (
        await db.execute(select(Lead).where(Lead.id == talk.lead_id))
    ).scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "sandbox_chat.html",
        {
            "current_tenant": tenant,
            "current_user": user,
            "talk": {
                "id": str(talk.id),
                "treeflow_id": talk.treeflow_id,
                "llm_mode": talk.sandbox_llm_mode,
                "turn_count": talk.turn_count,
                "status": talk.status,
            },
            "lead": {
                "display_name": lead.display_name if lead else "Sandbox Lead",
                "whatsapp": lead.whatsapp_e164 if lead else "",
            },
        },
    )


@router.post("/console/{tenant_slug}/sandbox/talks/{talk_id}/send")
async def sandbox_send_inbound(
    request: Request,
    talk_id: uuid.UUID,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
    pool: Annotated[ArqRedis, Depends(arq_pool)],
    text: Annotated[str, Form()],
) -> JSONResponse:
    """Q1 Nicolas: grava inbound em DB + enqueue worker. Retorna 202.

    NÃO chama run_turn inline. Worker process_sandbox_turn vai drenar.
    """
    tenant, _user = access
    _require_sandbox_enabled(tenant)
    await set_tenant_context(db, tenant.id)

    svc = SandboxService(db)
    try:
        await svc.record_operator_inbound(
            tenant_id=tenant.id, talk_id=talk_id, text=text
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    # Enqueue worker job
    await pool.enqueue_job(
        "process_sandbox_turn", str(tenant.id), str(talk_id)
    )

    return JSONResponse(
        {"status": "queued", "message": "digitando…"}, status_code=202
    )


@router.get(
    "/console/{tenant_slug}/sandbox/talks/{talk_id}/messages", response_class=HTMLResponse
)
async def sandbox_messages_partial(
    request: Request,
    talk_id: uuid.UUID,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    """HTMX partial: lista histórico de mensagens (inbound + outbound interleaved)."""
    tenant, _user = access
    _require_sandbox_enabled(tenant)
    await set_tenant_context(db, tenant.id)

    talk = (
        await db.execute(
            select(Talk).where(
                Talk.id == talk_id, Talk.tenant_id == tenant.id, Talk.is_sandbox.is_(True)
            )
        )
    ).scalar_one_or_none()
    if talk is None:
        raise HTTPException(404, "sandbox talk not found")

    # Inbound (operador → "lead simulado") + Outbound (agente → fake adapter)
    inbounds = (
        await db.execute(
            select(InboundMessageRow)
            .where(InboundMessageRow.lead_id == talk.lead_id)
            .order_by(InboundMessageRow.received_at.asc())
        )
    ).scalars().all()
    outbounds = (
        await db.execute(
            select(OutboundMessage)
            .where(OutboundMessage.talk_id == talk.id)
            .order_by(OutboundMessage.sent_at.asc())
        )
    ).scalars().all()

    # Interleave by raw datetime — sorting by the formatted "%H:%M" string
    # would break across midnight (00:10 < 23:50 lexicographically) or when
    # one day rolls into the next-day "%d/%m %H:%M" format.
    _EPOCH = datetime.min.replace(tzinfo=UTC)
    items: list[dict[str, Any]] = []
    for m in inbounds:
        items.append(
            {
                "direction": "in",  # operador "simula" lead → bubble esquerda
                "text": m.text,
                "time": _format_time(m.received_at),
                "_sort": m.received_at or _EPOCH,
                "status": m.status,
            }
        )
    for m in outbounds:
        items.append(
            {
                "direction": "out",  # agente → bubble direita
                "text": m.body_text or m.template_ref or "",
                "time": _format_time(m.sent_at) if m.sent_at else "",
                "_sort": m.sent_at or _EPOCH,
                "status": m.status,
            }
        )
    items.sort(key=lambda x: x["_sort"])

    return templates.TemplateResponse(
        request, "_sandbox_messages.html", {"messages": items, "talk_id": str(talk_id)}
    )


@router.get(
    "/console/{tenant_slug}/sandbox/talks/{talk_id}/state", response_class=HTMLResponse
)
async def sandbox_state_partial(
    request: Request,
    talk_id: uuid.UUID,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    """HTMX partial: state debugger painel."""
    tenant, _user = access
    _require_sandbox_enabled(tenant)
    await set_tenant_context(db, tenant.id)

    talk = (
        await db.execute(
            select(Talk).where(
                Talk.id == talk_id, Talk.tenant_id == tenant.id, Talk.is_sandbox.is_(True)
            )
        )
    ).scalar_one_or_none()
    if talk is None:
        raise HTTPException(404, "sandbox talk not found")

    state = (
        await db.execute(
            select(TalkFlowState).where(TalkFlowState.talk_id == talk.id)
        )
    ).scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "_sandbox_state.html",
        {
            "talk": {
                "id": str(talk.id),
                "treeflow_id": talk.treeflow_id,
                "llm_mode": talk.sandbox_llm_mode,
                "turn_count": talk.turn_count,
                "status": talk.status,
            },
            "state": {
                "current_node": state.current_node if state else "?",
                "collected": state.collected if state else {},
                "extracted_facts": state.extracted_facts if state else {},
                "objections_handled": state.objections_handled if state else [],
            }
            if state
            else {},
        },
    )
