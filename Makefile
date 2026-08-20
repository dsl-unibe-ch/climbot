.PHONY: help venv install install-dev check-env \
        backend frontend scrape sync-admin-docs ingest run-etl qdrant-up qdrant-query token list-models \
        build deploy deploy-data ingest-remote up up-prod down logs clean \
        precommit lint format

UV      := uv
COMPOSE := docker compose
ENV     ?= dev  # override with ENV=prod for production (e.g. make ingest ENV=prod)
VM_HOST ?= $(shell grep '^VM_HOST=' .env 2>/dev/null | cut -d'=' -f2-)

# Image tag derived from pyproject.toml; consumed by docker-compose via ${VERSION}
VERSION := $(shell $(UV) run python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null || python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
export VERSION

# ─────────────────────────────────────────────────────────────────────────────
help: ## Show available commands
	@python -c "import re; [print(f'{m.group(1):22} {m.group(2)}') for line in open('Makefile', encoding='utf-8') for m in [re.match(r'^([a-zA-Z_-]+):.*?## (.*)$$', line)] if m]"

# ── Setup ─────────────────────────────────────────────────────────────────────

venv: ## Create project virtual environment (.venv/)
	$(UV) venv

install: venv ## Install all Python dependencies
	$(UV) pip install -r backend/requirements.txt
	$(UV) pip install -r frontend/requirements.txt
	$(UV) pip install -r scraper/requirements.txt

install-dev: install ## Install dev tools and set up pre-commit
	$(UV) pip install pre-commit ruff
	pre-commit install
	@echo ""
	@echo "Tip: run 'detect-secrets scan > .secrets.baseline' if you add detect-secrets to pre-commit"

check-env: ## Verify service env files exist (required before most commands)
	@test -f backend/.env.$(ENV) || (echo "ERROR: backend/.env.$(ENV) not found. Copy backend/.env.$(ENV).example to backend/.env.$(ENV) and fill in your values." && exit 1)
	@test -f frontend/.env.$(ENV) || (echo "ERROR: frontend/.env.$(ENV) not found. Copy frontend/.env.$(ENV).example to frontend/.env.$(ENV) and fill in your values." && exit 1)

# ── Local development ─────────────────────────────────────────────────────────

token: check-env ## Acquire an Entra ID access token via device-code flow (prints to stdout)
	$(UV) run python scripts/get_token.py

list-models: check-env ## List all model IDs available on the configured endpoint
	$(UV) run python -c "\
from dotenv import load_dotenv; load_dotenv('backend/.env.$(ENV)'); \
import os; from openai import OpenAI; \
c = OpenAI(api_key=os.environ['OPENAI_API_KEY'], base_url=os.environ.get('OPENAI_BASE_URL') or None); \
[print(m.id) for m in c.models.list().data]"

scrape: check-env ## Run the Scrapy climate spider (downloads to data/scraped_docs/)
	cd scraper && SCRAPY_OUTPUT_DIR=../data/scraped_docs $(UV) run scrapy crawl climate_spider

qdrant-up: check-env ## Start the Qdrant vector DB service and wait until it accepts connections
	$(COMPOSE) up -d qdrant
	@echo "Waiting for Qdrant on localhost:6333 ..."
	@until $(UV) run python -c "import socket;socket.create_connection(('localhost',6333),1).close()" 2>/dev/null; do sleep 1; done
	@echo "Qdrant is ready."

qdrant-query: check-env ## Inspect Qdrant collections. ARGS='--search "query" --limit 5 --collection climate_docs'
	$(UV) run python scripts/query_qdrant.py $(ARGS)

sync-admin-docs: ## Copy admin_docs/ into scraped_docs/ (merges without deleting scraped content)
	@$(UV) run python -c "\
import shutil, pathlib; \
src=pathlib.Path('data/admin_docs'); dst=pathlib.Path('data/scraped_docs'); \
[dst.joinpath(s.relative_to(src)).parent.mkdir(parents=True,exist_ok=True) or \
 shutil.copy2(s, dst/s.relative_to(src)) \
 for s in src.rglob('*') if s.is_file() and not s.name.startswith('.')]"

ingest: check-env qdrant-up sync-admin-docs ## Drop Qdrant collections and index data/scraped_docs/ from scratch
	cd backend && DATA_DIR=../data/scraped_docs $(UV) run python -m app.core.ingestion --fresh

run-etl: check-env qdrant-up ## Start Qdrant, scrape, then wipe and reindex from scratch
	$(MAKE) scrape
	$(MAKE) ingest

backend: check-env qdrant-up ## Run FastAPI dev server (hot-reload) on :8000
	cd backend && $(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend: check-env ## Run Streamlit dev server on :8501
	cd frontend && $(UV) run streamlit run app.py --server.port 8501 --server.address 0.0.0.0

# ── Docker ────────────────────────────────────────────────────────────────────

build: ## Build all Docker images (no cache)
	$(COMPOSE) build --no-cache

deploy: build ## Build images locally, ship everything to VM, and start the production stack
	@test -n "$(VM_HOST)" || (echo "ERROR: VM_HOST not set. Add VM_HOST=user@host to .env" && exit 1)
	@echo "==> Creating remote directories..."
	ssh $(VM_HOST) "mkdir -p ~/climbot/backend ~/climbot/frontend ~/climbot/nginx"
	@echo "==> Copying compose files, nginx config, and env files..."
	scp docker-compose.yml docker-compose.prod.yml $(VM_HOST):~/climbot/
	scp nginx/nginx.conf $(VM_HOST):~/climbot/nginx/nginx.conf
	scp backend/.env.prod $(VM_HOST):~/climbot/backend/.env.prod
	scp frontend/.env.prod $(VM_HOST):~/climbot/frontend/.env.prod
	@echo "==> Shipping Docker images (this may take a while)..."
	docker save climebot-backend:$(or $(VERSION),latest) climebot-frontend:$(or $(VERSION),latest) \
	  | gzip \
	  | ssh $(VM_HOST) "docker load"
	@echo "==> Starting production stack on VM..."
	ssh $(VM_HOST) "cd ~/climbot && VERSION=$(or $(VERSION),latest) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
	@echo "Deploy complete. Stack is running on $(VM_HOST)."

deploy-data: ## Copy data/admin_docs/ into the running backend container on the VM
	@test -n "$(VM_HOST)" || (echo "ERROR: VM_HOST not set. Add VM_HOST=user@host to .env" && exit 1)
	scp -r data/admin_docs $(VM_HOST):~/climbot_admin_docs_tmp
	ssh $(VM_HOST) "cd ~/climbot && docker compose -f docker-compose.yml -f docker-compose.prod.yml cp ~/climbot_admin_docs_tmp/. backend:/app/data/admin_docs/ && rm -rf ~/climbot_admin_docs_tmp"

ingest-remote: ## Run ingestion inside the backend container on the VM
	@test -n "$(VM_HOST)" || (echo "ERROR: VM_HOST not set. Add VM_HOST=user@host to .env" && exit 1)
	ssh $(VM_HOST) "cd ~/climbot && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python -m app.core.ingestion --fresh"

up: check-env ## Start all services in detached mode (dev)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml up -d

up-prod: check-env ## Start all services with nginx overlay (production)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml up -d

down: ## Stop and remove containers
	$(COMPOSE) down

logs: ## Follow all container logs
	$(COMPOSE) logs -f

clean: ## Remove containers AND persistent volumes (destructive!)
	$(COMPOSE) down -v --remove-orphans

# ── Code quality ──────────────────────────────────────────────────────────────

precommit: ## Run pre-commit hooks on all files
	pre-commit run --all-files

lint: ## Lint with ruff
	ruff check backend/ frontend/ scraper/

format: ## Format with ruff
	ruff format backend/ frontend/ scraper/
