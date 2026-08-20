# 🌍 ClimeBot

A RAG-powered climate change research assistant combining **FastAPI**, **Streamlit**, **Qdrant**, and **GPUstack** models. Supports document chat, hybrid BM25 + dense vector search, semantic image search, and Microsoft Entra ID authentication. Data can be harvested automatically with the included Scrapy pipeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                              │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTPS (nginx in prod)
          ┌─────────────▼──────────────┐
          │   Streamlit frontend :8501 │  Entra ID OAuth
          │   Azure MSAL auth          │◄─── Microsoft Login
          └─────────────┬──────────────┘
                        │ Bearer JWT  (HTTP/SSE)
          ┌─────────────▼──────────────┐
          │   FastAPI backend   :8000  │
          │   • /chat  (SSE stream)    │
          │   • /search               │──► OpenAI compatible API from GPUStack
          │                           │    (embeddings + GPT-4o)
          └─────────────┬──────────────┘
                        │ HTTP :6333
          ┌─────────────▼──────────────┐
          │   Qdrant service (container)│
          │   named volume: qdrant_data │
          │   climate_docs (dense + sparse)  │
          │   climate_images (dense + sparse) │
          └────────────────────────────┘

  Scraper (local / VM, not in Docker)
  ┌──────────────────────────────────┐
  │  Scrapy CrawlSpider              │
  │  → downloads PDFs + images       │
  │  → saves to data/                │
  └──────────────────────────────────┘
        ↓  make ingest
  ┌──────────────────────────────────┐
  │  Ingestion pipeline              │
  │  PyMuPDF → chunk → embed → index│
  └──────────────────────────────────┘
```

---

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| Python | 3.11 |
| uv | 0.5+ |
| Docker + Docker Compose | Docker 24 / Compose v2 |
| GNU Make | 4.x |
| OpenAI API key (GPU Stack Key) | — |
| Microsoft Entra ID app registration | — |
| fastembed (auto-installed) | `Qdrant/bm25` model downloaded on first use (~50 MB) |

---

## 1. Microsoft Entra ID App Registration

You need **one** app registration that serves both the frontend (OAuth client) and backend (token audience).

### Steps in the Azure Portal

1. Go to **Microsoft Entra ID → App registrations → New registration**.
2. Name it `ClimeBot` and set the **Redirect URI** to `http://localhost:8501` (Web platform).
3. After creation, note the **Application (client) ID** and **Directory (tenant) ID**.
4. **Certificates & secrets** → New client secret → copy the value.
5. **Expose an API** → Add a scope:
   - Application ID URI: `api://<client-id>` (click *Set* to auto-fill)
   - Scope name: choose any name — e.g. `ClimeBot.Access` (this is just a label)
   - Who can consent: *Admins and users*

   > **Why expose an API?** Access tokens carry an `aud` (audience) claim that declares *which resource* they are intended for. By exposing the scope here, tokens that the Streamlit frontend requests will carry `aud = <client-id>` — meaning they are explicitly issued for *your* FastAPI backend. Without this step, MSAL falls back to requesting Microsoft Graph tokens (audience `00000003-0000-0000-c000-000000000000`), which your backend would correctly reject because they weren’t meant for it.

6. **API permissions** → Add the scope you just created (`api://<client-id>/ClimeBot.Access`) to the app itself, then **Grant admin consent**.
7. Copy the full scope URI (`api://<client-id>/ClimeBot.Access`) into `AZURE_API_SCOPE` in your `.env`.

> **Production VM**: add your VM's public URL as an additional Redirect URI (e.g. `https://climebot.example.com`).

---

## 2. Environment Setup

There are two env files per service — one for local development and one for production:

| File | Purpose |
|------|---------|
| `backend/.env.dev` / `frontend/.env.dev` | Local development — edit these on your machine |
| `backend/.env.prod` / `frontend/.env.prod` | Production VM — edit these, then copy to the VM |

```bash
# Local dev
cp backend/.env.dev.example backend/.env.dev
cp frontend/.env.dev.example frontend/.env.dev

# Production
cp backend/.env.prod.example backend/.env.prod
cp frontend/.env.prod.example frontend/.env.prod
```

Key differences between dev and prod:

```bash
# backend/.env.dev
QDRANT_PATH=./qdrant_storage   # local file-based Qdrant
BACKEND_CORS_ORIGINS=http://localhost:8501
AZURE_REDIRECT_URI=http://localhost:8501

# backend/.env.prod
QDRANT_HOST=qdrant             # Docker service name
QDRANT_PORT=6333
BACKEND_CORS_ORIGINS=https://climebot.dsl.unibe.ch
AZURE_REDIRECT_URI=https://climebot.dsl.unibe.ch

# frontend/.env.dev
BACKEND_URL=http://localhost:8000

# frontend/.env.prod
BACKEND_URL=http://backend:8000   # Docker service name
```

---

## 3. Local Development

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Create environment and install dependencies
The following command installs the dependencies for scraper, frontend and backend for the developer as well as the pre-commit hooks.
```bash
make install-dev
```

Qdrant runs as a **separate service** (a `qdrant/qdrant` container) with its data in
a Docker named volume — nothing is stored inside the backend image. Both the backend
and the bare-metal ETL talk to it over HTTP on port `6333`. `make qdrant-up` starts it
and waits until it is ready; `make backend`, `make ingest`, and `make run-etl` all start
it automatically as a dependency. For bare-metal dev keep `QDRANT_PATH=./qdrant_storage` in
`backend/.env.dev` and `BACKEND_URL=http://localhost:8000` in `frontend/.env.dev`.

### ETL — scrape and index data

To run a full refresh (scrape new data from the web and rebuild the index from scratch):

```bash
make run-etl
```

This runs `make scrape` followed by `make ingest` in sequence.

#### Folder layout

```
data/
  admin_docs/    # drop your own PDFs / images / .txt / .md files here
  scraped_docs/  # scraper writes here; admin_docs are merged in before indexing
```

#### Individual ETL commands

```bash
make scrape           # crawl configured sites, download to data/scraped_docs/
make sync-admin-docs  # copy admin_docs/ into scraped_docs/
make ingest           # sync admin docs, DROP collections, and reindex from scratch
```

> `make ingest` always wipes the Qdrant collections and rebuilds from scratch — there is no separate "reingest". This guarantees the index exactly matches what is on disk and prevents stale entries.

The ingestion pipeline:
1. Extracts text from PDFs/DOCX and splits into chunks
2. Extracts images from PDFs, describes each with the vision model, embeds the description
3. Embeds all text chunks with the dense (Qwen) model
4. Generates BM25 sparse vectors for all text and image descriptions via `fastembed`
5. Indexes everything into Qdrant (`climate_docs` + `climate_images` collections), each point storing both a named `dense` and `sparse` vector

You can also skip scraping and drop files directly into `data/admin_docs/`, then run `make ingest`.


## 4. Docker Deployment (development)

Uses `backend/.env.dev` and `frontend/.env.dev` automatically (set in `docker-compose.yml`).

```bash
# Build images
make build

# Start all services (Qdrant, backend, frontend)
make up

# Watch logs
make logs

# Stop
make down
```

Services:
- Streamlit UI → `http://localhost:8501`
- FastAPI docs → `http://localhost:8000/docs`

---

## 5. Remote VM Deployment (production)

> The VM only needs **Docker** installed — no git, no Make, no Python. Images are built locally and shipped over SSH. All config and secrets are copied as part of the deploy command.

### Prerequisites (local, before first deploy)

Edit `backend/.env.prod` and `frontend/.env.prod` locally (see §2). Key values:

```dotenv
# backend/.env.prod
AZURE_REDIRECT_URI=https://climebot.dsl.unibe.ch
BACKEND_CORS_ORIGINS=https://climebot.dsl.unibe.ch
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# frontend/.env.prod
AZURE_REDIRECT_URI=https://climebot.dsl.unibe.ch
BACKEND_URL=http://backend:8000
```

Add your `VM_HOST` address to the root `.env`:


### 5a. Build and deploy

One command builds the images, copies compose files + env files + nginx config, loads the images on the VM, and starts the stack:

```bash
make deploy
```

### 5b. Ship admin documents (first time or when updated)

```bash
make deploy-data
```

### 5c. Run ingestion on the VM

Ingestion runs inside the already-running backend container — no Make or Python needed on the VM host:

```bash
make ingest-remote
```

### 5d. Register the VM's URL as a Redirect URI in Entra ID

### 5f. Register the VM's URL as a Redirect URI in Entra ID

In the Azure Portal, add `https://climebot.example.com` as an additional Redirect URI for your app registration (required for the OAuth login flow).

### 5g. Smoke-test the deployment

```bash
ssh $VM_HOST "cd ~/climbot && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps"
curl -s http://127.0.0.1:8080/health     # → {"status": "ok"}
```

Then open the site, sign in, and confirm chat answers cite the expected sources.

---

## 6. Inspecting the Qdrant Index

`scripts/query_qdrant.py` connects to Qdrant and lets you inspect what is indexed without starting the full stack.

```bash
# List all collections and scroll the first 5 points from each
make qdrant-query

# Run a hybrid BM25 + dense RRF search against both collections
make qdrant-query ARGS='--search "climate extremes Switzerland"'

# Limit results and restrict to one collection
make qdrant-query ARGS='--search "alpine floods" --limit 10 --collection climate_docs'

# Scroll more points without a query
make qdrant-query ARGS='--collection climate_images --limit 20'
```

The script reads `QDRANT_HOST` / `QDRANT_PORT` / `QDRANT_API_KEY` from `backend/.env.dev` (or environment variables). Override the host at runtime:

```bash
QDRANT_HOST=qdrant-vm.example.com make qdrant-query ARGS='--limit 3'
```

---

## 7. Scrapy Spider — targeting custom sites

To override the default crawl targets, set in `.env`:

```dotenv
SCRAPY_TARGET_DOMAINS=climate.nasa.gov,carbonbrief.org
SCRAPY_START_URLS=https://climate.nasa.gov/news/
SCRAPY_DEPTH_LIMIT=2
```

Links that leave the primary domain are followed **one level deep only** (see spider source). Run `make ingest` after each scrape run.

---

## 8. Make Commands Reference

Most commands accept an `ENV` variable (default `dev`). On the VM, prefix commands with `ENV=prod`:

```bash
make ingest ENV=prod   # uses backend/.env.prod, frontend/.env.prod
make run-etl ENV=prod
```

```
make help          Show all commands
make install       Install Python dependencies
make install-dev   Install deps + pre-commit hooks
make backend       Run FastAPI dev server (hot-reload)
make frontend      Run Streamlit dev server
make token         Acquire Entra ID token via device-code flow
make qdrant-up     Start the Qdrant service and wait until ready
make qdrant-query  Inspect Qdrant collections and run test queries (see below)
make run-etl       Start Qdrant, scrape, then wipe and reindex from scratch
make scrape        Run Scrapy spider (output → data/scraped_docs/)
make sync-admin-docs  Copy admin_docs/ into scraped_docs/
make ingest        Start Qdrant, sync admin docs, drop collections, reindex
make build         Build Docker images
make deploy        Build + ship images, copy config/env, start stack on VM
make deploy-data   Copy data/admin_docs/ to the VM
make ingest-remote Run ingestion inside the backend container on the VM
make up            Start all services (dev)
make up-prod       Start with nginx overlay (production)
make down          Stop containers
make logs          Follow container logs
make clean         Remove containers + volumes (destructive)
make precommit     Run pre-commit on all files
make lint          Lint with ruff
make format        Format with ruff
```

---

## 9. Version Footer

The Streamlit UI displays the app version (from `pyproject.toml`) in the bottom-right corner. In Docker the version is injected via the `APP_VERSION` environment variable, which `docker-compose.yml` sets automatically from the `VERSION` Makefile variable. In local dev (no Docker) the frontend reads `pyproject.toml` directly as a fallback.

---

## 10. Quick Smoke Tests

Replace `http://localhost:8000` with your VM URL in production.

### Health check (no auth required)

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

Expected: `{"status": "ok"}`

### Obtain a token

```bash
# Step 1 — run make token; it will print a device-code URL and wait for you to sign in
make token
# Follow the prompt: open https://microsoft.com/devicelogin and enter the code shown.
# Once authenticated, the token is printed to stdout AND saved to .token

# Step 2 — load the token into your shell
export TOKEN=$(cat .token)
echo $TOKEN | cut -c1-40   # sanity check: should start with eyJ
```

> **Do not** use `$(make token 2>/dev/null)` — that hides the device-code prompt and the command will appear to hang.

### Search

```bash
curl -s -X POST http://localhost:8000/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "climate extremes Switzerland", "top_k": 3}' \
  | python -m json.tool
```

### Chat (streaming SSE)

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What is NCCR CLIM+?"}]}'
```

`-N` disables buffering so you see tokens stream in real time.

### FastAPI interactive docs

```
http://localhost:8000/docs
```

---

## 9. Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_TENANT_ID` | — | Entra ID tenant ID |
| `AZURE_CLIENT_ID` | — | App registration client ID |
| `AZURE_CLIENT_SECRET` | — | Client secret |
| `AZURE_REDIRECT_URI` | `http://localhost:8501` | OAuth redirect URI |
| `AZURE_API_SCOPE` | `api://<client-id>/ClimeBot.Access` | Full scope URI from Entra ID "Expose an API" |
| `OPENAI_API_KEY` | — | OpenAI or GPUStack API key |
| `OPENAI_BASE_URL` | _(empty)_ | Custom endpoint e.g. `https://gpustack.unibe.ch/v1`; blank = openai.com |
| `OPENAI_MODEL` | `gpt-4o` | Chat + vision model name at the endpoint |
| `OPENAI_EMBEDDING_MODEL` | `qwen3-embedding-0.6b` | Embedding model name at the endpoint |
| `EMBEDDING_DIM` | `1024` | Output dimension of the embedding model |
| `LLM_TEMPERATURE` | `0.3` | Sampling temperature |
| `LLM_MAX_TOKENS` | `16000` | Max tokens per response |
| `LLM_TOP_P` | `1.0` | Nucleus sampling |
| `QDRANT_HOST` | `localhost` | Qdrant service host — `qdrant` (service name) in Docker, `localhost` for bare-metal ETL |
| `QDRANT_PORT` | `6333` | Qdrant service HTTP port |
| `QDRANT_API_KEY` | _(empty)_ | Required for Qdrant Cloud |
| `BACKEND_HOST` | `0.0.0.0` | Uvicorn bind address — **not a URL**; keep as `0.0.0.0` |
| `BACKEND_PORT` | `8000` | Uvicorn listen port |
| `BACKEND_URL` | `http://backend:8000` | `http://localhost:8000` for local dev |
| `BACKEND_CORS_ORIGINS` | `http://localhost:8501` | Comma-separated allowed origins |
| `DATA_DIR` | `/app/data` | Document directory inside container |
| `CHUNK_SIZE` | `1000` | Text chunk size (characters) |
| `CHUNK_OVERLAP` | `200` | Chunk overlap (characters) |
| `SCRAPY_OUTPUT_DIR` | `./data` | Scraper download directory |
| `SCRAPY_DEPTH_LIMIT` | `3` | Maximum crawl depth |
| `SCRAPY_TARGET_DOMAINS` | _(see spider)_ | Comma-separated target domains |
| `SCRAPY_START_URLS` | _(see spider)_ | Comma-separated seed URLs |

---

## 10. Project Structure

```
ClimeBot/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app + lifespan
│   │   ├── config.py        # Pydantic settings
│   │   ├── auth/
│   │   │   └── azure_auth.py   # JWT validation (Entra ID)
│   │   ├── api/
│   │   │   ├── chat.py      # POST /chat  (SSE stream)
│   │   │   ├── search.py    # POST /search
│   │   │   ├── ingest.py    # POST /ingest
│   │   │   └── health.py    # GET /health
│   │   ├── core/
│   │   │   ├── vectorstore.py  # Qdrant client wrapper
│   │   │   ├── embeddings.py   # Text + image embedding
│   │   │   ├── llm.py          # Streaming GPT-4o
│   │   │   └── ingestion.py    # Parse → chunk → embed → index
│   │   └── models/
│   │       └── schemas.py   # Pydantic request/response models
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── app.py               # Streamlit main entry point
│   ├── auth/
│   │   └── azure_oauth.py   # MSAL authorization-code flow
│   ├── components/
│   │   ├── chat_ui.py       # Streaming chat UI
│   │   └── image_viewer.py  # Search results + image rendering
│   ├── .streamlit/config.toml
│   ├── Dockerfile
│   └── requirements.txt
├── scraper/
│   ├── climebot_scraper/
│   │   ├── settings.py
│   │   ├── items.py
│   │   ├── pipelines.py     # FileDownloadPipeline + MetadataPipeline
│   │   ├── middlewares.py
│   │   └── spiders/
│   │       └── climate_spider.py  # CrawlSpider with link rules
│   ├── scrapy.cfg
│   └── requirements.txt
├── nginx/
│   └── nginx.conf
├── data/                    # Dropped documents live here
├── docker-compose.yml
├── docker-compose.prod.yml
├── Makefile
├── pyproject.toml
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
└── README.md
```

---

## 11. Contributing to Github Repository

Please follow the steps and guidelines prescribed below while contributing to the github repository.

**Never commit directly to `main`.** All changes must go through a pull request.

### Workflow

```bash
# 1. Create a feature branch from main
git checkout main
git pull origin main
git checkout -b feat/your-feature-name

# 2. Make your changes, then lint
make lint

# 3. Fix any reported issues, then format
make format

# 4. Re-run lint to confirm clean
make lint

# 5. Commit
# In the absence of a Jira board (currently the case)
git add .
git commit -m "feat: describe your change"
# 6. Push and open a PR
git push origin feat/your-feature-name
```

Then open a Pull Request on GitHub targeting `main`. Request a review — do not merge your own PR.

### Branch naming

| Prefix | Use for |
|--------|---------|
| `feat/` | New features, tooling, config |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |

### Pre-commit hooks

`make install-dev` wires up pre-commit hooks that run lint and format checks automatically on every `git commit`. If a hook fails, the commit is blocked — fix the reported issues and try again.

To run all hooks manually without committing:

```bash
make precommit
```
