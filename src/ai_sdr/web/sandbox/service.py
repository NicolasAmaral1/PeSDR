"""SandboxService — orquestra criação de Lead+Talk sandbox + dispatch de turn.

Não chama run_turn direto (Q1 Nicolas: reusar arq). Apenas grava inbound_messages
e enfileira process_sandbox_turn pro worker drenar.

Lead criado sempre com:
- is_sandbox=true
- status='active' (não 'pending_assignment' — sandbox não passa por HITL inbox)
- whatsapp_e164 fake derivado de UUID, único por tenant

Talk criado sempre com:
- is_sandbox=true
- sandbox_llm_mode setado conforme escolha do operador
- handling_mode='ai'
- status='active'
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings


class SandboxService:
    """Cria + opera Talks sandbox via DB-as-source-of-truth."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_talk(
        self,
        *,
        tenant: Tenant,
        treeflow_id: str,
        sandbox_llm_mode: Literal["real", "fake"],
        display_name: str | None = None,
    ) -> Talk:
        """Cria Lead sandbox + Talk sandbox + TalkFlowState inicial.

        Retorna o Talk criado (com state attached).
        """
        # Resolve TreeflowVersion (snapshot mais recente publicada do tenant)
        treeflow_version = await self._resolve_latest_version(tenant.id, treeflow_id)
        if treeflow_version is None:
            raise ValueError(
                f"No TreeflowVersion found for tenant={tenant.slug!r} "
                f"treeflow_id={treeflow_id!r}. Run seed script first."
            )

        # Cria Lead sandbox — phone fake garante uniqueness sem colidir com prod
        fake_phone = f"+555099{secrets.token_hex(4)}"  # +55 50 99XXXXXXXX
        lead = Lead(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            whatsapp_e164=fake_phone,
            external_label=display_name or "Lead Sandbox",
            display_name=display_name,
            status="active",  # sandbox bypassa HITL inbox
            is_sandbox=True,
            channel_identifiers={"whatsapp": fake_phone},
        )
        self.session.add(lead)
        await self.session.flush()  # garante ID

        now = datetime.now(UTC)
        talk = Talk(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            lead_id=lead.id,
            treeflow_id=treeflow_id,
            treeflow_version_id=treeflow_version.id,
            status="active",
            handling_mode="ai",
            created_at=now,
            last_message_at=now,
            turn_count=0,
            tokens_consumed={},
            is_sandbox=True,
            sandbox_llm_mode=sandbox_llm_mode,
        )
        self.session.add(talk)
        await self.session.flush()

        # TalkFlowState inicial (collected vazio, current_node = entry do treeflow)
        # Resolve entry_node lendo o YAML em memória
        from ai_sdr.flowengine.treeflow_loader import load_treeflow

        tenants_dir = Path(get_settings().tenants_dir)
        treeflow_yaml = tenants_dir / tenant.slug / "treeflows" / f"{treeflow_id}.yaml"
        treeflow_dom = load_treeflow(treeflow_yaml)

        state = TalkFlowState(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            talk_id=talk.id,
            current_node=treeflow_dom.entry_node,
            collected={},
            extracted_facts={},
            messages=[],
            objections_handled=[],
            updated_at=now,
        )
        self.session.add(state)
        await self.session.commit()

        return talk

    async def list_sandbox_talks(self, tenant_id: uuid.UUID) -> list[Talk]:
        """Lista Talks sandbox ATIVOS do tenant (pro dashboard)."""
        rows = (
            await self.session.execute(
                select(Talk)
                .where(
                    Talk.tenant_id == tenant_id,
                    Talk.is_sandbox.is_(True),
                    Talk.status == "active",
                )
                .order_by(Talk.last_message_at.desc())
                .limit(50)
            )
        ).scalars().all()
        return list(rows)

    async def record_operator_inbound(
        self,
        *,
        tenant_id: uuid.UUID,
        talk_id: uuid.UUID,
        text: str,
    ) -> InboundMessageRow:
        """Persiste a mensagem do operador como inbound_messages (queued).

        Worker process_sandbox_turn vai drenar e rodar run_turn.
        """
        talk = (
            await self.session.execute(
                select(Talk).where(
                    Talk.id == talk_id, Talk.is_sandbox.is_(True), Talk.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if talk is None:
            raise ValueError(f"sandbox talk {talk_id} not found")

        # Lead lookup pra pegar phone fake (necessário pro adapter contract).
        # Defense-in-depth: filter by tenant_id even though RLS already gates.
        lead = (
            await self.session.execute(
                select(Lead).where(
                    Lead.id == talk.lead_id, Lead.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if lead is None:
            raise ValueError(f"lead {talk.lead_id} not found")

        now = datetime.now(UTC)
        msg = InboundMessageRow(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            lead_id=lead.id,
            provider="sandbox",
            external_id=str(uuid.uuid4()),  # sandbox-unique
            from_address=lead.whatsapp_e164 or "sandbox",
            text=text,
            received_at=now,
            ingested_at=now,
            status="queued",
            raw={"sandbox": True, "talk_id": str(talk_id)},
        )
        self.session.add(msg)
        await self.session.commit()
        return msg

    async def _resolve_latest_version(
        self, tenant_id: uuid.UUID, treeflow_id: str
    ) -> TreeflowVersion | None:
        return (
            await self.session.execute(
                select(TreeflowVersion)
                .where(
                    TreeflowVersion.tenant_id == tenant_id,
                    TreeflowVersion.treeflow_id == treeflow_id,
                )
                .order_by(TreeflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
