"""When a built SPA exists, FastAPI serves it at /inbox; otherwise the app
still boots and the route is absent (conditional mount)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_inbox_mounted_when_build_exists(tmp_path, monkeypatch):
    from ai_sdr import main as main_mod

    build = tmp_path / "inbox"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>x</title>")
    monkeypatch.setattr(main_mod, "_inbox_static_dir", lambda: build)

    app = main_mod.create_app()
    paths = [getattr(r, "path", "") for r in app.routes]
    assert any(p.startswith("/inbox") for p in paths)


def test_app_boots_without_build(monkeypatch):
    from ai_sdr import main as main_mod

    monkeypatch.setattr(main_mod, "_inbox_static_dir", lambda: Path("/nonexistent/inbox"))
    app = main_mod.create_app()  # must not raise
    assert app is not None
