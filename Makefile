SHELL := /bin/bash
# Repository root — integration orchestrator only.
#
# The product is two independent projects:
#   backend/   Python API, worker, database (its own Makefile, .env, Compose)
#   frontend/  React SPA (its own Makefile, package.json, .env)
#
# Nothing here is required to build or run either project on its own; these
# targets only wire the two together for full-product development and CI.
BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help setup up down migrate seed-dev dev dev-json-logs dev-backend dev-frontend observability-up \
        observability-down test test-backend \
        test-frontend test-e2e e2e check-requirements lint openapi types vendor-swagger-ui mail ci clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install both projects (backend virtualenv + frontend node_modules)
	$(MAKE) -C $(BACKEND) setup
	$(MAKE) -C $(FRONTEND) install

up: ## Start the development PostgreSQL
	$(MAKE) -C $(BACKEND) up

down: ## Stop the development stack, keeping the development volume
	$(MAKE) -C $(BACKEND) down

migrate: ## Migrate both databases (application + OAuth) with the owner (DDL) role
	$(MAKE) -C $(BACKEND) migrate

seed-dev: ## Idempotent synthetic seed (refuses production configuration)
	$(MAKE) -C $(BACKEND) seed-dev

dev: ## Start both projects (API + worker + SPA); Ctrl-C stops all
	$(PY) scripts/dev.py

dev-json-logs: ## Like `dev`, with JSON logs (also to backend/.run/dev.log) and OTLP traces/metrics to the local Alloy
	mkdir -p $(BACKEND)/.run
	PFA_LOG_FORMAT=json OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 OTEL_METRIC_EXPORT_INTERVAL=10000 \
	$(PY) scripts/dev.py 2>&1 | tee $(BACKEND)/.run/dev.log

observability-up: ## Start local Loki + Alloy + Grafana for the logs (http://127.0.0.1:3000)
	$(MAKE) -C $(BACKEND) observability-up

observability-down: ## Stop the local monitoring stack
	$(MAKE) -C $(BACKEND) observability-down

dev-backend: ## Start only the backend project (API + worker)
	$(MAKE) -C $(BACKEND) dev

dev-frontend: ## Start only the frontend project (SPA dev server)
	$(MAKE) -C $(FRONTEND) dev

test: test-backend test-frontend ## Backend + frontend test suites

test-backend: ## Backend pytest against the isolated test database
	$(MAKE) -C $(BACKEND) test

test-frontend: ## Frontend Vitest component tests
	$(MAKE) -C $(FRONTEND) test

test-e2e: e2e ## Alias of `e2e`

e2e: ## Full-stack Playwright run: test DB, migrations, API, worker, SPA, fake ANAF
	$(PY) scripts/e2e.py

check-requirements: ## Verify tests/requirements.yaml maps every requirement to real tests
	$(PY) scripts/check_requirements.py

lint: ## Ruff lint for the backend and the integration scripts
	$(MAKE) -C $(BACKEND) lint
	$(PY) -m ruff check --config $(BACKEND)/pyproject.toml scripts

openapi: ## Publish the backend OpenAPI contract to backend/openapi.json
	$(MAKE) -C $(BACKEND) openapi

types: ## Publish OpenAPI, pin it in the frontend and regenerate TS types
	$(MAKE) -C $(BACKEND) openapi
	cp $(BACKEND)/openapi.json $(FRONTEND)/openapi.json
	$(MAKE) -C $(FRONTEND) types

vendor-swagger-ui: ## Copy the pinned Swagger UI assets into the backend static dir
	cd $(FRONTEND) && SWAGGER_UI_TARGET_DIR=../backend/pfa/static/swagger-ui npm run vendor:swagger-ui

mail: ## Show the newest captured development e-mails (link included)
	$(MAKE) -C $(BACKEND) mail

ci: ## CI entry point: lint, traceability, backend tests and frontend tests
	$(MAKE) lint
	$(MAKE) check-requirements
	$(MAKE) test

clean: ## Remove caches and build output from both projects, keeping volumes and .env
	$(MAKE) -C $(BACKEND) clean
	$(MAKE) -C $(FRONTEND) clean
