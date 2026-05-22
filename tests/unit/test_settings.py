import pytest

from ai_sdr.settings import Settings


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@h/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TENANTS_DIR", "tenants")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", "/tmp/age.key")

    s = Settings()

    assert s.database_url == "postgresql+asyncpg://x:y@h/d"
    assert s.redis_url == "redis://h:6379/0"
    assert s.app_env == "production"
    assert s.log_level == "DEBUG"
    assert s.tenants_dir == "tenants"
    assert s.sops_age_key_file == "/tmp/age.key"


def test_settings_app_env_validates_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@h/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("APP_ENV", "bogus")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("TENANTS_DIR", "tenants")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", "/tmp/age.key")

    with pytest.raises(ValueError):
        Settings()
