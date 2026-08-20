from fastapi import APIRouter, Depends

from app.auth.azure_auth import verify_token
from app.config import get_settings
from app.core.embeddings import embed_sparse, embed_texts
from app.core.vectorstore import search_async
from app.models.schemas import SearchRequest, SearchResponse, SearchResult

router = APIRouter(prefix="/search", tags=["search"])
settings = get_settings()


@router.post("", response_model=SearchResponse)
async def search(request: SearchRequest, _user: dict = Depends(verify_token)) -> SearchResponse:
    query_embedding = embed_texts([request.query])[0]
    sparse_indices, sparse_values = embed_sparse([request.query])[0]
    results: list[SearchResult] = []

    text_hits = await search_async(
        settings.qdrant_collection_text,
        query_embedding,
        sparse_indices,
        sparse_values,
        top_k=request.top_k,
    )
    for hit in text_hits:
        results.append(
            SearchResult(
                id=str(hit.id),
                score=hit.score,
                content=hit.payload.get("content", ""),
                source=hit.payload.get("source", ""),
                result_type="text",
                metadata={k: v for k, v in hit.payload.items() if k != "content"},
            )
        )

    if request.include_images:
        image_hits = await search_async(
            settings.qdrant_collection_images,
            query_embedding,
            sparse_indices,
            sparse_values,
            top_k=3,
        )
        for hit in image_hits:
            results.append(
                SearchResult(
                    id=str(hit.id),
                    score=hit.score,
                    content=hit.payload.get("content", ""),
                    source=hit.payload.get("source", ""),
                    result_type="image",
                    image_base64=hit.payload.get("image_base64"),
                    metadata={
                        k: v for k, v in hit.payload.items() if k not in ("content", "image_base64")
                    },
                )
            )

    results.sort(key=lambda r: r.score, reverse=True)
    return SearchResponse(results=results)
