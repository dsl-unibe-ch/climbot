.PHONY: help venv install install-dev check-env \
        backend frontend scrape sync-admin-docs ingest run-etl token list-models \
        build up up-prod down logs clean \
        precommit lint format

UV      := uv
COMPOSE := docker compose

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
	@test -f backend/.env || (echo "ERROR: backend/.env not found. Copy backend/.env.example to backend/.env and fill in your values." && exit 1)
	@test -f frontend/.env || (echo "ERROR: frontend/.env not found. Copy frontend/.env.example to frontend/.env and fill in your values." && exit 1)

# ── Local development ─────────────────────────────────────────────────────────

token: check-env ## Acquire an Entra ID access token via device-code flow (prints to stdout)
	$(UV) run python scripts/get_token.py

list-models: check-env ## List all model IDs available on the configured endpoint
	$(UV) run python -c "\
from dotenv import load_dotenv; load_dotenv('backend/.env'); \
import os; from openai import OpenAI; \
c = OpenAI(api_key=os.environ['OPENAI_API_KEY'], base_url=os.environ.get('OPENAI_BASE_URL') or None); \
[print(m.id) for m in c.models.list().data]"

scrape: check-env ## Run the Scrapy climate spider (downloads to data/scraped_docs/)
	cd scraper && SCRAPY_OUTPUT_DIR=../data/scraped_docs $(UV) run scrapy crawl climate_spider

sync-admin-docs: ## Copy admin_docs/ into scraped_docs/ (merges without deleting scraped content)
	@$(UV) run python -c "\
import shutil, pathlib; \
src=pathlib.Path('data/admin_docs'); dst=pathlib.Path('data/scraped_docs'); \
[dst.joinpath(s.relative_to(src)).parent.mkdir(parents=True,exist_ok=True) or \
 shutil.copy2(s, dst/s.relative_to(src)) \
 for s in src.rglob('*') if s.is_file() and not s.name.startswith('.')]"

ingest: check-env sync-admin-docs ## Drop Qdrant collections and index data/scraped_docs/ from scratch
	cd backend && DATA_DIR=../data/scraped_docs $(UV) run python -m app.core.ingestion --fresh

run-etl: check-env ## Scrape then wipe and reindex from scratch
	$(MAKE) scrape
	$(MAKE) ingest

backend: check-env ## Run FastAPI dev server (hot-reload) on :8000
	cd backend && $(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend: check-env ## Run Streamlit dev server on :8501
	cd frontend && $(UV) run streamlit run app.py --server.port 8501 --server.address 0.0.0.0

# ── Docker ────────────────────────────────────────────────────────────────────

build: ## Build all Docker images (no cache)
	$(COMPOSE) build --no-cache

up: check-env ## Start all services in detached mode (dev)
	$(COMPOSE) up -d

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
