"""Unit-test conftest — seeds the settings singleton so unit tests that
call get_settings() (e.g., cookie-signer tests using _patch_settings)
don't fail with a pydantic ValidationError when no .env file is present.

Only the singleton pointer is pre-seeded; individual tests may overwrite
specific attributes via monkeypatch.setattr(settings, "field", value).
"""

from __future__ import annotations

import ai_sdr.settings as _settings_mod
from ai_sdr.settings import Settings


def pytest_configure(config):  # noqa: ANN001
    """Seed _settings before any test collection or session fixtures run."""
    if _settings_mod._settings is None:
        _settings_mod._settings = Settings(
            database_url="postgresql+asyncpg://ai_sdr_app:x@localhost:15432/ai_sdr",
            redis_url="redis://localhost:16379/0",
            app_env="test",
            sops_age_key_file="/tmp/age.key",
        )
