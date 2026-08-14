from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client.models import Distance, PointStruct, ScoredPoint, VectorParams

from app.config import get_settings

settings = get_settings()

_sync_client: QdrantClient | None = None
_async_client: AsyncQdrantClient | None = None


def _sync() -> QdrantClient:
    global _sync_client
    if _sync_client is None:
        _sync_client = QdrantClient(
            path=settings.qdrant_path,
            api_key=settings.qdrant_api_key or None,
        )
    return _sync_client


def _async() -> AsyncQdrantClient:
    global _async_client
    if _async_client is None:
        _async_client = AsyncQdrantClient(
            path=settings.qdrant_path,
            api_key=settings.qdrant_api_key or None,
        )
    return _async_client


def ensure_collections() -> None:
    """Sync variant — for the CLI ingestion process only."""
    client = _sync()
    for name in (settings.qdrant_collection_text, settings.qdrant_collection_images):
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
            )


async def ensure_collections_async() -> None:
    """Async variant — for the FastAPI lifespan; keeps the server on a single async client."""
    client = _async()
    for name in (settings.qdrant_collection_text, settings.qdrant_collection_images):
        if not await client.collection_exists(name):
            await client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
            )


def drop_and_recreate_collections() -> None:
    client = _sync()
    for name in (settings.qdrant_collection_text, settings.qdrant_collection_images):
        if client.collection_exists(name):
            client.delete_collection(name)
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
        )


def upsert_points(collection: str, points: list[PointStruct]) -> None:
    _sync().upsert(collection_name=collection, points=points, wait=True)


async def search_async(
    collection: str, query_vector: list[float], top_k: int = 5
) -> list[ScoredPoint]:
    return await _async().search(
        collection_name=collection,
        query_vector=query_vector,
        limit=top_k,
        with_payload=True,
    )
