# 🌍 ClimeBot

A RAG-powered climate change research assistant combining **FastAPI**, **Streamlit**, **Qdrant**, and **GPUstack** models. Supports document chat, semantic image search, and Microsoft Entra ID authentication. Data can be harvested automatically with the included Scrapy pipeline.

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
          │   climate_docs             │
          │   climate_images           │
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

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Edit `backend/.env` and `frontend/.env` to contain the respective variables.

```bash
# Qdrant runs as its own service (container). Bare-metal ETL/dev uses localhost;
# the backend container reaches it as the compose service name "qdrant".
QDRANT_HOST=localhost
QDRANT_PORT=6333

# Put backend settings in backend/.env and frontend settings in frontend/.env.
BACKEND_URL=http://backend:8000   # put this in frontend/.env
```

---

## 3. Local Development (without Docker)

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
it automatically as a dependency. For bare-metal dev keep `QDRANT_HOST=localhost` in
`backend/.env` and `BACKEND_URL=http://localhost:8000` in `frontend/.env`.

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
3. Embeds all text chunks
4. Indexes everything into Qdrant (`climate_docs` + `climate_images` collections)

You can also skip scraping and drop files directly into `data/admin_docs/`, then run `make ingest`.

### Run the backend

```bash
make backend   # FastAPI on http://localhost:8000
```

### Run the frontend (separate terminal)

```bash
make frontend  # Streamlit on http://localhost:8501
```

---

## 4. Docker Deployment (development)

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

> **What actually gets sent to the VM:** only **code** (via git) and **secrets + your curated documents** (via `scp`/`rsync`). The vector database is **never** shipped — it is rebuilt on the VM by running the ETL. Everything under `data/` and `qdrant_storage/` is git-ignored, so it is **not** on GitHub.

### 5a. Get the code onto the VM

```bash
ssh user@<vm-ip>
git clone https://github.com/dsl-unibe-ch/climbot /opt/climebot   # or, if already cloned: cd /opt/climebot && git pull
cd /opt/climebot
```

### 5b. Send secrets and admin documents (these are NOT in git)

Because the `.env` files and everything under `data/` are git-ignored, they must be copied manually **from your local machine**:

```bash
# Secrets
scp backend/.env  user@<vm-ip>:/opt/climebot/backend/.env
scp frontend/.env user@<vm-ip>:/opt/climebot/frontend/.env

# ⚠️ Admin documents — git-ignored, so copy them explicitly
rsync -avz data/admin_docs/ user@<vm-ip>:/opt/climebot/data/admin_docs/
```

> **Reminder:** `data/admin_docs/` holds your hand-curated sources (PDFs, notes) and is **not** on GitHub. If you skip this `scp`/`rsync`, the VM's index will contain only freshly scraped content and will be missing all of your manually added documents.

### 5c. Production `.env` values

Set these on the VM (`backend/.env` / `frontend/.env`):

```dotenv
AZURE_REDIRECT_URI=https://climebot.example.com
BACKEND_CORS_ORIGINS=https://climebot.example.com
BACKEND_URL=http://backend:8000   # frontend/.env
QDRANT_HOST=qdrant                # backend/.env — the compose service name
QDRANT_PORT=6333                  # backend/.env
```

### 5d. Start the production stack

```bash
make up-prod
```

The `docker-compose.prod.yml` overlay:
- Starts the **Qdrant service** with its persistent volume, published on **`127.0.0.1:6333` only** (reachable by the host ETL, never exposed publicly)
- Removes direct port exposure for backend/frontend
- Starts the nginx reverse proxy bound to `127.0.0.1:8080` only

TLS is terminated by the VM's host-level reverse proxy, which forwards traffic to `127.0.0.1:8080`. The Docker containers have no direct public exposure.

### 5e. Run the full ETL on the VM

Scraping **and** ingestion run on the **VM host** (bare metal via `uv`), exactly as they do locally — so the VM needs Python + `uv` installed (see §3). With the Qdrant service already running from `make up-prod`:

```bash
make run-etl   # scrape → sync admin docs → ingest into the running Qdrant service
```

The scraper downloads fresh content on the VM, your scp-copied `admin_docs/` are merged in, and everything is indexed into the Qdrant service over `localhost:6333`. Nothing DB-related was shipped — it is built in place.

### 5f. Register the VM's URL as a Redirect URI in Entra ID

In the Azure Portal, add `https://climebot.example.com` as an additional Redirect URI for your app registration (required for the OAuth login flow).

### 5g. Smoke-test the deployment

```bash
docker compose ps                        # qdrant / backend / frontend should be healthy
curl -s http://127.0.0.1:8080/health     # → {"status": "ok"}
```

Then open the site, sign in, and confirm chat answers cite the expected sources.

---

## 6. Scrapy Spider — targeting custom sites

To override the default crawl targets, set in `.env`:

```dotenv
SCRAPY_TARGET_DOMAINS=climate.nasa.gov,carbonbrief.org
SCRAPY_START_URLS=https://climate.nasa.gov/news/
SCRAPY_DEPTH_LIMIT=2
```

Links that leave the primary domain are followed **one level deep only** (see spider source). Run `make ingest` after each scrape run.

---

## 7. Make Commands Reference

```
make help          Show all commands
make install       Install Python dependencies
make install-dev   Install deps + pre-commit hooks
make backend       Run FastAPI dev server (hot-reload)
make frontend      Run Streamlit dev server
make token         Acquire Entra ID token via device-code flow
make qdrant-up     Start the Qdrant service and wait until ready
make run-etl       Start Qdrant, scrape, then wipe and reindex from scratch
make scrape        Run Scrapy spider (output → data/scraped_docs/)
make sync-admin-docs  Copy admin_docs/ into scraped_docs/
make ingest        Start Qdrant, sync admin docs, drop collections, reindex
make build         Build Docker images
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

## 8. Quick Smoke Tests

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
