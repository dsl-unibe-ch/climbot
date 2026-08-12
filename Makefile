.PHONY: help venv install install-dev check-env \
        backend frontend scrape ingest reingest token list-models \
        build up up-prod down logs clean \
        precommit lint format

UV      := uv
COMPOSE := docker compose

# ─────────────────────────────────────────────────────────────────────────────
help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

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

check-env: ## Verify .env exists (required before most commands)
	@test -f .env || (echo "\033[31mERROR: .env not found.\033[0m Copy .env.example → .env and fill in your values." && exit 1)

# ── Local development ─────────────────────────────────────────────────────────

token: check-env ## Acquire an Entra ID access token via device-code flow (prints to stdout)
	$(UV) run python scripts/get_token.py

list-models: check-env ## List all model IDs available on the configured endpoint
	$(UV) run python -c "\
from dotenv import load_dotenv; load_dotenv('.env'); \
import os; from openai import OpenAI; \
c = OpenAI(api_key=os.environ['OPENAI_API_KEY'], base_url=os.environ.get('OPENAI_BASE_URL') or None); \
[print(m.id) for m in c.models.list().data]"

scrape: check-env ## Run the Scrapy climate spider (downloads to data/)
	cd scraper && SCRAPY_OUTPUT_DIR=../data $(UV) run scrapy crawl climate_spider

ingest: check-env ## Ingest documents from data/ into Qdrant (idempotent)
	cd backend && DATA_DIR=../data $(UV) run python -m app.core.ingestion

reingest: check-env ## Drop Qdrant collections and ingest from scratch
	cd backend && DATA_DIR=../data $(UV) run python -m app.core.ingestion --fresh

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
