from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api import chat, health, search
from app.config import get_settings
from app.core.vectorstore import ensure_collections_async

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info("ClimeBot backend starting — ensuring Qdrant collections…")
    await ensure_collections_async()
    logger.info("Ready.")
    yield
    logger.info("ClimeBot backend shutting down.")


app = FastAPI(title="ClimeBot API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.backend_cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(search.router)
