# 🌍 ClimeBot

A RAG-powered climate change research assistant combining **FastAPI**, **Streamlit**, **Qdrant**, and **OpenAI GPT-4o**. Supports document chat, semantic image search, and Microsoft Entra ID authentication. Data can be harvested automatically with the included Scrapy pipeline.

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
          │   • /search               │──► OpenAI API
          │   • /ingest               │    (embeddings + GPT-4o)
          └─────────────┬──────────────┘
                        │
          ┌─────────────▼──────────────┐
          │   Qdrant vector DB  :6333  │
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
| OpenAI API key | — |
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
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```dotenv
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_ID=<your-client-id>
AZURE_CLIENT_SECRET=<your-client-secret>
AZURE_REDIRECT_URI=http://localhost:8501   # or your VM URL

OPENAI_API_KEY=sk-...

# For local dev change QDRANT_HOST to "localhost"
QDRANT_HOST=qdrant

# For local dev change BACKEND_URL to "http://localhost:8000"
BACKEND_URL=http://backend:8000
```

---

## 3. Local Development (without Docker)

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Create environment and install dependencies

```bash
make install-dev
```

### Start Qdrant (Docker required for local vector DB)

```bash
docker run -d -p 6333:6333 -p 6334:6334 \
  -v qdrant_data:/qdrant/storage \
  qdrant/qdrant:v1.12.0
```

> Set `QDRANT_HOST=localhost` in `.env` when running backend outside Docker.
> Set `BACKEND_URL=http://localhost:8000` in `.env` when running frontend outside Docker.

### Scrape data (first run)

The scraper runs **outside Docker** and populates `data/`:

```bash
make scrape   # crawls configured sites, saves PDFs + images to data/
```

Or drop your own PDFs / images / Markdown files directly into `data/`.

### Ingest into Qdrant

#### First time ingestion
```bash
make ingest   # parse → chunk → embed → index
```

The pipeline will:
1. Extract text from PDFs/DOCX and split into chunks
2. Extract images from PDFs, describe each with the vision model, embed the description
3. Embed all text chunks
4. Index everything into Qdrant (`climate_docs` + `climate_images` collections)

#### Refresh the index

To add new documents without losing existing data (idempotent — same files produce the same point IDs):

```bash
make ingest
```

To wipe all indexed data and rebuild from scratch (e.g. after changing the embedding model or chunk size):

```bash
make reingest
```

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

# Start all services (Qdrant + backend + frontend)
make up

# Watch logs
make logs

# Stop
make down
```

Services:
- Streamlit UI → `http://localhost:8501`
- FastAPI docs → `http://localhost:8000/docs`
- Qdrant dashboard → `http://localhost:6333/dashboard`

---

## 5. Remote VM Deployment (production)

### 5a. Copy project to VM

```bash
# From your local machine
rsync -avz --exclude '.env' --exclude 'data/' \
  ./ user@<vm-ip>:/opt/climebot/
```

### 5b. Set up .env on VM

```bash
ssh user@<vm-ip>
cd /opt/climebot
cp .env.example .env
nano .env   # Fill in production values
```

Key differences for production `.env`:

```dotenv
AZURE_REDIRECT_URI=https://climebot.example.com
BACKEND_CORS_ORIGINS=https://climebot.example.com
BACKEND_URL=http://backend:8000  # internal Docker network
QDRANT_HOST=qdrant
```

### 5c. Configure nginx SSL

1. Place your TLS certificate and key in `nginx/ssl/`:
   - `nginx/ssl/fullchain.pem`
   - `nginx/ssl/privkey.pem`
2. Uncomment the HTTPS server block in [nginx/nginx.conf](nginx/nginx.conf) and set your domain name.

### 5d. Start production stack

```bash
make up-prod
```

This uses the `docker-compose.prod.yml` overlay which:
- Removes direct port exposure for backend/frontend/qdrant
- Adds the nginx reverse proxy on ports 80 and 443

### 5e. Register the VM’s URL as a Redirect URI in Entra ID

In the Azure Portal, add `https://climebot.example.com` as an additional Redirect URI for your app registration.

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
make scrape        Run Scrapy spider
make ingest        Ingest data/ into Qdrant (idempotent)
make reingest      Drop collections and ingest from scratch
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

### Trigger re-ingestion via API

```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  | python -m json.tool
```

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
| `QDRANT_HOST` | `qdrant` | `qdrant` (Docker) or `localhost` (local dev) |
| `QDRANT_PORT` | `6333` | Qdrant gRPC/REST port |
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
