import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from qdrant_client.models import ScoredPoint

from app.auth.azure_auth import verify_token
from app.config import get_settings
from app.core.embeddings import embed_sparse, embed_texts
from app.core.index_state import read_index_state
from app.core.llm import stream_chat_completion
from app.core.source_files import pdf_source_metadata
from app.core.spreadsheets import search_spreadsheet_rows, spreadsheet_db_path
from app.core.vectorstore import search_async
from app.models.schemas import ChatRequest

router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()


@router.post("")
async def chat(request: ChatRequest, _user: dict = Depends(verify_token)) -> StreamingResponse:
    last_user_msg = next((m.content for m in reversed(request.messages) if m.role == "user"), "")

    query_embedding = embed_texts([last_user_msg])[0]
    sparse_indices, sparse_values = embed_sparse([last_user_msg])[0]

    text_hits = await search_async(
        settings.qdrant_collection_text, query_embedding, sparse_indices, sparse_values, top_k=5
    )
    image_hits = await search_async(
        settings.qdrant_collection_images, query_embedding, sparse_indices, sparse_values, top_k=3
    )
    spreadsheet_rows = {}
    for hit in text_hits:
        payload = hit.payload or {}
        if payload.get("type") != "spreadsheet":
            continue
        spreadsheet_rows[str(hit.id)] = search_spreadsheet_rows(
            spreadsheet_db_path(settings.data_dir),
            payload.get("content_hash", ""),
            payload.get("sheet", ""),
            last_user_msg,
        )

    if not text_hits and not image_hits:
        return StreamingResponse(
            _empty_store_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    context_parts = []
    for hit in text_hits:
        content = (hit.payload or {}).get("content", "")
        rows = spreadsheet_rows.get(str(hit.id), [])
        if rows:
            row_text = "\n".join(
                f"Row {row.row_number}: "
                + " | ".join(f"{key}: {value}" for key, value in row.values.items())
                for row in rows
            )
            content = f"{content}\nMatching worksheet rows:\n{row_text}"
        context_parts.append(content)
    context_parts += [(hit.payload or {}).get("content", "") for hit in image_hits]
    context = "\n\n---\n\n".join(filter(None, context_parts))

    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    return StreamingResponse(
        _sse_stream(
            messages,
            context,
            text_hits,
            image_hits,
            spreadsheet_rows,
            (read_index_state(settings.data_dir) or {}).get("indexed_at"),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_stream(
    messages: list[dict],
    context: str,
    text_hits: list[ScoredPoint],
    image_hits: list[ScoredPoint],
    spreadsheet_rows: dict[str, list],
    indexed_at: str | None,
) -> AsyncGenerator[bytes, None]:
    try:
        async for kind, text in stream_chat_completion(messages, context):
            if kind == "think":
                yield f"data: {json.dumps({'think': text})}\n\n".encode()
            else:
                yield f"data: {json.dumps({'token': text})}\n\n".encode()
    except Exception as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n".encode()

    text_sources = []
    for hit in text_hits:
        payload = hit.payload or {}
        source = {
            "source": payload.get("source", "unknown"),
            "score": round(hit.score, 3),
            "snippet": payload.get("content", ""),
            "page": payload.get("page"),
            "type": payload.get("type", "text"),
            "indexed_at": indexed_at,
        } | pdf_source_metadata(settings.data_dir, payload.get("source_path", ""))
        if source["type"] == "spreadsheet":
            source["sheet"] = payload.get("sheet", "")
            source["records"] = [
                {"row_number": row.row_number, **row.values}
                for row in spreadsheet_rows.get(str(hit.id), [])
            ]
        text_sources.append(source)

    image_sources = []
    for hit in image_hits:
        payload = hit.payload or {}
        image_sources.append(
            {
                "source": payload.get("source", "unknown"),
                "score": round(hit.score, 3),
                "snippet": payload.get("content", ""),
                "page": payload.get("page"),
                "type": "image",
                "image_base64": payload.get("image_base64", ""),
                "image_ext": payload.get("image_ext", "png"),
                "indexed_at": indexed_at,
            }
            | pdf_source_metadata(settings.data_dir, payload.get("source_path", ""))
        )
    sources = sorted(text_sources + image_sources, key=lambda r: r["score"], reverse=True)
    yield f"data: {json.dumps({'sources': sources})}\n\n".encode()
    yield b"data: [DONE]\n\n"


async def _empty_store_response() -> AsyncGenerator[bytes, None]:
    msg = "The knowledge base is empty. Get in touch with the administrator."
    yield f"data: {json.dumps({'token': msg})}\n\n".encode()
    yield f"data: {json.dumps({'sources': []})}\n\n".encode()
    yield b"data: [DONE]\n\n"
