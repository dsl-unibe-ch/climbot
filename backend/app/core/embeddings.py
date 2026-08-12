import base64

from openai import OpenAI

from app.config import get_settings

settings = get_settings()

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    response = _openai().embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]


def embed_image(image_bytes: bytes) -> tuple[list[float], str]:
    """Describe an image via a vision model, then embed the description text."""
    vision_model = settings.openai_vision_model or settings.openai_model
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
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        max_tokens=600,
    )
    description = vision_resp.choices[0].message.content or ""
    embedding = embed_texts([description])[0]
    return embedding, description
