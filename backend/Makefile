SHELL := /bin/bash
# Backend project.  Run every target from this directory (backend/); the project
# is self-contained: its own virtualenv, .env, Compose files and scripts.
VENV ?= .venv
PY := $(VENV)/bin/python
COMPOSE := docker compose
COMPOSE_TEST := docker compose -f compose.test.yaml -p pfa-accounting-test

.DEFAULT_GOAL := help
.PHONY: help env venv install setup keys keys-rotate up down wait-db migrate migrate-test seed-dev dev dev-json-logs api auth-server worker \
        observability-up observability-down \
        test-db-up test-db-down test lint format openapi mail reset-dev clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Create backend/.env from .env.example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created backend/.env from .env.example")

venv: ## Create the project virtualenv
	uv venv --python 3.12 $(VENV)

install: venv ## Install Python dependencies (pinned) into $(VENV)
	uv pip install --python $(VENV) -e '.[dev]'

setup: env install keys ## Create .env, the virtualenv, dependencies and dev keys

keys: ## Generate a development JWT signing key (Ed25519) outside PostgreSQL
	$(PY) -m pfa.scripts.generate_keys

keys-rotate: ## Rotate the development signing key (old key kept for overlap)
	$(PY) -m pfa.scripts.generate_keys --rotate

up: ## Start the development PostgreSQL (keeps the named volume)
	$(COMPOSE) up -d --wait postgres

down: ## Stop the development stack, keeping the development volume
	$(COMPOSE) down

wait-db: ## Wait until the development PostgreSQL reports healthy
	$(COMPOSE) up -d --wait postgres

migrate: ## Migrate both databases (OAuth db, move OAuth rows, application db) as owner
	$(PY) -m pfa.scripts.migrate

migrate-test: ## Migrate both isolated test databases
	PFA_DATABASE_URL="$${PFA_TEST_DATABASE_URL}" \
	PFA_MIGRATION_DATABASE_URL="$${PFA_TEST_MIGRATION_DATABASE_URL}" \
	PFA_OAUTH_DATABASE_URL="$${PFA_TEST_OAUTH_DATABASE_URL}" \
	PFA_OAUTH_MIGRATION_DATABASE_URL="$${PFA_TEST_OAUTH_MIGRATION_DATABASE_URL}" \
	$(PY) -m pfa.scripts.migrate

seed-dev: ## Idempotent synthetic seed (refuses production configuration)
	$(PY) -m pfa.scripts.seed_dev

dev: ## Start the API and the durable worker (Ctrl-C stops both)
	$(PY) -m pfa.scripts.dev

dev-json-logs: ## Like `dev`, with JSON logs (also to .run/dev.log) and OTLP traces/metrics to the local Alloy
	mkdir -p .run
	PFA_LOG_FORMAT=json OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 OTEL_METRIC_EXPORT_INTERVAL=10000 \
	$(PY) -m pfa.scripts.dev 2>&1 | tee .run/dev.log

observability-up: ## Start local Loki + Alloy + Grafana (http://127.0.0.1:3000, admin/admin)
	$(COMPOSE) -f observability/compose.yaml up -d

observability-down: ## Stop the local monitoring stack (keeps its data volumes)
	$(COMPOSE) -f observability/compose.yaml down

api: ## Start only the FastAPI service (reload)
	$(PY) -m uvicorn pfa.main:app --reload --host 127.0.0.1 --port 8000

auth-server: ## Start the OAuth server as its own service on :8001 (split mode, see README)
	$(PY) -m uvicorn pfa.oauth.app:create_authorization_server_app --factory --reload --host 127.0.0.1 --port 8001

worker: ## Start only the durable worker
	$(PY) -m pfa.worker

test-db-up: ## Start the isolated test PostgreSQL project
	$(COMPOSE_TEST) up -d --wait

test-db-down: ## Remove the isolated test PostgreSQL project and its volume
	$(COMPOSE_TEST) down -v --remove-orphans

test: test-db-up ## Isolated test DB + migrations + pytest
	PFA_ENV=test $(PY) -m pfa.scripts.run_tests

lint: ## Ruff lint
	$(VENV)/bin/ruff check .

format: ## Ruff autofix + format
	$(VENV)/bin/ruff check --fix . && $(VENV)/bin/ruff format .

openapi: ## Publish the OpenAPI contract to backend/openapi.json
	$(PY) -m pfa.scripts.export_openapi

mail: ## Show the newest captured development e-mails (TEMPLATE=/EMAIL=/LIMIT= filters)
	$(PY) -m pfa.scripts.show_mail $(if $(TEMPLATE),--template $(TEMPLATE)) $(if $(EMAIL),--email $(EMAIL)) $(if $(LIMIT),--limit $(LIMIT))

reset-dev: ## DESTRUCTIVE: drop the development database volume and re-seed
	$(COMPOSE) down -v --remove-orphans
	$(COMPOSE) up -d --wait postgres
	$(MAKE) migrate seed-dev

clean: ## Remove caches and build output, keeping volumes and .env
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find pfa tests migrations -name '__pycache__' -type d -prune -exec rm -rf {} +
