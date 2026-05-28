"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI

from ai_sdr.api.routes.health import router as health_router
from ai_sdr.api.routes.leads import router as leads_router
from ai_sdr.api.routes.webhooks import router as webhooks_router
from ai_sdr.logging_setup import configure_logging
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import Settings, get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema


def _validate_langsmith_config(settings: Settings) -> None:
    """Warn if LangSmith tracing is half-configured. Does NOT raise — the
    app boots either way; langchain just silently skips emitting traces
    if the API key is missing."""
    if not settings.langchain_tracing_v2:
        return
    if not settings.langsmith_api_key:
        structlog.get_logger().warning(
            "langsmith.misconfigured",
            reason=(
                "LANGCHAIN_TRACING_V2=true but LANGSMITH_API_KEY is unset — "
                "langchain will silently no-op tracing. Either unset "
                "LANGCHAIN_TRACING_V2 or provide a valid LANGSMITH_API_KEY "
                "(from https://smith.langchain.com → API Keys)."
            ),
            project=settings.langchain_project,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=settings.app_env)
    _validate_langsmith_config(settings)
    await ensure_checkpointer_schema()
    log.info("checkpointer.ready")

    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    tenants_dir = Path(settings.tenants_dir)
    app.state.adapter_registry = AdapterRegistry(
        tenant_loader=TenantLoader(tenants_dir),
        sops_loader=SopsLoader(tenants_dir),
    )
    log.info("messaging.ready")

    yield
    await app.state.arq_pool.aclose()
    log.info("app.stopping")


def create_app() -> FastAPI:
    app = FastAPI(title="AI SDR", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(leads_router)
    app.include_router(webhooks_router)
    return app


app = create_app()
