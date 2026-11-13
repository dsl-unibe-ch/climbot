from fastapi import APIRouter, Depends

from app.auth.azure_auth import verify_token
from app.config import get_settings
from app.core.embeddings import embed_sparse, embed_texts
from app.core.index_state import read_index_state
from app.core.source_files import pdf_source_metadata
from app.core.spreadsheets import search_spreadsheet_rows, spreadsheet_db_path
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
    indexed_at = (read_index_state(settings.data_dir) or {}).get("indexed_at")
    for hit in text_hits:
        payload = hit.payload or {}
        metadata = {k: v for k, v in payload.items() if k != "content"}
        metadata.update(pdf_source_metadata(settings.data_dir, payload.get("source_path", "")))
        result_type = payload.get("type", "text")
        if result_type == "spreadsheet":
            metadata["records"] = [
                {"row_number": row.row_number, **row.values}
                for row in search_spreadsheet_rows(
                    spreadsheet_db_path(settings.data_dir),
                    payload.get("content_hash", ""),
                    payload.get("sheet", ""),
                    request.query,
                )
            ]
        metadata["indexed_at"] = indexed_at
        results.append(
            SearchResult(
                id=str(hit.id),
                score=hit.score,
                content=payload.get("content", ""),
                source=payload.get("source", ""),
                result_type=result_type,
                metadata=metadata,
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
            payload = hit.payload or {}
            metadata = {k: v for k, v in payload.items() if k not in ("content", "image_base64")}
            metadata.update(pdf_source_metadata(settings.data_dir, payload.get("source_path", "")))
            metadata["indexed_at"] = indexed_at
            results.append(
                SearchResult(
                    id=str(hit.id),
                    score=hit.score,
                    content=payload.get("content", ""),
                    source=payload.get("source", ""),
                    result_type="image",
                    image_base64=payload.get("image_base64"),
                    metadata=metadata,
                )
            )

    results.sort(key=lambda r: r.score, reverse=True)
    return SearchResponse(results=results)
