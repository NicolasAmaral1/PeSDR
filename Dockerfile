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

# sops — used at runtime to decrypt tenants/<slug>/secrets.enc.yaml
RUN curl -LsSf -o /usr/local/bin/sops \
    https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.amd64 && \
    chmod +x /usr/local/bin/sops

WORKDIR /app

COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "ai_sdr.main:app", "--host", "0.0.0.0", "--port", "8000"]
