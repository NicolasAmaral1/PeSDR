"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ai_sdr.api.routes.console_inbox import router as console_inbox_router
from ai_sdr.api.routes.console_me import router as console_me_router
from ai_sdr.api.routes.health import router as health_router
from ai_sdr.api.routes.leads import router as leads_router
from ai_sdr.api.routes.webhooks import router as webhooks_router
from ai_sdr.api.routes.ws_inbox import router as ws_inbox_router
from ai_sdr.logging_setup import configure_logging
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.realtime.hub import InboxHub
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import Settings, get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.web.login import router as console_login_router
from ai_sdr.web.routes import router as console_router
from ai_sdr.web.sandbox import router as sandbox_router  # PR #24


def _inbox_static_dir() -> Path:
    # src/ai_sdr/main.py -> src/ai_sdr/web/static/inbox
    return Path(__file__).parent / "web" / "static" / "inbox"


def _validate_console_secret_key_if_needed(settings: Settings) -> None:
    """If any tenant has console.enabled=true, CONSOLE_SECRET_KEY MUST be set."""
    from pathlib import Path

    from ai_sdr.tenant_loader.loader import TenantLoader

    tdir = Path(settings.tenants_dir)
    if not tdir.is_dir():
        return  # no tenants directory yet (early dev) — nothing to validate

    loader = TenantLoader(tdir)
    for slug_dir in tdir.iterdir():
        if not slug_dir.is_dir():
            continue
        if not (slug_dir / "tenant.yaml").exists():
            continue
        try:
            cfg = loader.load(slug_dir.name)
        except Exception:  # noqa: BLE001,S112
            continue  # broken yaml — let TreeFlowLoader complain elsewhere
        if cfg.console is not None and cfg.console.enabled:
            if not settings.console_secret_key or len(settings.console_secret_key) < 32:
                raise RuntimeError(
                    f"tenant {slug_dir.name!r} has console.enabled=true but "
                    f"CONSOLE_SECRET_KEY is unset or too short (need 32+ chars). "
                    f'Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
            return  # one is enough — secret will be reused for all tenants


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

    # P11: refuse to boot if any tenant has console.enabled=true but
    # CONSOLE_SECRET_KEY is unset (sessions would be unsignable).
    _validate_console_secret_key_if_needed(settings)

    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    tenants_dir = Path(settings.tenants_dir)
    app.state.adapter_registry = AdapterRegistry(
        tenant_loader=TenantLoader(tenants_dir),
        sops_loader=SopsLoader(tenants_dir),
    )
    log.info("messaging.ready")

    # Realtime inbox: shared redis client + per-process pubsub fan-out hub.
    app.state.redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=True
    )
    app.state.inbox_hub = InboxHub()
    await app.state.inbox_hub.start(app.state.redis)
    log.info("realtime.ready")

    yield
    await app.state.inbox_hub.stop()
    await app.state.redis.aclose()
    await app.state.arq_pool.aclose()
    log.info("app.stopping")


def create_app() -> FastAPI:
    app = FastAPI(title="AI SDR", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(leads_router)
    app.include_router(webhooks_router)
    app.include_router(console_login_router)
    app.include_router(console_router)
    app.include_router(sandbox_router)  # PR #24 — sandbox console extension
    app.include_router(console_inbox_router)
    app.include_router(ws_inbox_router)
    app.include_router(console_me_router)
    inbox_dir = _inbox_static_dir()
    if (inbox_dir / "index.html").exists():
        app.mount("/inbox", StaticFiles(directory=str(inbox_dir), html=True), name="inbox")
    return app


app = create_app()
