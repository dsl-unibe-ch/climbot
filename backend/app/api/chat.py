import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from qdrant_client.models import ScoredPoint

from app.auth.azure_auth import verify_token
from app.config import get_settings
from app.core.embeddings import embed_texts
from app.core.llm import stream_chat_completion
from app.core.vectorstore import search_async
from app.models.schemas import ChatRequest

router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()


@router.post("")
async def chat(request: ChatRequest, _user: dict = Depends(verify_token)) -> StreamingResponse:
    last_user_msg = next((m.content for m in reversed(request.messages) if m.role == "user"), "")

    query_embedding = embed_texts([last_user_msg])[0]

    text_hits = await search_async(settings.qdrant_collection_text, query_embedding, top_k=5)
    image_hits = await search_async(settings.qdrant_collection_images, query_embedding, top_k=2)

    context_parts = [h.payload.get("content", "") for h in text_hits]
    context_parts += [h.payload.get("content", "") for h in image_hits]
    context = "\n\n---\n\n".join(filter(None, context_parts))

    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    return StreamingResponse(
        _sse_stream(messages, context, text_hits, image_hits),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_stream(
    messages: list[dict],
    context: str,
    text_hits: list[ScoredPoint],
    image_hits: list[ScoredPoint],
) -> AsyncGenerator[bytes, None]:
    try:
        async for kind, text in stream_chat_completion(messages, context):
            if kind == "think":
                yield f"data: {json.dumps({'think': text})}\n\n".encode()
            else:
                yield f"data: {json.dumps({'token': text})}\n\n".encode()
    except Exception as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n".encode()

    sources = [
        {
            "source": h.payload.get("source", "unknown"),
            "score": round(h.score, 3),
            "snippet": h.payload.get("content", "")[:300],
            "page": h.payload.get("page"),
            "type": "text",
        }
        for h in text_hits
    ] + [
        {
            "source": h.payload.get("source", "unknown"),
            "score": round(h.score, 3),
            "snippet": h.payload.get("description", "")[:300],
            "page": h.payload.get("page"),
            "type": "image",
            "image_base64": h.payload.get("image_base64", ""),
            "image_ext": h.payload.get("image_ext", "png"),
        }
        for h in image_hits
    ]
    yield f"data: {json.dumps({'sources': sources})}\n\n".encode()
    yield b"data: [DONE]\n\n"
