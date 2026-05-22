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
