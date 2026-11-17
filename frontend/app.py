import os
import tomllib
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import streamlit as st
from auth.azure_oauth import AzureAuth
from components.chat_ui import render_chat_history, render_sources, stream_from_backend
from components.image_viewer import render_image_results

_LOGO = Path(__file__).parent / "assets" / "images" / "logo_name_white.svg"
_BACKEND = os.environ.get("BACKEND_URL")
_SUPPORT_EMAIL = os.environ["SUPPORT_EMAIL"]
_SUPPORT_URL = f"mailto:{quote(_SUPPORT_EMAIL, safe='@+._-')}"


def _get_version() -> str:
    # Prefer env var (set by Docker via docker-compose); fall back to pyproject.toml for local dev
    if v := os.environ.get("APP_VERSION"):
        return v
    toml_path = Path(__file__).parents[1] / "pyproject.toml"
    if toml_path.exists():
        with toml_path.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    return "unknown"


@st.cache_data(ttl=60)
def _get_index_status() -> tuple[dict | None, str | None]:
    try:
        response = httpx.get(f"{_BACKEND}/health", timeout=3.0)
        response.raise_for_status()
        return response.json().get("index"), None
    except httpx.HTTPError as exc:
        return None, str(exc)


def _format_indexed_at(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M UTC")


st.set_page_config(
    page_title="NCCR CLIM+ Bot",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Fixed footer injected once at module level
st.html(
    "<style>"
    "#dsl-footer{position:fixed;bottom:0.75rem;right:1rem;font-size:0.75rem;"
    "color:#4a6a8a;z-index:9999;pointer-events:auto;}"
    "#dsl-footer a{color:#4a6a8a;text-decoration:none;}"
    "#dsl-footer a:hover{color:#8ba3c0;text-decoration:underline;}"
    "#dsl-footer .support-link{border:1px solid #4a6a8a;border-radius:0.35rem;"
    "padding:0.2rem 0.45rem;}"
    "</style>"
    f'<div id="dsl-footer">v{_get_version()} &nbsp;·&nbsp; Created by '
    '<a href="https://www.dsl.unibe.ch/" target="_blank">DSL</a>'
    f' &nbsp;·&nbsp; <a class="support-link" href="{_SUPPORT_URL}">Request Access/Support</a></div>',
)

st.html("""
<style>
#disclaimer-footer {
    position: fixed;
    bottom: 0.75rem;
    left: 50%;
    transform: translateX(-50%);
    width: 100%;
    text-align: center;
    font-size: 0.75rem;
    color: #4a6a8a;
    z-index: 9999;
}
</style>
<div id="disclaimer-footer">
    ClimeBot uses AI language models and can make mistakes. Always check important information against the cited sources. Powered by open weight models at <a href="https://gpustack.unibe.ch/#/login" target="_blank"> GPUStack UniBE</a>
</div>
""")


def main() -> None:
    auth = AzureAuth()

    if not auth.is_authenticated():
        auth.handle_auth_flow()
        return

    user = auth.get_user_info()
    display_name = user.get("name") or user.get("preferred_username", "User")

    with st.sidebar:
        st.image(str(_LOGO), use_container_width=True)
        st.caption(f"Signed in as **{display_name}**")
        index_status, index_error = _get_index_status()
        if index_status and index_status.get("indexed_at"):
            st.caption(
                f"Knowledge base updated: **{_format_indexed_at(index_status['indexed_at'])}**"
            )
        elif index_error:
            st.caption("Knowledge base update time unavailable.")
        else:
            st.caption("Knowledge base has not been indexed yet.")
        st.divider()

        st.subheader("🔍 Document Search")
        st.caption("Use the sidebar search to find documents and images directly.")
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

    col_chat, col_search = st.columns([3, 2])

    with col_chat:
        st.subheader("💬 Chat with ClimeBot")
        render_chat_history(auth.get_access_token())

    with col_search:
        search = st.session_state.get("_search")

        if search:
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
                if think_text or sources:
                    with st.expander("🧠 Reasoning", expanded=False):
                        if think_text:
                            st.markdown(think_text)
                        render_sources(sources, auth.get_access_token(), key="live")

        st.session_state["messages"].append(
            {
                "role": "assistant",
                "content": response,
                "think": think_text,
                "sources": sources,
            }
        )
        st.rerun()


if __name__ == "__main__":
    main()
