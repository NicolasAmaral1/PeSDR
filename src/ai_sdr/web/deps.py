"""Shared console deps — Jinja2 templates instance + tenant loader factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader

# Single Jinja2Templates instance — points at src/ai_sdr/web/templates/.
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def tenant_loader_dep(_request: Request) -> TenantLoader:
    """FastAPI dep — TenantLoader rooted at settings.tenants_dir.

    Stateless; safe to instantiate per request (the underlying YAML cache
    is owned by the loader, not external).
    """
    return TenantLoader(Path(get_settings().tenants_dir))
