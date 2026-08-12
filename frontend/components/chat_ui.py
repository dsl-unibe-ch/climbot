import json
import os
from pathlib import Path

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")


def render_chat_history() -> None:
    """Render only the conversation history; chat input is handled at page level."""
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("think"):
                with st.expander("🧠 Reasoning", expanded=False):
                    st.markdown(msg["think"])


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
