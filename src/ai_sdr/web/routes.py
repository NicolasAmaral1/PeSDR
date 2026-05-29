"""Console HTML routes — /console/{slug}/leads + HTMX partial endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime
from ai_sdr.web.auth import require_tenant_access
from ai_sdr.web.deps import templates

router = APIRouter()


async def _tenants_visible_to(user: User, db: AsyncSession) -> list[Tenant]:
    if user.is_platform_admin:
        rows = (await db.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
        return list(rows)
    rows = (
        (
            await db.execute(
                select(Tenant)
                .join(UserTenantAccess, UserTenantAccess.tenant_id == Tenant.id)
                .where(UserTenantAccess.user_id == user.id)
                .order_by(Tenant.slug)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/console/{tenant_slug}/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    tenant, user = access
    tenants_available = await _tenants_visible_to(user, db)
    return templates.TemplateResponse(
        request,
        "leads_list.html",
        {
            "current_tenant": tenant,
            "current_user": user,
            "tenants_available": tenants_available,
        },
    )


def _format_lead_display(lead: Lead) -> str:
    if lead.whatsapp_e164:
        # +5511988887777 → +55 11 98888-7777
        digits = lead.whatsapp_e164.lstrip("+")
        if len(digits) >= 12 and digits.startswith("55"):
            return f"+{digits[:2]} {digits[2:4]} {digits[4:9]}-{digits[9:13]}"
        return lead.whatsapp_e164
    if lead.external_label:
        return lead.external_label
    return f"#{str(lead.id)[:8]}"


def _format_time_short(dt: datetime) -> str:
    """HH:MM if today, else DD/MM HH:MM."""
    now = datetime.now(UTC)
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%d/%m %H:%M")


async def _list_pending_lead_rows(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:  # type: ignore[type-arg]
    """Returns ALL pending leads for tenant_id, enriched with queued_count + preview."""
    leads = (
        (
            await db.execute(
                select(Lead)
                .where(Lead.tenant_id == tenant_id, Lead.status == "pending_assignment")
                .order_by(Lead.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not leads:
        return []

    # Counts of queued messages per lead.
    count_rows = (
        await db.execute(
            select(InboundMessageRow.lead_id, func.count().label("n"))
            .where(
                InboundMessageRow.lead_id.in_([le.id for le in leads]),
                InboundMessageRow.status == "queued",
            )
            .group_by(InboundMessageRow.lead_id)
        )
    ).all()
    counts = {row.lead_id: row.n for row in count_rows}

    # First message text per lead (used as preview).
    first_msg_rows = (
        await db.execute(
            select(InboundMessageRow.lead_id, InboundMessageRow.text)
            .where(
                InboundMessageRow.lead_id.in_([le.id for le in leads]),
                InboundMessageRow.status == "queued",
            )
            .order_by(InboundMessageRow.lead_id, InboundMessageRow.received_at.asc())
        )
    ).all()
    previews: dict[uuid.UUID, str] = {}
    for row in first_msg_rows:
        if row.lead_id not in previews:
            text = (row.text or "").strip()
            if len(text) > 80:
                text = text[:77] + "…"
            previews[row.lead_id] = text

    out = []
    for lead in leads:
        out.append(
            {
                "id": lead.id,
                "display_label": _format_lead_display(lead),
                "created_at_short": _format_time_short(lead.created_at),
                "queued_count": int(counts.get(lead.id, 0)),
                "preview": previews.get(lead.id),
            }
        )
    return out


@router.get("/console/{tenant_slug}/leads/list", response_class=HTMLResponse)
async def leads_list_partial(
    request: Request,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
    selected_lead_id: uuid.UUID | None = None,
) -> HTMLResponse:
    tenant, _user = access
    leads = await _list_pending_lead_rows(db, tenant.id)
    return templates.TemplateResponse(
        request,
        "_lead_card.html",
        {
            "leads": leads,
            "current_tenant": tenant,
            "selected_lead_id": selected_lead_id,
        },
    )


@router.get("/console/{tenant_slug}/leads/{lead_id}/detail", response_class=HTMLResponse)
async def lead_detail_partial(
    request: Request,
    lead_id: uuid.UUID,
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> HTMLResponse:
    tenant, _user = access
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant.id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found in this tenant")
    if lead.status != "pending_assignment":
        # Lead might have been just assigned by another operator — render
        # an empty-state hint instead of a stale detail panel.
        subtitle = "Outro operador pode ter atribuído enquanto você olhava. Selecione outro lead."
        return templates.TemplateResponse(
            request,
            "_empty_state.html",
            {
                "title": "Lead já foi atribuído",
                "subtitle": subtitle,
            },
        )

    messages = (
        (
            await db.execute(
                select(InboundMessageRow)
                .where(
                    InboundMessageRow.lead_id == lead.id,
                    InboundMessageRow.status == "queued",
                )
                .order_by(InboundMessageRow.received_at.asc())
            )
        )
        .scalars()
        .all()
    )

    # Enumerate available treeflows by scanning tenants/<slug>/treeflows/*.yaml
    tenants_dir = Path(get_settings().tenants_dir)
    treeflow_dir = tenants_dir / tenant.slug / "treeflows"
    treeflows = sorted(p.stem for p in treeflow_dir.glob("*.yaml")) if treeflow_dir.is_dir() else []

    lead_ctx = {
        "id": lead.id,
        "id_short": str(lead.id)[:8] + "…",
        "display_label": _format_lead_display(lead),
        "created_at_short": _format_time_short(lead.created_at),
        "provider": "whatsapp_cloud" if lead.whatsapp_e164 else None,
        "status": lead.status,
    }
    message_ctx = [
        {
            "received_at_short": _format_time_short(m.received_at),
            "text": m.text,
        }
        for m in messages
    ]

    return templates.TemplateResponse(
        request,
        "_lead_detail.html",
        {
            "lead": lead_ctx,
            "messages": message_ctx,
            "current_tenant": tenant,
            "treeflows": treeflows,
        },
    )


@router.post("/console/{tenant_slug}/leads/{lead_id}/assign", response_class=HTMLResponse)
async def lead_assign(
    request: Request,
    lead_id: uuid.UUID,
    treeflow_id: Annotated[str, Form()],
    access: Annotated[tuple[Tenant, User], Depends(require_tenant_access)],
    db: Annotated[AsyncSession, Depends(db_session)],
    pool: Annotated[ArqRedis, Depends(arq_pool)],
) -> HTMLResponse:
    tenant, _user = access
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant.id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if lead.status != "pending_assignment":
        raise HTTPException(
            status_code=409,
            detail=f"lead is {lead.status}, not pending_assignment",
        )

    tdir = Path(get_settings().tenants_dir)
    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tdir),
        treeflow_loader=TreeFlowLoader(tdir),
        sops_loader=SopsLoader(tdir),
    )
    _talkflow = await runtime.create(db, tenant, lead_id=lead.id, treeflow_id=treeflow_id)
    lead.status = "active"
    await db.commit()

    await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead.id))

    # commit() ended the transaction → set_local tenant context was lost.
    # Re-set so the master-list query passes the RLS policy on `leads`.
    await set_tenant_context(db, tenant.id)
    # Render updated master list + OOB swap for detail panel as one response.
    leads = await _list_pending_lead_rows(db, tenant.id)
    leads_html = templates.get_template("_lead_card.html").render(
        leads=leads,
        current_tenant=tenant,
        selected_lead_id=None,
    )
    empty_state_html = templates.get_template("_empty_state.html").render(
        title="Nenhum lead selecionado",
        subtitle="Clique em um lead à esquerda para ver detalhes.",
    )
    # HTMX OOB swap: the second fragment with hx-swap-oob replaces #lead-detail.
    body = leads_html + f'\n<div id="lead-detail" hx-swap-oob="innerHTML">{empty_state_html}</div>'
    return HTMLResponse(content=body)
