from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()

_client: AsyncOpenAI | None = None


def _async_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
    return _client


_SYSTEM_PROMPT = """\
You are ClimeBot, an expert climate change research assistant.
Answer questions accurately and concisely using the provided document context.
Cite source document names when referencing specific data or findings.
Acknowledge uncertainty when the context does not contain enough information.
"""


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


async def stream_chat_completion(
    messages: list[dict], context: str
) -> AsyncGenerator[tuple[str, str], None]:
    system_content = _SYSTEM_PROMPT
    if context.strip():
        system_content += f"\n\n--- Document Context ---\n{context}"

    full_messages = [{"role": "system", "content": system_content}] + messages

    stream = await _async_openai().chat.completions.create(
        model=settings.openai_model,
        messages=full_messages,
        stream=True,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
    )

    buf = ""
    in_think = False
    # keep a rolling buffer long enough to detect split open/close tags
    _guard = max(len(_THINK_OPEN), len(_THINK_CLOSE))

    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta is None:
            continue
        buf += delta

        while True:
            if in_think:
                pos = buf.find(_THINK_CLOSE)
                if pos >= 0:
                    if pos > 0:
                        yield ("think", buf[:pos])
                    buf = buf[pos + len(_THINK_CLOSE) :]
                    in_think = False
                elif len(buf) > _guard:
                    yield ("think", buf[:-_guard])
                    buf = buf[-_guard:]
                    break
                else:
                    break
            else:
                pos = buf.find(_THINK_OPEN)
                if pos >= 0:
                    if pos > 0:
                        yield ("token", buf[:pos])
                    buf = buf[pos + len(_THINK_OPEN) :]
                    in_think = True
                elif len(buf) > _guard:
                    yield ("token", buf[:-_guard])
                    buf = buf[-_guard:]
                    break
                else:
                    break

    if buf:
        yield ("think", buf) if in_think else ("token", buf)
