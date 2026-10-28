import base64
import re
from io import BytesIO

from fastembed.sparse.bm25 import Bm25
from openai import OpenAI
from PIL import Image

from app.config import get_settings

settings = get_settings()

_client: OpenAI | None = None
_bm25_model: Bm25 | None = None

_VISION_REFUSAL = re.compile(
    r"^(?:(?:i(?:'m| am) sorry|sorry)[,.:!]?\s*)?"
    r"(?:i\s+(?:am\s+unable|(?:can't|cannot|can not|won't|will not|must not))"
    r"\s+(?:to\s+)?(?:help|assist|describe|analy[sz]e|interpret|process|view|see|access)"
    r"\b|i(?:'m| am) unable to\b)",
    re.IGNORECASE,
)


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
    return _client


def _bm25() -> Bm25:
    global _bm25_model
    if _bm25_model is None:
        _bm25_model = Bm25("Qdrant/bm25")
    return _bm25_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    response = _openai().embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]


def embed_sparse(texts: list[str]) -> list[tuple[list[int], list[float]]]:
    """Return BM25 sparse vectors as (indices, values) tuples."""
    return [(sp.indices.tolist(), sp.values.tolist()) for sp in _bm25().embed(texts)]


def embed_image(image_bytes: bytes) -> tuple[list[float], str]:
    """Describe an image via a vision model, then embed the description text."""
    vision_model = settings.openai_vision_model or settings.openai_model
    with Image.open(BytesIO(image_bytes)) as image:
        if image.format is None:
            raise ValueError("Cannot determine image format for vision request")
        mime_type = Image.MIME[image.format]
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    vision_resp = _openai().chat.completions.create(
        model=vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this image in detail, focusing on any climate-related "
                            "data, charts, graphs, maps, or scientific information it contains."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        temperature=0,
        max_tokens=600,
    )
    if not vision_resp.choices:
        raise ValueError(f"Vision model {vision_model} returned no choices")
    choice = vision_resp.choices[0]
    if choice.message.refusal:
        raise ValueError(f"Vision model {vision_model} refused the image: {choice.message.refusal}")
    if choice.finish_reason != "stop":
        raise ValueError(
            f"Vision model {vision_model} returned an incomplete or blocked response "
            f"(finish_reason={choice.finish_reason})"
        )
    description = (choice.message.content or "").strip()
    if not description:
        raise ValueError(f"Vision model {vision_model} returned an empty image description")
    if _VISION_REFUSAL.match(description.replace("\u2019", "'")):
        raise ValueError(f"Vision model {vision_model} returned a refusal: {description}")
    embedding = embed_texts([description])[0]
    return embedding, description
