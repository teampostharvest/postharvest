# ============================================================================
# PostHarvest — developer Makefile
# ============================================================================
# One entry point for everything: Docker (dev+prod), local servers, CLI,
# tests, lint, typecheck.
#
# Docker commands run from docker/ (compose project dir — see DECISIONS.md D6).
# The root .env is for local CLI/tests; compose reads docker/.env.
# ============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help

DOCKER := docker compose
PROD_FLAGS := -f docker-compose.yml -f docker-compose.prod.yml
HOT_FLAGS := -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hot.yml
PROD_DIR := docker

# Prefer the project venv (.venv) when present; fall back to system python.
# Ubuntu 24.04+ ships PEP 668 (externally-managed) pythons that refuse
# pip installs, so a venv is the reliable host path.
VENV_BIN := .venv/bin
ifneq ($(wildcard $(VENV_BIN)/python),)
  PY := $(VENV_BIN)/python
else
  PY := python
endif
PIP := $(PY) -m pip
UVICORN := $(PY) -m uvicorn

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort -k1,1 \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'


# ============================================================================
# Docker — dev stack (backend :8000, frontend :3000, postgres, hot reload)
# ============================================================================

dev: ## Start the dev stack (compose from docker/)
	cd $(PROD_DIR) && $(DOCKER) up

dev-up: ## Start dev stack in the background
	cd $(PROD_DIR) && $(DOCKER) up -d

dev-down: ## Stop dev stack (keeps images/volumes)
	cd $(PROD_DIR) && $(DOCKER) down

dev-build: ## Rebuild dev images and start
	cd $(PROD_DIR) && $(DOCKER) up -d --build

dev-logs: ## Follow dev stack logs
	cd $(PROD_DIR) && $(DOCKER) logs -f

dev-ps: ## List dev stack containers
	cd $(PROD_DIR) && $(DOCKER) ps


# ============================================================================
# Docker — production stack (nginx :80/:443 + hardened prod layer)
# ============================================================================

prod-up: ## Start the production stack (nginx, frontend, backend, node, redis)
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod up -d

prod-build: ## Rebuild prod images and start
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod up -d --build

prod-down: ## Stop the production stack
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod down

prod-restart: ## Restart production containers (no rebuild)
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod restart

prod-logs: ## Follow prod stack logs
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod logs -f

prod-ps: ## List prod stack containers
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod ps

prod-health: ## Hit the prod health endpoint via nginx
	curl -fsS http://localhost/api/health && echo


# ============================================================================
# Docker — hot-reload frontend inside the prod topology (nginx :443 + auth)
# ============================================================================
# Keeps nginx + hardened backend; swaps only the frontend to `next dev` with
# ./frontend bind-mounted. Always browse https://localhost/ (same-origin /api).

hot-up: ## Build + start hot-reload frontend (nginx + backend untouched)
	cd $(PROD_DIR) && $(DOCKER) $(HOT_FLAGS) --profile prod up -d --build --no-deps frontend

hot-off: ## Restore the static prod frontend (rebuilds nothing)
	cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod up -d --no-deps frontend

hot-logs: ## Follow hot frontend logs
	cd $(PROD_DIR) && $(DOCKER) $(HOT_FLAGS) --profile prod logs -f frontend


# ============================================================================
# Python / backend (host, outside Docker)
# ============================================================================

backend-install: ## Install backend requirements into the current interpreter
	$(PIP) install -r backend/requirements.txt

backend-dev: ## Run uvicorn with reload against local code (cwd backend)
	$(UVICORN) backend.main:app --reload --reload-dir backend --host 127.0.0.1 --port 8000

backend-start: ## Run uvicorn (no reload) against local code
	$(UVICORN) backend.main:app --host 127.0.0.1 --port 8000

go-worker-build: ## Build the go worker binary (offline, vendored)
	cd golang && GOPROXY=off go build -o /tmp/go-worker ./cmd/server

go-server: ## Run the go worker server (host dev; PORT env, default 8080)
	cd golang && go run ./cmd/server


# ============================================================================
# CLI (host, outside Docker)
# ============================================================================

cli: ## Show CLI help
	$(PY) cli.py --help

cli-accounts: ## List saved Facebook accounts
	$(PY) cli.py accounts

cli-login: ## Open browser to log into Facebook (option: ACCOUNT=myaccount)
	$(PY) cli.py login --account $(ACCOUNT)

cli-scrape: ## Scrape posts (e.g. URL=https://facebook.com/x --browser MAXX=20 EXPORT=xlsx OUT=posts.xlsx)
	$(PY) cli.py scrape $(URL) $(if $(BROWSER),--browser ,)$(if $(MAXX),--max-posts $(MAXX) ,)$(if $(EXPORT),--export $(EXPORT) ,)$(if $(OUT),--output $(OUT) ,)


# ============================================================================
# Frontend (host, outside Docker)
# ============================================================================

frontend-install: ## Install frontend dependencies (npm ci)
	cd frontend && npm ci

frontend-dev: ## Next.js dev server (hot reload, http://localhost:3000)
	cd frontend && npm run dev

frontend-build: ## Production build
	cd frontend && npm run build

frontend-start: ## Serve a production build (port 3000)
	cd frontend && npm run start


# ============================================================================
# Tests & checks
# ============================================================================

test: ## Backend test suite (pytest, includes CLI + scraper)
	$(PY) -m pytest tests/ -v --tb=short

test-frontend: ## Frontend unit tests (vitest)
	cd frontend && npm run test

test-node: ## Node fetcher tests (vitest)
	cd node && npm run test

test-go: ## Go worker tests (offline, vendored deps — no go get ever)
	cd golang && test -z "$$(gofmt -l . | grep -v '^vendor/')" && GOPROXY=off go test -count=1 ./...

test-all: test test-frontend test-node test-go ## Run ALL suites (backend + frontend + node + go)

checks: typecheck lint lint-ff ## Frontend static checks (typecheck + lint)

lint: ## Backend lint (if configured) + frontend eslint
	cd frontend && npm run lint

lint-ff: ## Frontend fast lint (oxlint)
	cd frontend && npm run lint:ox

typecheck: ## Frontend TypeScript check
	cd frontend && npx tsc --noEmit

typecheck-node: ## Node service TypeScript check
	cd node && npm run typecheck


# ============================================================================
# Whole-codebase status (the one command that shows everything)
# ============================================================================

status: ## Show git state, compose services, and containers at a glance
	@echo "── git ───────────────────────────────────────────────"
	@printf 'branch: %s\n' "$$(git branch --show-current)"
	@echo "worktree:"; git status --short | sed 's/^/  /' || true
	@echo
	@echo "── compose services (base) ──────────────────────────"
	@cd $(PROD_DIR) && $(DOCKER) ps -a --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo "  (no compose containers)"
	@echo
	@echo "── compose services (prod profile) ──────────────────"
	@cd $(PROD_DIR) && $(DOCKER) $(PROD_FLAGS) --profile prod ps -a --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo "  (no prod containers)"
	@echo
	@echo "── project containers (any compose project) ─────────"
	@docker ps -a --filter name=postharvest --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true

ps: ## Alias: compose ps (base)
	cd $(PROD_DIR) && $(DOCKER) ps

redis-cli: ## Open a redis-cli shell in the compose-managed redis
	docker exec -it postharvest-redis redis-cli


# ============================================================================
# Install everything (host tools)
# ============================================================================

install: backend-install frontend-install ## Install host backend + frontend deps

setup: install ## Alias for full host setup