from pathlib import Path

import streamlit as st
from auth.azure_oauth import AzureAuth
from components.chat_ui import render_chat_history, render_sources, stream_from_backend
from components.image_viewer import render_image_results

_LOGO = Path(__file__).parent / "assets" / "images" / "logo_name_white.svg"

st.set_page_config(
    page_title="NCCR CLIM+ Bot",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Fixed footer injected once at module level
st.markdown(
    "<style>"
    "#dsl-footer{position:fixed;bottom:0.75rem;right:1rem;font-size:0.75rem;"
    "color:#4a6a8a;z-index:9999;pointer-events:auto;}"
    "#dsl-footer a{color:#4a6a8a;text-decoration:none;}"
    "#dsl-footer a:hover{color:#8ba3c0;text-decoration:underline;}"
    "</style>"
    '<div id="dsl-footer">Powered by <a href="https://www.dsl.unibe.ch/" target="_blank">DSL</a></div>',
    unsafe_allow_html=True,
)


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
        render_chat_history()

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
                        render_sources(sources, key="live")

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
