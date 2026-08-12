from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., max_length=10_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_images: bool = True


class SearchResult(BaseModel):
    id: str
    score: float
    content: str
    source: str
    result_type: str  # "text" | "image"
    image_base64: str | None = None
    metadata: dict[str, Any] = {}


class SearchResponse(BaseModel):
    results: list[SearchResult]


class IngestRequest(BaseModel):
    data_dir: str | None = None


class IngestResponse(BaseModel):
    message: str
    documents_processed: int
    images_processed: int
