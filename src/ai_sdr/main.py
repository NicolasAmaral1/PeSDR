"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from ai_sdr.api.routes.health import router as health_router
from ai_sdr.logging_setup import configure_logging
from ai_sdr.settings import get_settings
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=get_settings().log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=get_settings().app_env)
    await ensure_checkpointer_schema()
    log.info("checkpointer.ready")
    yield
    log.info("app.stopping")


def create_app() -> FastAPI:
    app = FastAPI(title="AI SDR", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
