import base64
import json
import os
from pathlib import Path

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")


def render_sources(sources: list, key: str = "0") -> None:
    if not sources:
        return
    _state_key = f"_show_all_src_{key}"
    show_all = st.session_state.get(_state_key, False)
    visible = sources if show_all else sources[:3]
    with st.expander(f"📎 {len(sources)} source(s)", expanded=False):
        for src in visible:
            icon = "🖼️" if src.get("type") == "image" else "📄"
            label = f"{icon} {src['source']}"
            if src.get("page") is not None:
                label += f"  ·  p. {src['page']}"
            label += f"  ·  score {src['score']}"
            with st.expander(label, expanded=False):
                if src.get("type") == "image" and src.get("image_base64"):
                    img_bytes = base64.b64decode(src["image_base64"])
                    st.image(img_bytes, use_container_width=True)
                snippet = src.get("snippet", "").strip()
                if snippet:
                    st.caption(snippet)
                elif src.get("type") != "image":
                    st.markdown("_No preview available._")
        if len(sources) > 3:
            if show_all:
                if st.button("▲ Show less", key=f"_btn_src_{key}"):
                    st.session_state[_state_key] = False
                    st.rerun()
            else:
                if st.button(f"▼ {len(sources) - 3} more…", key=f"_btn_src_{key}"):
                    st.session_state[_state_key] = True
                    st.rerun()


def render_chat_history() -> None:
    """Render only the conversation history; chat input is handled at page level."""
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for i, msg in enumerate(st.session_state["messages"]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("think") or msg.get("sources"):
                with st.expander("🧠 Reasoning and📎Source(s)", expanded=False):
                    if msg.get("think"):
                        st.markdown(msg["think"])
                    if msg.get("sources"):
                        render_sources(msg["sources"], key=str(i))


def stream_from_backend(messages: list[dict], token: str) -> tuple[str, str, list]:
    area = st.empty()
    area.markdown("_⏳ Searching documents and thinking…_")

    full_text = ""
    think_text = ""
    sources: list = []

    try:
        with httpx.stream(
            "POST",
            f"{_BACKEND}/chat",
            json={"messages": messages},
            headers={"Authorization": f"Bearer {token}"},
            timeout=300.0,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if "error" in data:
                    area.error(data["error"])
                    return data["error"], "", []
                if "think" in data:
                    think_text += data["think"]
                if "token" in data and data["token"]:
                    full_text += data["token"]
                    area.markdown(full_text + "▌")
                if "sources" in data:
                    sources = data["sources"]

    except httpx.HTTPStatusError as exc:
        area.error(f"Backend error ({exc.response.status_code})")
        return "", "", []
    except Exception as exc:
        area.error(f"Connection error: {exc}")
        return "", "", []

    if not full_text:
        area.warning("No response received — the model may still be processing. Try again.")
        return "", think_text, sources

    area.markdown(full_text)
    return full_text, think_text, sources
