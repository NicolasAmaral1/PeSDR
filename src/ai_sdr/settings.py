"""Application settings loaded from environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    redis_url: str
    app_env: Literal["development", "test", "production"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    console_secret_key: str | None = None
    tenants_dir: str = "tenants"
    sops_age_key_file: str

    # LangSmith tracing — opt-in. langchain-core reads env vars directly;
    # these fields exist so main.py startup validator can warn on misconfig.
    langchain_tracing_v2: bool = False
    langsmith_api_key: str | None = None
    langchain_project: str = "pesdr-dev"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
