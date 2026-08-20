from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client.models import (
    Distance,
    Fusion,
    FusionQuery,
    PointStruct,
    Prefetch,
    ScoredPoint,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.config import get_settings

settings = get_settings()

_sync_client: QdrantClient | None = None
_async_client: AsyncQdrantClient | None = None


def _sync() -> QdrantClient:
    global _sync_client
    if _sync_client is None:
        _sync_client = QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
        )
    return _sync_client


def _async() -> AsyncQdrantClient:
    global _async_client
    if _async_client is None:
        _async_client = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
        )
    return _async_client


def _is_legacy_schema(info) -> bool:
    """True when the collection still uses the old unnamed single-vector config."""
    return not isinstance(info.config.params.vectors, dict)


def ensure_collections() -> None:
    """Sync variant — for the CLI ingestion process only."""
    client = _sync()
    for name in (settings.qdrant_collection_text, settings.qdrant_collection_images):
        if client.collection_exists(name):
            if not _is_legacy_schema(client.get_collection(name)):
                continue
            client.delete_collection(name)
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(size=settings.embedding_dim, distance=Distance.COSINE)
            },
            sparse_vectors_config={"sparse": SparseVectorParams()},
        )


async def ensure_collections_async() -> None:
    """Async variant — for the FastAPI lifespan; keeps the server on a single async client."""
    client = _async()
    for name in (settings.qdrant_collection_text, settings.qdrant_collection_images):
        if await client.collection_exists(name):
            if not _is_legacy_schema(await client.get_collection(name)):
                continue
            await client.delete_collection(name)
        await client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(size=settings.embedding_dim, distance=Distance.COSINE)
            },
            sparse_vectors_config={"sparse": SparseVectorParams()},
        )


def drop_and_recreate_collections() -> None:
    client = _sync()
    for name in (settings.qdrant_collection_text, settings.qdrant_collection_images):
        if client.collection_exists(name):
            client.delete_collection(name)
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(size=settings.embedding_dim, distance=Distance.COSINE)
            },
            sparse_vectors_config={"sparse": SparseVectorParams()},
        )


def upsert_points(collection: str, points: list[PointStruct]) -> None:
    _sync().upsert(collection_name=collection, points=points, wait=True)


async def search_async(
    collection: str,
    query_vector: list[float],
    sparse_indices: list[int],
    sparse_values: list[float],
    top_k: int = 5,
) -> list[ScoredPoint]:
    response = await _async().query_points(
        collection_name=collection,
        prefetch=[
            Prefetch(query=query_vector, using="dense", limit=top_k * 4),
            Prefetch(
                query=SparseVector(indices=sparse_indices, values=sparse_values),
                using="sparse",
                limit=top_k * 4,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
    )
    return response.points
