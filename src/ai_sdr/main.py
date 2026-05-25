"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI

from ai_sdr.api.routes.health import router as health_router
from ai_sdr.api.routes.webhooks import router as webhooks_router
from ai_sdr.logging_setup import configure_logging
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=settings.app_env)
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
    app.include_router(webhooks_router)
    return app


app = create_app()
