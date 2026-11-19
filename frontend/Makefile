SHELL := /bin/bash
# Frontend project.  Run every target from this directory (frontend/); the
# project is self-contained: its own package.json, .env files and node_modules.
# The browser suite needs a running stack provided by the backend project (see
# README.md); this Makefile never starts or reads backend files.
NPM ?= npm
OPENAPI_URL ?= http://127.0.0.1:8000/openapi.json

.DEFAULT_GOAL := help
.PHONY: help install dev build preview test test-watch typecheck e2e types sync-openapi clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install npm dependencies from the lockfile
	$(NPM) ci

dev: ## Start the SPA dev server (http://127.0.0.1:5173)
	$(NPM) run dev

build: ## Type-check and produce a production bundle in dist/
	$(NPM) run build

preview: ## Serve the production bundle
	$(NPM) run preview

test: ## Vitest component tests (single run)
	$(NPM) run test -- --run

test-watch: ## Vitest component tests (watch mode)
	$(NPM) run test

typecheck: ## TypeScript project check without emitting
	$(NPM) run typecheck

e2e: ## Playwright browser tests against a running stack (E2E_* env)
	$(NPM) run test:e2e

types: ## Regenerate TypeScript types from the pinned openapi.json
	$(NPM) run generate:types

sync-openapi: ## Fetch the OpenAPI contract from a running API and regenerate types
	OPENAPI_URL=$(OPENAPI_URL) $(NPM) run sync:openapi && $(NPM) run generate:types

clean: ## Remove build output and local report directories
	rm -rf dist node_modules/.vite test-results playwright-report
