import base64
import os

import httpx
import streamlit as st
from auth.azure_oauth import AzureAuth
from components.chat_ui import render_chat_history, stream_from_backend
from components.image_viewer import render_image_results

st.set_page_config(
    page_title="ClimeBot",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    auth = AzureAuth()

    if not auth.is_authenticated():
        auth.handle_auth_flow()
        return

    user = auth.get_user_info()
    display_name = user.get("name") or user.get("preferred_username", "User")

    with st.sidebar:
        st.title("🌍 ClimeBot")
        st.caption(f"Signed in as **{display_name}**")
        st.divider()

        st.subheader("🔍 Document Search")
        search_query = st.text_input("Search query", placeholder="e.g. sea level rise 2024")
        include_images = st.checkbox("Include images", value=True)

        if st.button("Search", use_container_width=True, disabled=not search_query):
            st.session_state["_search"] = {
                "query": search_query,
                "include_images": include_images,
            }

        st.divider()

        if st.button("🚪  Logout", use_container_width=True):
            auth.logout()
            st.rerun()

        with st.expander("⚙️  Admin"):
            if st.button("Re-ingest documents", use_container_width=True):
                _trigger_ingest(auth.get_access_token())

    col_chat, col_search = st.columns([3, 2])

    with col_chat:
        st.subheader("💬 Chat with ClimeBot")
        render_chat_history()

    with col_search:
        chat_sources = st.session_state.get("_chat_sources")
        search = st.session_state.get("_search")

        if not chat_sources and not search:
            st.info(
                "Sources used to answer your question will appear here. Use the sidebar search to find documents and images directly."
            )

        if chat_sources:
            st.subheader("📎 Retrieved Sources")
            for src in chat_sources:
                icon = "🖼️" if src.get("type") == "image" else "📄"
                label = f"{icon} {src['source']}"
                if src.get("page") is not None:
                    label += f"  ·  p. {src['page']}"
                label += f"  ·  score {src['score']}"
                with st.expander(label, expanded=False):
                    if src.get("type") == "image" and src.get("image_base64"):
                        img_bytes = base64.b64decode(src["image_base64"])
                        st.image(img_bytes, use_column_width=True)
                    snippet = src.get("snippet", "").strip()
                    if snippet:
                        st.caption(snippet)
                    elif src.get("type") != "image":
                        st.markdown("_No preview available._")

        if search:
            st.divider()
            render_image_results(
                search["query"],
                search["include_images"],
                auth.get_access_token(),
            )

    # Page-level input — Streamlit sticks this to the bottom of the viewport
    if prompt := st.chat_input("Ask about climate change…"):
        if "messages" not in st.session_state:
            st.session_state["messages"] = []

        st.session_state["messages"].append({"role": "user", "content": prompt})

        with col_chat:
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                response, think_text, sources = stream_from_backend(
                    st.session_state["messages"], auth.get_access_token()
                )
                if think_text:
                    with st.expander("🧠 Reasoning", expanded=False):
                        st.markdown(think_text)

        st.session_state["messages"].append(
            {
                "role": "assistant",
                "content": response,
                "think": think_text,
            }
        )
        st.session_state["_chat_sources"] = sources
        st.rerun()


def _trigger_ingest(token: str) -> None:
    backend = os.environ.get("BACKEND_URL", "http://localhost:8000")
    try:
        resp = httpx.post(
            f"{backend}/ingest",
            json={},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        st.success("Ingestion started in background.")
    except Exception as exc:
        st.error(f"Failed to start ingestion: {exc}")


if __name__ == "__main__":
    main()
