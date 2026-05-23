"""TalkFlowRuntime — orchestrates publish_version, create, step using all the pieces."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import yaml
from langchain_core.runnables.config import RunnableConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.llm_yaml import LLMDefaults
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import checkpointer_from_settings
from ai_sdr.treeflow.compiler import LLMFactory, compile_treeflow
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.state import TalkFlowState


@dataclass
class StepResult:
    talkflow_id: uuid.UUID
    response_text: str
    current_node: str
    completed: bool
    collected: dict[str, Any]


SecretsResolver = Callable[[str], dict[str, str]]
"""(tenant_slug) -> {secret_name: value}. Default: SopsLoader.load."""


class TalkFlowRuntime:
    def __init__(
        self,
        *,
        tenant_loader: TenantLoader,
        treeflow_loader: TreeFlowLoader,
        sops_loader: SopsLoader,
        llm_factory: LLMFactory | None = None,
        secrets_resolver: SecretsResolver | None = None,
    ) -> None:
        self._tenants = tenant_loader
        self._treeflows = treeflow_loader
        self._sops = sops_loader
        self._llm_factory = llm_factory
        self._resolve_secrets: SecretsResolver = secrets_resolver or self._sops.load

    # ---------- public ----------

    async def publish_version(
        self,
        session: AsyncSession,
        tenant: Tenant,
        treeflow_id: str,
    ) -> TreeflowVersion:
        """Snapshot tenants/<slug>/treeflows/<treeflow_id>.yaml into treeflow_versions.

        Idempotent — returns the existing row if (tenant, id, version, hash) match.
        Raises ValueError if the same (tenant, id, version) was published with a
        different hash (bump the version field before re-publishing).
        """
        tf = self._treeflows.load(tenant.slug, treeflow_id)
        raw = self._treeflows.raw_yaml(tenant.slug, treeflow_id)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        await set_tenant_context(session, tenant.id)
        existing = (
            await session.execute(
                select(TreeflowVersion).where(
                    TreeflowVersion.tenant_id == tenant.id,
                    TreeflowVersion.treeflow_id == treeflow_id,
                    TreeflowVersion.version == tf.version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.content_hash != digest:
                raise ValueError(
                    f"TreeFlow {treeflow_id} v{tf.version} already published with a "
                    "different hash; bump the version field before re-publishing."
                )
            return existing

        row = TreeflowVersion(
            tenant_id=tenant.id,
            treeflow_id=treeflow_id,
            version=tf.version,
            content_hash=digest,
            content_yaml=raw,
        )
        session.add(row)
        await session.flush()
        return row

    async def create(
        self,
        session: AsyncSession,
        tenant: Tenant,
        lead_id: str,
        treeflow_id: str,
    ) -> TalkFlow:
        """Create a TalkFlow row pinned to the latest published version of `treeflow_id`.

        If a TalkFlow for this (tenant, lead) already exists, returns it.
        """
        await set_tenant_context(session, tenant.id)
        version = (
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
        if version is None:
            raise ValueError(
                f"TreeFlow {treeflow_id} has no published versions for tenant "
                f"{tenant.slug}; call publish_version() first."
            )

        existing = (
            await session.execute(
                select(TalkFlow).where(
                    TalkFlow.tenant_id == tenant.id,
                    TalkFlow.lead_id == lead_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        new_id = uuid.uuid4()
        thread_id = f"{tenant.id}:{new_id}"
        row = TalkFlow(
            id=new_id,
            tenant_id=tenant.id,
            lead_id=lead_id,
            treeflow_version_id=version.id,
            thread_id=thread_id,
        )
        session.add(row)
        await session.flush()
        return row

    async def step(
        self,
        session: AsyncSession,
        tenant: Tenant,
        talkflow_id: uuid.UUID,
        user_input: str,
    ) -> StepResult:
        """Run one turn of the conversation. Persists state via the postgres checkpointer."""
        await set_tenant_context(session, tenant.id)
        talkflow = (
            await session.execute(select(TalkFlow).where(TalkFlow.id == talkflow_id))
        ).scalar_one()
        version = (
            await session.execute(
                select(TreeflowVersion).where(TreeflowVersion.id == talkflow.treeflow_version_id)
            )
        ).scalar_one()

        tf = TreeFlow.model_validate(yaml.safe_load(version.content_yaml))
        tenant_cfg = self._tenants.load(tenant.slug)
        if tenant_cfg.llm is None:
            raise ValueError(f"tenant {tenant.slug} has no llm config in tenant.yaml")
        llm_defaults: LLMDefaults = tenant_cfg.llm
        secrets = self._resolve_secrets(tenant.slug)

        async with checkpointer_from_settings() as saver:
            graph = compile_treeflow(
                tf,
                tenant_llm=llm_defaults,
                secrets=secrets,
                llm_factory=self._llm_factory,
                checkpointer=saver,
            )
            cfg: RunnableConfig = {"configurable": {"thread_id": talkflow.thread_id}}

            # Bootstrap state on first turn; on subsequent turns the checkpointer
            # already holds it, so we only send the new user input.
            checkpoint = await saver.aget(cfg)
            input_state: TalkFlowState
            if checkpoint is None:
                input_state = {
                    "tenant_id": str(tenant.id),
                    "lead_id": talkflow.lead_id,
                    "treeflow_id": tf.id,
                    "treeflow_version": tf.version,
                    "current_node": tf.entry_node,
                    "collected": {},
                    "messages": [],
                    "last_user_input": user_input,
                    "last_agent_response": "",
                    "completed": False,
                }
            else:
                input_state = {"last_user_input": user_input}

            out = await graph.ainvoke(input_state, config=cfg)

        if out.get("completed"):
            talkflow.status = "completed"
            await session.flush()

        return StepResult(
            talkflow_id=talkflow.id,
            response_text=out.get("last_agent_response", ""),
            current_node=out.get("current_node", ""),
            completed=bool(out.get("completed", False)),
            collected=out.get("collected", {}),
        )
