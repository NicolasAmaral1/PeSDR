# Foundation & Multi-tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap the AI SDR project with multi-tenant database isolation (Postgres RLS), tenant config loaded from YAML, encrypted secrets (SOPS + age), structured logging, and a healthy FastAPI app.

**Architecture:** Async Python (3.12) + FastAPI + SQLAlchemy 2 (asyncpg) + Alembic. Postgres 16 with pgvector extension. Multi-tenancy via Row-Level Security policies on every table, scoped per-connection via `SET LOCAL app.current_tenant`. Tenant configuration (YAML) and encrypted secrets (SOPS) live in the repo under `tenants/<id>/`. Logs are JSON via structlog.

**Tech Stack:** Python 3.12 · uv (package mgmt) · FastAPI · SQLAlchemy 2 (async) · asyncpg · Alembic · pgvector · Redis (just running, used by later plans) · pydantic v2 + pydantic-settings · structlog · pytest + pytest-asyncio + testcontainers · ruff · mypy · pre-commit · SOPS · age · Docker Compose

---

## File Structure

```
ai-sdr/
├── pyproject.toml                              # uv-managed deps + tool configs
├── uv.lock                                     # lockfile (auto-generated)
├── .python-version                             # "3.12"
├── .editorconfig
├── .pre-commit-config.yaml
├── .env.example                                # template (committed)
├── .env                                        # local (gitignored)
├── .sops.yaml                                  # SOPS recipients config
├── Makefile
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_extensions.py
│       └── 0002_tenants_table.py
├── src/
│   └── ai_sdr/
│       ├── __init__.py
│       ├── main.py                             # FastAPI app + lifespan
│       ├── settings.py                         # pydantic-settings, reads .env
│       ├── logging_setup.py                    # structlog config
│       ├── db/
│       │   ├── __init__.py
│       │   ├── engine.py                       # async engine factory
│       │   ├── session.py                      # async sessionmaker + dependency
│       │   ├── base.py                         # DeclarativeBase
│       │   └── rls.py                          # set_tenant_context helper
│       ├── models/
│       │   ├── __init__.py
│       │   └── tenant.py                       # Tenant SQLAlchemy model
│       ├── schemas/
│       │   ├── __init__.py
│       │   └── tenant_yaml.py                  # Pydantic schemas for tenant.yaml
│       ├── tenant_loader/
│       │   ├── __init__.py
│       │   └── loader.py                       # load + validate + cache
│       ├── secrets/
│       │   ├── __init__.py
│       │   └── sops_loader.py                  # decrypt SOPS files
│       └── api/
│           ├── __init__.py
│           ├── deps.py                         # FastAPI dependencies
│           └── routes/
│               ├── __init__.py
│               └── health.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py                             # testcontainers fixtures
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_settings.py
│   │   ├── test_tenant_yaml_schema.py
│   │   └── test_logging_setup.py
│   └── integration/
│       ├── __init__.py
│       ├── test_db_extensions.py
│       ├── test_rls_isolation.py
│       ├── test_tenant_loader.py
│       ├── test_sops_loader.py
│       └── test_health_endpoint.py
└── tenants/
    └── example/
        ├── tenant.yaml                         # fixture tenant for tests/dev
        └── secrets.enc.yaml                    # SOPS-encrypted (committed)
```

**Layout note:** Single package `src/ai_sdr/` with submodules now. The spec's `packages/core`, `packages/adapters` split will come in later plans when domains separate cleanly. YAGNI for plan 1.

---

## Prerequisites (one-time host setup)

Before starting Task 1, the engineer's machine needs:

- macOS or Linux
- Docker Desktop installed and running
- `uv` installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `age` installed: `brew install age` (macOS) or `apt install age` (Linux)
- `sops` installed: `brew install sops` (macOS) or download from https://github.com/getsops/sops/releases

Verify:

```bash
docker --version
uv --version
age --version
sops --version
```

---

## Task 1: Python tooling (uv, ruff, mypy, pre-commit, editorconfig)

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.editorconfig`
- Create: `.pre-commit-config.yaml`
- Modify: `.gitignore` (add Python entries — already present from spec commit)

- [ ] **Step 1: Create `.python-version`**

```
3.12
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "ai-sdr"
version = "0.1.0"
description = "Multi-tenant AI SDR platform"
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pgvector>=0.3.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "structlog>=24.4",
    "pyyaml>=6.0",
    "redis>=5.2",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=6.0",
    "httpx>=0.28",
    "testcontainers[postgres]>=4.8",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit>=4.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ai_sdr"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "S", "C4", "RET", "SIM"]
ignore = ["S101"]  # assert is fine in tests

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S105", "S106"]  # allow hardcoded passwords in tests

[tool.mypy]
python_version = "3.12"
strict = true
disallow_untyped_decorators = false
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = "testcontainers.*"
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
    "integration: tests that require docker (postgres, redis)",
]
```

- [ ] **Step 3: Create `.editorconfig`**

```ini
root = true

[*]
indent_style = space
indent_size = 4
end_of_line = lf
charset = utf-8
trim_trailing_whitespace = true
insert_final_newline = true

[*.{yml,yaml,toml,md}]
indent_size = 2
```

- [ ] **Step 4: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
      - id: detect-private-key

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 5: Initialize uv and create source skeleton**

Run:

```bash
mkdir -p src/ai_sdr tests/unit tests/integration
touch src/ai_sdr/__init__.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
uv sync
uv run pre-commit install
```

Expected: `uv.lock` created, all deps installed, pre-commit hook installed.

- [ ] **Step 6: Verify tooling**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Expected: all pass (or only style fixes from ruff format, which we apply with `uv run ruff format .`).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .editorconfig .pre-commit-config.yaml src/ tests/
git commit -m "chore: bootstrap python project with uv, ruff, mypy, pre-commit"
```

---

## Task 2: Makefile and .env.example

**Files:**
- Create: `Makefile`
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example`**

```bash
# Database
DATABASE_URL=postgresql+asyncpg://ai_sdr:ai_sdr_dev@localhost:5432/ai_sdr

# Redis
REDIS_URL=redis://localhost:6379/0

# App
APP_ENV=development
LOG_LEVEL=INFO

# Tenants directory (relative to project root)
TENANTS_DIR=tenants

# SOPS age key file (path to private key)
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
```

- [ ] **Step 2: Create `Makefile`**

```makefile
.PHONY: help up down logs install lint format type test test-unit test-integration migrate clean

help:
	@echo "Targets:"
	@echo "  up                 Start docker compose services (postgres, redis)"
	@echo "  down               Stop docker compose services"
	@echo "  logs               Tail compose logs"
	@echo "  install            uv sync + pre-commit install"
	@echo "  lint               ruff check"
	@echo "  format             ruff format"
	@echo "  type               mypy"
	@echo "  test               run all tests"
	@echo "  test-unit          run unit tests only"
	@echo "  test-integration   run integration tests (needs docker)"
	@echo "  migrate            alembic upgrade head"

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

install:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .

format:
	uv run ruff format .

type:
	uv run mypy src

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v -m integration

migrate:
	uv run alembic upgrade head

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
```

- [ ] **Step 3: Copy `.env.example` to `.env`**

Run:

```bash
cp .env.example .env
```

- [ ] **Step 4: Commit**

```bash
git add Makefile .env.example
git commit -m "chore: add Makefile and .env.example"
```

---

## Task 3: Docker Compose (Postgres + pgvector + Redis)

**Files:**
- Create: `docker-compose.yml`
- Create: `Dockerfile`

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: ai_sdr_postgres
    environment:
      POSTGRES_USER: ai_sdr
      POSTGRES_PASSWORD: ai_sdr_dev
      POSTGRES_DB: ai_sdr
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ai_sdr -d ai_sdr"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    container_name: ai_sdr_redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  postgres_data:
  redis_data:
```

- [ ] **Step 2: Create `Dockerfile` (for app — used in later plans, but ready now)**

```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "ai_sdr.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Start services and verify**

Run:

```bash
make up
docker compose ps
```

Expected: both services show `(healthy)` after ~10s.

- [ ] **Step 4: Verify Postgres connection and pgvector availability**

Run:

```bash
docker exec ai_sdr_postgres psql -U ai_sdr -d ai_sdr -c "SELECT version();"
docker exec ai_sdr_postgres psql -U ai_sdr -d ai_sdr -c "SELECT * FROM pg_available_extensions WHERE name='vector';"
```

Expected: Postgres 16 version printed, and `vector` extension available.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml Dockerfile
git commit -m "feat: docker compose with postgres+pgvector and redis"
```

---

## Task 4: Settings (pydantic-settings) and structlog

**Files:**
- Create: `src/ai_sdr/settings.py`
- Create: `src/ai_sdr/logging_setup.py`
- Create: `tests/unit/test_settings.py`
- Create: `tests/unit/test_logging_setup.py`

- [ ] **Step 1: Write the failing settings test**

`tests/unit/test_settings.py`:

```python
import os

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.settings'`.

- [ ] **Step 3: Implement `src/ai_sdr/settings.py`**

```python
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
    tenants_dir: str = "tenants"
    sops_age_key_file: str


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_settings.py -v`

Expected: PASS.

- [ ] **Step 5: Write failing logging test**

`tests/unit/test_logging_setup.py`:

```python
import json
import logging
from io import StringIO

import structlog

from ai_sdr.logging_setup import configure_logging


def test_configure_logging_emits_json(capsys: object) -> None:
    configure_logging(level="INFO")
    log = structlog.get_logger()
    log.info("hello", tenant_id="abc")

    out = capsys.readouterr().out  # type: ignore[attr-defined]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "no log line was emitted"
    parsed = json.loads(lines[-1])
    assert parsed["event"] == "hello"
    assert parsed["tenant_id"] == "abc"
    assert parsed["level"] == "info"
    assert "timestamp" in parsed


def test_configure_logging_respects_level() -> None:
    configure_logging(level="WARNING")
    assert logging.getLogger().level == logging.WARNING
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_logging_setup.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.logging_setup'`.

- [ ] **Step 7: Implement `src/ai_sdr/logging_setup.py`**

```python
"""structlog configuration for JSON output with tenant/talkflow context."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib logging + structlog for JSON output."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/unit -v`

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/ai_sdr/settings.py src/ai_sdr/logging_setup.py tests/unit/test_settings.py tests/unit/test_logging_setup.py
git commit -m "feat: settings (pydantic-settings) and structlog json logging"
```

---

## Task 5: Database engine, session, and base

**Files:**
- Create: `src/ai_sdr/db/__init__.py` (empty)
- Create: `src/ai_sdr/db/base.py`
- Create: `src/ai_sdr/db/engine.py`
- Create: `src/ai_sdr/db/session.py`

- [ ] **Step 1: Create `src/ai_sdr/db/__init__.py`**

```python
```

(empty file)

- [ ] **Step 2: Create `src/ai_sdr/db/base.py`**

```python
"""SQLAlchemy declarative base."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide declarative base."""
```

- [ ] **Step 3: Create `src/ai_sdr/db/engine.py`**

```python
"""Async SQLAlchemy engine factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_sdr.settings import get_settings


def create_engine() -> AsyncEngine:
    """Create the async engine using settings.database_url."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )
```

- [ ] **Step 4: Create `src/ai_sdr/db/session.py`**

```python
"""Async session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_sdr.db.engine import create_engine

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Lazy-init the sessionmaker."""
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession."""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session
```

- [ ] **Step 5: Verify imports**

Run:

```bash
uv run python -c "from ai_sdr.db.engine import create_engine; from ai_sdr.db.session import get_session, get_sessionmaker; print('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/db/
git commit -m "feat: async sqlalchemy engine + session"
```

---

## Task 6: Alembic init and first migration (extensions)

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_extensions.py`

- [ ] **Step 1: Initialize alembic**

Run:

```bash
uv run alembic init -t async migrations
```

This creates `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/`.

- [ ] **Step 2: Edit `alembic.ini` — set `sqlalchemy.url` to read from env**

Find `sqlalchemy.url = ...` and replace with:

```ini
sqlalchemy.url = postgresql+asyncpg://ai_sdr:ai_sdr_dev@localhost:5432/ai_sdr
```

(Note: env.py will override this from settings — see next step.)

- [ ] **Step 3: Replace `migrations/env.py` entirely**

```python
"""Alembic env (async, reads DB url from project settings)."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from ai_sdr.db.base import Base
from ai_sdr.settings import get_settings

# noqa: F401 — import models so metadata is populated
from ai_sdr import models  # noqa: F401

config = context.config

# Override URL from settings
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 4: Create stub `src/ai_sdr/models/__init__.py`**

```python
"""SQLAlchemy models. Each model is re-exported here so alembic can discover them."""
```

(Tenant model added next task.)

- [ ] **Step 5: Create first migration manually for extensions**

Create `migrations/versions/0001_extensions.py`:

```python
"""enable required postgres extensions

Revision ID: 0001_extensions
Revises:
Create Date: 2026-05-21 00:00:00
"""

from alembic import op

revision = "0001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector";')


def downgrade() -> None:
    op.execute('DROP EXTENSION IF EXISTS "vector";')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp";')
```

- [ ] **Step 6: Run migration**

Run:

```bash
make migrate
```

Expected: alembic prints `Running upgrade -> 0001_extensions, enable required postgres extensions`.

- [ ] **Step 7: Verify extensions installed**

Run:

```bash
docker exec ai_sdr_postgres psql -U ai_sdr -d ai_sdr -c "SELECT extname FROM pg_extension;"
```

Expected: list contains `uuid-ossp` and `vector`.

- [ ] **Step 8: Write integration test confirming extensions**

`tests/integration/test_db_extensions.py`:

```python
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@pytest.fixture
async def engine() -> AsyncEngine:
    from ai_sdr.settings import get_settings

    eng = create_async_engine(get_settings().database_url)
    yield eng
    await eng.dispose()


@pytest.mark.integration
async def test_extensions_installed(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT extname FROM pg_extension;"))
        names = {row[0] for row in result.all()}
        assert "uuid-ossp" in names
        assert "vector" in names
```

- [ ] **Step 9: Run integration test**

Run: `uv run pytest tests/integration/test_db_extensions.py -v -m integration`

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add alembic.ini migrations/ src/ai_sdr/models/__init__.py tests/integration/test_db_extensions.py
git commit -m "feat: alembic init + extensions migration"
```

---

## Task 7: Tenant model and migration

**Files:**
- Create: `src/ai_sdr/models/tenant.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `migrations/versions/0002_tenants_table.py`

- [ ] **Step 1: Write failing test**

`tests/integration/test_tenant_model.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_create_and_read_tenant(session: AsyncSession) -> None:
    t = Tenant(slug="joana-mentora", display_name="Joana Mentora")
    session.add(t)
    await session.commit()

    fetched = (await session.execute(select(Tenant).where(Tenant.slug == "joana-mentora"))).scalar_one()
    assert fetched.display_name == "Joana Mentora"
    assert fetched.id is not None
```

- [ ] **Step 2: Run test (expect fail — Tenant model doesn't exist)**

Run: `uv run pytest tests/integration/test_tenant_model.py -v -m integration`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.models.tenant'`.

- [ ] **Step 3: Create `src/ai_sdr/models/tenant.py`**

```python
"""Tenant model (multi-tenant root)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 4: Re-export from `src/ai_sdr/models/__init__.py`**

Replace contents of `src/ai_sdr/models/__init__.py`:

```python
"""SQLAlchemy models. Each model is re-exported here so alembic can discover them."""

from ai_sdr.models.tenant import Tenant

__all__ = ["Tenant"]
```

- [ ] **Step 5: Generate migration**

Run:

```bash
uv run alembic revision --autogenerate -m "tenants table"
```

This creates `migrations/versions/<hash>_tenants_table.py`. **Rename it to `0002_tenants_table.py`** and ensure `revision = "0002_tenants_table"` and `down_revision = "0001_extensions"`.

- [ ] **Step 6: Edit the generated migration to set deterministic revision**

Open `migrations/versions/0002_tenants_table.py` and ensure header looks like:

```python
"""tenants table

Revision ID: 0002_tenants_table
Revises: 0001_extensions
Create Date: 2026-05-21 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_tenants_table"
down_revision = "0001_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
```

(RLS policies come in Task 8 as a separate migration.)

- [ ] **Step 7: Run migration**

Run: `make migrate`

Expected: alembic applies `0002_tenants_table`.

- [ ] **Step 8: Run test (now should pass)**

Run: `uv run pytest tests/integration/test_tenant_model.py -v -m integration`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/ai_sdr/models/ migrations/versions/0002_tenants_table.py tests/integration/test_tenant_model.py
git commit -m "feat: tenant model + migration"
```

---

## Task 8: Row-Level Security (RLS) policies and helper

**Files:**
- Create: `src/ai_sdr/db/rls.py`
- Create: `migrations/versions/0003_tenants_rls.py`
- Create: `tests/integration/test_rls_isolation.py`

**Context:** Tenants table itself doesn't need RLS (it's the root catalog). But the helper that sets `app.current_tenant` per-connection is needed now so later tables can use it. To prove RLS works, we add an example "isolated table" `tenant_scoped_demo` with a policy, write the isolation test, then drop the demo table at end of migration (keeping just the helper convention).

Actually, simpler: create a real demonstration table that will be reused — `kb_documents_stub` — with proper RLS. But that pollutes plan 1. Cleanest: create a tiny scratch table `_rls_demo` only for this task's test, and we'll drop it in a later migration when real tables exist. Skip if you prefer: implement only the helper + a unit test using raw SQL.

We take the simpler route: implement the helper, and write an integration test that creates an ephemeral RLS-protected table inside the test transaction (transaction-scoped, doesn't persist).

- [ ] **Step 1: Create `src/ai_sdr/db/rls.py`**

```python
"""Helper to scope a connection/session to a tenant via Postgres RLS."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """SET LOCAL app.current_tenant for the current transaction.

    Must be called at the start of every request that touches tenant-scoped tables.
    LOCAL scope ties the setting to the current transaction, so it does not leak
    across pooled connections.
    """
    await session.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": str(tenant_id)})
```

- [ ] **Step 2: Write the failing RLS isolation test**

`tests/integration/test_rls_isolation.py`:

```python
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_reads(session: AsyncSession) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # Create ephemeral table with RLS inside transaction
    async with session.begin():
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS _rls_test (
                    id SERIAL PRIMARY KEY,
                    tenant_id UUID NOT NULL,
                    value TEXT
                );
                ALTER TABLE _rls_test ENABLE ROW LEVEL SECURITY;
                ALTER TABLE _rls_test FORCE ROW LEVEL SECURITY;
                DROP POLICY IF EXISTS tenant_iso ON _rls_test;
                CREATE POLICY tenant_iso ON _rls_test
                    USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
                """
            )
        )

    # Insert as tenant_a
    async with session.begin():
        await set_tenant_context(session, tenant_a)
        await session.execute(
            text("INSERT INTO _rls_test (tenant_id, value) VALUES (:t, :v)"),
            {"t": str(tenant_a), "v": "row_a"},
        )

    # Insert as tenant_b
    async with session.begin():
        await set_tenant_context(session, tenant_b)
        await session.execute(
            text("INSERT INTO _rls_test (tenant_id, value) VALUES (:t, :v)"),
            {"t": str(tenant_b), "v": "row_b"},
        )

    # Read as tenant_a — should see only row_a
    async with session.begin():
        await set_tenant_context(session, tenant_a)
        rows = (await session.execute(text("SELECT value FROM _rls_test ORDER BY value"))).all()
        assert [r[0] for r in rows] == ["row_a"], f"expected only row_a, got {rows}"

    # Read as tenant_b — should see only row_b
    async with session.begin():
        await set_tenant_context(session, tenant_b)
        rows = (await session.execute(text("SELECT value FROM _rls_test ORDER BY value"))).all()
        assert [r[0] for r in rows] == ["row_b"], f"expected only row_b, got {rows}"

    # Cleanup
    async with session.begin():
        await session.execute(text("DROP TABLE _rls_test;"))
```

- [ ] **Step 3: Run test**

Run: `uv run pytest tests/integration/test_rls_isolation.py -v -m integration`

Expected: PASS (RLS isolates rows as expected).

If FAIL: it likely means the Postgres role is a superuser (superusers bypass RLS unless `FORCE ROW LEVEL SECURITY` is set, which we do). If still failing, check Postgres connection user.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/db/rls.py tests/integration/test_rls_isolation.py
git commit -m "feat: rls helper + isolation integration test"
```

---

## Task 9: Tenant YAML schemas (validation)

**Files:**
- Create: `src/ai_sdr/schemas/__init__.py` (empty)
- Create: `src/ai_sdr/schemas/tenant_yaml.py`
- Create: `tests/unit/test_tenant_yaml_schema.py`

**Scope note:** This plan only validates the **subset** of `tenant.yaml` needed for foundation (id, display_name, timezone, schedule, conversation, optional crm/messaging stubs). Full schemas for guardrails, LLM, media, treeflows are added in later plans where those subsystems are implemented. This avoids dead code.

- [ ] **Step 1: Write the failing schema test**

`tests/unit/test_tenant_yaml_schema.py`:

```python
import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import TenantConfig


def test_minimal_tenant_yaml_validates() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "Joana Mentora",
        "timezone": "America/Sao_Paulo",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.id == "joana-mentora"
    assert cfg.timezone == "America/Sao_Paulo"


def test_full_tenant_yaml_validates() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "Joana Mentora",
        "timezone": "America/Sao_Paulo",
        "schedule": {
            "mon-fri": "08:00-22:00",
            "sat": "09:00-18:00",
            "sun": "off",
            "off_hours_behavior": "queue",
        },
        "conversation": {
            "debounce_ms": 5000,
            "optout_stop_words": ["para", "stop"],
            "optout_action": "end_conversation_silent",
        },
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.schedule is not None
    assert cfg.schedule.off_hours_behavior == "queue"
    assert cfg.conversation is not None
    assert cfg.conversation.debounce_ms == 5000
    assert "para" in cfg.conversation.optout_stop_words


def test_invalid_id_format_rejected() -> None:
    data = {
        "id": "Invalid ID With Spaces",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
    }
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)


def test_invalid_off_hours_behavior_rejected() -> None:
    data = {
        "id": "x",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "schedule": {
            "mon-fri": "08:00-22:00",
            "off_hours_behavior": "fly_to_the_moon",
        },
    }
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)
```

- [ ] **Step 2: Run test (expect fail)**

Run: `uv run pytest tests/unit/test_tenant_yaml_schema.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.schemas'`.

- [ ] **Step 3: Create `src/ai_sdr/schemas/__init__.py`**

```python
```

- [ ] **Step 4: Create `src/ai_sdr/schemas/tenant_yaml.py`**

```python
"""Pydantic schemas validating tenant YAML configuration.

Only the subset required for the foundation plan is implemented here.
Later plans extend with crm, messaging, llm, media, guardrails, treeflows.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mon_fri: str | None = Field(default=None, alias="mon-fri")
    sat: str | None = None
    sun: str | None = None
    off_hours_behavior: Literal["queue", "respond_with_notice"] = "queue"


class ConversationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debounce_ms: int = Field(default=5000, ge=0, le=60_000)
    optout_stop_words: list[str] = Field(default_factory=list)
    optout_action: Literal["end_conversation_silent", "send_confirmation"] = (
        "end_conversation_silent"
    )


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    timezone: str
    schedule: ScheduleConfig | None = None
    conversation: ConversationConfig | None = None

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "id must be a slug: lowercase, digits, hyphens; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        return v
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_tenant_yaml_schema.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/schemas/ tests/unit/test_tenant_yaml_schema.py
git commit -m "feat: tenant yaml pydantic schemas (foundation subset)"
```

---

## Task 10: Tenant loader (YAML → Pydantic with cache)

**Files:**
- Create: `src/ai_sdr/tenant_loader/__init__.py`
- Create: `src/ai_sdr/tenant_loader/loader.py`
- Create: `tenants/example/tenant.yaml`
- Create: `tests/integration/test_tenant_loader.py`

- [ ] **Step 1: Create example tenant fixture**

`tenants/example/tenant.yaml`:

```yaml
id: "example"
display_name: "Example Tenant"
timezone: "America/Sao_Paulo"

schedule:
  mon-fri: "08:00-22:00"
  sat: "09:00-18:00"
  sun: "off"
  off_hours_behavior: "queue"

conversation:
  debounce_ms: 5000
  optout_stop_words: ["para", "pare", "parar", "stop", "sair"]
  optout_action: "end_conversation_silent"
```

- [ ] **Step 2: Write failing loader test**

`tests/integration/test_tenant_loader.py`:

```python
from pathlib import Path

import pytest

from ai_sdr.tenant_loader.loader import (
    TenantLoader,
    TenantNotFoundError,
)


@pytest.fixture
def loader(tmp_path: Path) -> TenantLoader:
    # Copy example tenant into tmp_path/tenants/example/tenant.yaml
    src = Path("tenants/example/tenant.yaml")
    dest_dir = tmp_path / "tenants" / "example"
    dest_dir.mkdir(parents=True)
    (dest_dir / "tenant.yaml").write_text(src.read_text(), encoding="utf-8")
    return TenantLoader(tenants_dir=tmp_path / "tenants")


def test_load_existing_tenant(loader: TenantLoader) -> None:
    cfg = loader.load("example")
    assert cfg.id == "example"
    assert cfg.display_name == "Example Tenant"
    assert cfg.conversation is not None
    assert cfg.conversation.debounce_ms == 5000


def test_load_caches_result(loader: TenantLoader) -> None:
    cfg1 = loader.load("example")
    cfg2 = loader.load("example")
    assert cfg1 is cfg2  # same object due to cache


def test_load_unknown_tenant_raises(loader: TenantLoader) -> None:
    with pytest.raises(TenantNotFoundError):
        loader.load("does-not-exist")


def test_reload_bypasses_cache(loader: TenantLoader) -> None:
    cfg1 = loader.load("example")
    cfg2 = loader.reload("example")
    assert cfg1 is not cfg2
    assert cfg1 == cfg2  # but equal by value
```

- [ ] **Step 3: Run test (expect fail)**

Run: `uv run pytest tests/integration/test_tenant_loader.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Create `src/ai_sdr/tenant_loader/__init__.py`**

```python
"""Tenant configuration loader (YAML → validated Pydantic model)."""

from ai_sdr.tenant_loader.loader import TenantLoader, TenantNotFoundError

__all__ = ["TenantLoader", "TenantNotFoundError"]
```

- [ ] **Step 5: Create `src/ai_sdr/tenant_loader/loader.py`**

```python
"""Load and validate tenant YAML config files."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_sdr.schemas.tenant_yaml import TenantConfig


class TenantNotFoundError(Exception):
    """Raised when a tenant directory does not exist."""


class TenantLoader:
    """Load tenant.yaml files from disk, validate, and cache."""

    def __init__(self, tenants_dir: Path) -> None:
        self._tenants_dir = Path(tenants_dir)
        self._cache: dict[str, TenantConfig] = {}

    def load(self, tenant_id: str) -> TenantConfig:
        """Return cached config or read from disk."""
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        cfg = self._read(tenant_id)
        self._cache[tenant_id] = cfg
        return cfg

    def reload(self, tenant_id: str) -> TenantConfig:
        """Force re-read from disk, bypassing cache."""
        cfg = self._read(tenant_id)
        self._cache[tenant_id] = cfg
        return cfg

    def _read(self, tenant_id: str) -> TenantConfig:
        path = self._tenants_dir / tenant_id / "tenant.yaml"
        if not path.is_file():
            raise TenantNotFoundError(f"tenant config not found at {path}")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return TenantConfig.model_validate(data)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/integration/test_tenant_loader.py -v`

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/tenant_loader/ tenants/example/tenant.yaml tests/integration/test_tenant_loader.py
git commit -m "feat: tenant yaml loader with cache + example fixture"
```

---

## Task 11: SOPS + age (secrets management)

**Files:**
- Create: `.sops.yaml`
- Create: `src/ai_sdr/secrets/__init__.py`
- Create: `src/ai_sdr/secrets/sops_loader.py`
- Create: `tests/integration/test_sops_loader.py`
- Create: `tenants/example/secrets.enc.yaml` (created in steps via sops CLI)

- [ ] **Step 1: Generate an age keypair for local dev (if not yet)**

Run:

```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
# Print the public key — copy it for next step
grep "public key:" ~/.config/sops/age/keys.txt
```

Note the public key (starts with `age1...`). Keep `~/.config/sops/age/keys.txt` safe; it's gitignored by default and **must not** be committed.

- [ ] **Step 2: Create `.sops.yaml` at project root**

Replace `<YOUR_AGE_PUBKEY>` with the key from Step 1.

```yaml
creation_rules:
  - path_regex: tenants/.*/secrets\.enc\.yaml$
    age: <YOUR_AGE_PUBKEY>
```

- [ ] **Step 3: Create the plain example secrets file (temporary, will be encrypted)**

`tenants/example/secrets.yaml` (temporary, **will be deleted**):

```yaml
anthropic_key: "sk-ant-FAKE-FOR-TEST-ONLY"
rd_station_token: "fake-rd-token"
wa_token: "fake-wa-token"
```

- [ ] **Step 4: Encrypt the file with sops**

Run:

```bash
sops --encrypt --in-place tenants/example/secrets.yaml
mv tenants/example/secrets.yaml tenants/example/secrets.enc.yaml
```

Verify the file now contains `ENC[AES256_GCM,...` for each value.

- [ ] **Step 5: Write failing test**

`tests/integration/test_sops_loader.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_sdr.secrets.sops_loader import SopsLoader, SopsDecryptError


@pytest.fixture
def loader(tmp_path: Path) -> SopsLoader:
    # Copy the .sops.yaml AND the example encrypted secrets into tmp_path
    sops_cfg = Path(".sops.yaml")
    secrets_src = Path("tenants/example/secrets.enc.yaml")
    shutil.copy(sops_cfg, tmp_path / ".sops.yaml")
    (tmp_path / "tenants" / "example").mkdir(parents=True)
    shutil.copy(secrets_src, tmp_path / "tenants" / "example" / "secrets.enc.yaml")
    return SopsLoader(tenants_dir=tmp_path / "tenants", project_root=tmp_path)


@pytest.mark.integration
def test_sops_binary_available() -> None:
    """SOPS must be installed on the host."""
    result = subprocess.run(["sops", "--version"], capture_output=True, text=True)
    assert result.returncode == 0


@pytest.mark.integration
def test_decrypt_returns_plaintext_dict(loader: SopsLoader) -> None:
    secrets = loader.load("example")
    assert secrets["anthropic_key"] == "sk-ant-FAKE-FOR-TEST-ONLY"
    assert secrets["rd_station_token"] == "fake-rd-token"


@pytest.mark.integration
def test_decrypt_missing_file_raises(loader: SopsLoader) -> None:
    with pytest.raises(SopsDecryptError):
        loader.load("does-not-exist")
```

- [ ] **Step 6: Run test (expect fail — module missing)**

Run: `uv run pytest tests/integration/test_sops_loader.py -v -m integration`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.secrets'`.

- [ ] **Step 7: Create `src/ai_sdr/secrets/__init__.py`**

```python
"""SOPS-based secrets loader (uses `sops --decrypt`)."""

from ai_sdr.secrets.sops_loader import SopsDecryptError, SopsLoader

__all__ = ["SopsLoader", "SopsDecryptError"]
```

- [ ] **Step 8: Create `src/ai_sdr/secrets/sops_loader.py`**

```python
"""Decrypt SOPS-encrypted YAML files using the `sops` CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml


class SopsDecryptError(Exception):
    """Raised when sops fails to decrypt."""


class SopsLoader:
    """Decrypt and cache tenant secrets files."""

    def __init__(self, tenants_dir: Path, project_root: Path | None = None) -> None:
        self._tenants_dir = Path(tenants_dir)
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._cache: dict[str, dict[str, Any]] = {}

    def load(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        path = self._tenants_dir / tenant_id / "secrets.enc.yaml"
        if not path.is_file():
            raise SopsDecryptError(f"secrets file not found at {path}")
        try:
            result = subprocess.run(
                ["sops", "--decrypt", str(path)],
                capture_output=True,
                text=True,
                check=True,
                cwd=str(self._project_root),
            )
        except subprocess.CalledProcessError as e:
            raise SopsDecryptError(
                f"sops decrypt failed for {path}: {e.stderr.strip()}"
            ) from e
        data = yaml.safe_load(result.stdout)
        if not isinstance(data, dict):
            raise SopsDecryptError(f"expected dict in decrypted file, got {type(data)}")
        self._cache[tenant_id] = data
        return data

    def reload(self, tenant_id: str) -> dict[str, Any]:
        self._cache.pop(tenant_id, None)
        return self.load(tenant_id)
```

- [ ] **Step 9: Run tests**

Run: `uv run pytest tests/integration/test_sops_loader.py -v -m integration`

Expected: all PASS.

- [ ] **Step 10: Update `.gitignore` to ensure age private key is never committed**

The `.gitignore` already excludes `*.dec.yaml` and `secrets.yaml`. Add age keys explicitly:

Append to `.gitignore`:

```
# age private keys (never commit)
keys.txt
*.age
```

- [ ] **Step 11: Commit**

```bash
git add .sops.yaml src/ai_sdr/secrets/ tenants/example/secrets.enc.yaml tests/integration/test_sops_loader.py .gitignore
git commit -m "feat: sops + age secrets loader with example fixture"
```

---

## Task 12: FastAPI app + /health endpoint

**Files:**
- Create: `src/ai_sdr/main.py`
- Create: `src/ai_sdr/api/__init__.py` (empty)
- Create: `src/ai_sdr/api/deps.py`
- Create: `src/ai_sdr/api/routes/__init__.py` (empty)
- Create: `src/ai_sdr/api/routes/health.py`
- Create: `tests/integration/test_health_endpoint.py`

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
touch src/ai_sdr/api/__init__.py src/ai_sdr/api/routes/__init__.py
```

- [ ] **Step 2: Write failing health endpoint test**

`tests/integration/test_health_endpoint.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from ai_sdr.main import app


@pytest.mark.integration
async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"
```

- [ ] **Step 3: Run test (expect fail — app doesn't exist)**

Run: `uv run pytest tests/integration/test_health_endpoint.py -v -m integration`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.main'`.

- [ ] **Step 4: Create `src/ai_sdr/api/deps.py`**

```python
"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.session import get_session as _get_session
from ai_sdr.settings import get_settings


async def db_session() -> AsyncIterator[AsyncSession]:
    async for s in _get_session():
        yield s


async def redis_client() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
```

- [ ] **Step 5: Create `src/ai_sdr/api/routes/health.py`**

```python
"""Health endpoint: pings DB and Redis."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session, redis_client

router = APIRouter()


@router.get("/health")
async def health(
    db: AsyncSession = Depends(db_session),
    rds: aioredis.Redis = Depends(redis_client),
) -> dict[str, str]:
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"db unhealthy: {e}") from e

    try:
        pong = await rds.ping()
        redis_status = "ok" if pong else "fail"
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"redis unhealthy: {e}") from e

    return {"status": "ok", "db": db_status, "redis": redis_status}
```

- [ ] **Step 6: Create `src/ai_sdr/main.py`**

```python
"""FastAPI app entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI

from ai_sdr.api.routes.health import router as health_router
from ai_sdr.logging_setup import configure_logging
from ai_sdr.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=get_settings().log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=get_settings().app_env)
    yield
    log.info("app.stopping")


def create_app() -> FastAPI:
    app = FastAPI(title="AI SDR", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
```

- [ ] **Step 7: Run test**

Run: `uv run pytest tests/integration/test_health_endpoint.py -v -m integration`

Expected: PASS.

- [ ] **Step 8: Run app manually to sanity-check**

Run (in a separate terminal):

```bash
uv run uvicorn ai_sdr.main:app --reload
```

Then in your browser or via curl:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok","db":"ok","redis":"ok"}`.

Stop the server with Ctrl+C.

- [ ] **Step 9: Commit**

```bash
git add src/ai_sdr/main.py src/ai_sdr/api/ tests/integration/test_health_endpoint.py
git commit -m "feat: fastapi app + /health endpoint"
```

---

## Task 13: README and CLAUDE.md

**Files:**
- Create: `README.md`
- Create: `CLAUDE.md`

- [ ] **Step 1: Create `README.md`**

```markdown
# AI SDR

Multi-tenant AI SDR platform. See `docs/superpowers/specs/2026-05-21-ai-sdr-design.md` for the full design.

## Quickstart (foundation)

```bash
# Prereqs: docker, uv, age, sops installed (see Prerequisites in plan 1)

# 1. Install deps
make install

# 2. Start postgres + redis
make up

# 3. Apply migrations
make migrate

# 4. Generate your age keypair (if not yet)
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
# Add your public key to .sops.yaml

# 5. Run the app
uv run uvicorn ai_sdr.main:app --reload

# 6. Hit health endpoint
curl http://localhost:8000/health
```

## Testing

```bash
make test-unit             # fast, no docker
make test-integration      # needs `make up` first
make test                  # both
```

## Project layout

See plan 1 `docs/superpowers/plans/2026-05-21-foundation-multitenancy.md` for full file structure.
```

- [ ] **Step 2: Create `CLAUDE.md`**

```markdown
# AI SDR — Claude Code Instructions

## Project context

Multi-tenant AI SDR platform. Full design: `docs/superpowers/specs/2026-05-21-ai-sdr-design.md`.
Implementation plans: `docs/superpowers/plans/`.

## Tech stack

Python 3.12 · uv · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · Postgres+pgvector · Redis · structlog · LangGraph (later plans) · pytest · ruff · mypy · SOPS+age.

## Workflow

- TDD: write failing test, implement minimum to pass, refactor.
- Commit per task. Reference plan task in commit message.
- Run `make lint format type test-unit` before commits.
- Integration tests require `make up` (docker compose).

## Multi-tenancy

- Every tenant-scoped table has `tenant_id UUID` + Row-Level Security policy.
- Set tenant per-request via `await set_tenant_context(session, tenant_id)`.
- See `src/ai_sdr/db/rls.py`.

## Secrets

- NEVER commit plaintext secrets.
- Tenant secrets live in `tenants/<id>/secrets.enc.yaml` (SOPS-encrypted).
- Read via `SopsLoader.load(tenant_id)`.

## Tenant config

- Each tenant has `tenants/<id>/tenant.yaml` (validated by `ai_sdr.schemas.tenant_yaml.TenantConfig`).
- Load via `TenantLoader.load(tenant_id)`.

## Adding a new tenant

1. Create `tenants/<slug>/tenant.yaml`.
2. Create `tenants/<slug>/secrets.yaml` (plaintext, temporary), then encrypt: `sops --encrypt --in-place tenants/<slug>/secrets.yaml && mv tenants/<slug>/secrets.yaml tenants/<slug>/secrets.enc.yaml`.
3. Insert row in `tenants` table manually via psql for now (an onboarding command will land in a later plan). Example:
   ```sql
   INSERT INTO tenants (slug, display_name) VALUES ('<slug>', '<Display Name>');
   ```
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: README + CLAUDE.md for project setup and conventions"
```

---

## Task 14: Final smoke test (end-to-end foundation)

This task is verification only — no new code, just a clean run-through to confirm everything works on a fresh checkout-equivalent state.

- [ ] **Step 1: Reset to a clean state**

Run:

```bash
make down
docker volume rm ai-sdr_postgres_data ai-sdr_redis_data 2>/dev/null || true
```

- [ ] **Step 2: Bring services back up**

Run:

```bash
make up
sleep 5
make migrate
```

Expected: alembic applies `0001_extensions` and `0002_tenants_table` cleanly.

- [ ] **Step 3: Run full test suite**

Run:

```bash
make test
```

Expected: all unit + integration tests PASS.

- [ ] **Step 4: Run linting + types**

Run:

```bash
make lint
uv run ruff format --check .
make type
```

Expected: no errors.

- [ ] **Step 5: Manual health check**

Run (separate terminal):

```bash
uv run uvicorn ai_sdr.main:app
```

Then:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok","db":"ok","redis":"ok"}`.

- [ ] **Step 6: Confirm DONE**

Tag this milestone in git (no push needed):

```bash
git tag plan1-foundation-complete
git log --oneline | head -20
```

Expected: clean commit history reflecting the tasks, ending with the `docs: README + CLAUDE.md` commit (and possibly the tag).

---

## What this plan deliberately does NOT include

- LangGraph / TreeFlow engine → **Plan 2**
- LLM integration → **Plan 2**
- Field extraction / structured output → **Plan 2**
- KB / pgvector retriever → **Plan 3**
- Guardrails → **Plan 3**
- Objection classifier → **Plan 4**
- CRM adapter / RDStation → **Plan 5**
- WhatsApp / messaging → **Plan 6**
- Media (Whisper / Vision / ElevenLabs) → **Plan 7**
- Follow-up scheduler / metrics / tracing → **Plan 8**
- Production deploy / CI/CD → **Plan 9**

Each later plan builds on this foundation. After completing this plan, you have a multi-tenant-ready Python app skeleton with database isolation, tenant config loading, secrets management, structured logging, and a health endpoint — the substrate for everything else.
