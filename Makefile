# ═══════════════════════════════════════════════════════════════
# SenHarness Makefile
# ═══════════════════════════════════════════════════════════════

.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE ?= docker compose
# `docker-compose.yml` is the dev-mode default (source mounts + --reload +
# pnpm dev). Plain `docker compose up` is dev. Production is a separate
# standalone file passed alone via -f.
COMPOSE_DEV  := $(COMPOSE)
COMPOSE_PROD := $(COMPOSE) -f docker-compose.prod.yml
BACKEND_SVC  := backend
FRONTEND_SVC := frontend

## ─── Meta ────────────────────────────────────────────────
.PHONY: help
help:  ## Show this help
	@awk 'BEGIN{FS=":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z_-]+:.*##/{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## ─── Lifecycle ───────────────────────────────────────────
.PHONY: up
up: ## Start full stack (dev profile)
	$(COMPOSE_DEV) up -d --build

.PHONY: up-fg
up-fg: ## Start full stack in foreground
	$(COMPOSE_DEV) up --build

.PHONY: down
down: ## Stop and remove containers
	$(COMPOSE_DEV) down

.PHONY: restart
restart: down up ## Restart dev stack

.PHONY: ps
ps: ## Show container status
	$(COMPOSE_DEV) ps

.PHONY: logs
logs: ## Tail logs
	$(COMPOSE_DEV) logs -f --tail=200

.PHONY: logs-backend
logs-backend:
	$(COMPOSE_DEV) logs -f --tail=200 $(BACKEND_SVC)

.PHONY: logs-frontend
logs-frontend:
	$(COMPOSE_DEV) logs -f --tail=200 $(FRONTEND_SVC)

## ─── Database ────────────────────────────────────────────
.PHONY: migrate
migrate: ## Run Alembic migrations
	$(COMPOSE_DEV) exec $(BACKEND_SVC) alembic upgrade head

.PHONY: migration
migration: ## Generate a new migration: make migration m="describe change"
	$(COMPOSE_DEV) exec $(BACKEND_SVC) alembic revision --autogenerate -m "$(m)"

.PHONY: downgrade
downgrade: ## Roll back one Alembic revision
	$(COMPOSE_DEV) exec $(BACKEND_SVC) alembic downgrade -1

.PHONY: seed
seed: ## Seed default workspace + default agent
	$(COMPOSE_DEV) exec $(BACKEND_SVC) python -m cli.commands seed

.PHONY: create-admin
create-admin: ## Create a platform admin identity
	$(COMPOSE_DEV) exec $(BACKEND_SVC) python -m cli.commands create-admin

.PHONY: db-shell
db-shell: ## psql shell
	$(COMPOSE_DEV) exec db psql -U $${DB_USER:-senharness} -d $${DB_NAME:-senharness}

## ─── Quality ─────────────────────────────────────────────
.PHONY: lint
lint: lint-backend lint-frontend ## Lint everything

.PHONY: lint-backend
lint-backend:
	$(COMPOSE_DEV) exec $(BACKEND_SVC) ruff check .

.PHONY: lint-frontend
lint-frontend:
	$(COMPOSE_DEV) exec $(FRONTEND_SVC) pnpm lint

.PHONY: format
format: ## Format code
	$(COMPOSE_DEV) exec $(BACKEND_SVC) ruff format .
	$(COMPOSE_DEV) exec $(FRONTEND_SVC) pnpm format

.PHONY: typecheck
typecheck:
	$(COMPOSE_DEV) exec $(BACKEND_SVC) ty check app
	$(COMPOSE_DEV) exec $(FRONTEND_SVC) pnpm typecheck

.PHONY: test
test: test-backend test-frontend ## Run all tests

.PHONY: test-backend
test-backend:
	$(COMPOSE_DEV) exec $(BACKEND_SVC) pytest -x

.PHONY: test-frontend
test-frontend:
	$(COMPOSE_DEV) exec $(FRONTEND_SVC) pnpm test

## ─── Shells ──────────────────────────────────────────────
.PHONY: sh-backend
sh-backend: ## Open a shell in backend container
	$(COMPOSE_DEV) exec $(BACKEND_SVC) bash

.PHONY: sh-frontend
sh-frontend:
	$(COMPOSE_DEV) exec $(FRONTEND_SVC) sh

## ─── Production ──────────────────────────────────────────
.PHONY: prod-up
prod-up:
	$(COMPOSE_PROD) up -d --build

.PHONY: prod-down
prod-down:
	$(COMPOSE_PROD) down

## ─── Clean ───────────────────────────────────────────────
.PHONY: clean
clean: ## Remove build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .next -prune -exec rm -rf {} +
	find . -type d -name .turbo -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +

.PHONY: nuke
nuke: ## DANGER: stop, remove containers AND volumes (wipes DB)
	$(COMPOSE_DEV) down -v
