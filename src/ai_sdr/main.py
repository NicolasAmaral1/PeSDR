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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=settings.app_env)
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
