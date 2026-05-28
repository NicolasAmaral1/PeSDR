"""arq WorkerSettings — entrypoint for the `ai-sdr worker` process.

The worker stores shared state (db session factory, adapter registry) on
the arq job context so jobs can reach it without globals. Job functions
are registered in `functions`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings

from ai_sdr.db.engine import build_engine
from ai_sdr.db.session import session_factory_for
from ai_sdr.logging_setup import configure_logging
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.worker.jobs.follow_up_scanner import follow_up_scanner
from ai_sdr.worker.jobs.inbound import process_lead_inbox


async def _on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = structlog.get_logger()
    log.info("worker.starting", env=settings.app_env)
    await ensure_checkpointer_schema()

    ctx["engine"] = build_engine(settings.database_url)
    ctx["session_factory"] = session_factory_for(ctx["engine"])

    tenants_dir = Path(settings.tenants_dir)
    ctx["adapter_registry"] = AdapterRegistry(
        tenant_loader=TenantLoader(tenants_dir),
        sops_loader=SopsLoader(tenants_dir),
    )
    log.info("worker.ready")


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    structlog.get_logger().info("worker.stopped")


class WorkerSettings:
    """arq looks up class attributes by name."""

    functions = [process_lead_inbox]
    cron_jobs = [
        cron(follow_up_scanner, minute=set(range(0, 60)), run_at_startup=False),
    ]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    max_tries = 3
    job_completion_wait = 30  # seconds before retry after unhandled exception

    @classmethod
    def redis_settings(cls) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)
