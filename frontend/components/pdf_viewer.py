import os

import httpx
import streamlit as st

_BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
_MAX_INLINE_PDF_BYTES = 20 * 1024 * 1024


def render_pdf_source(source: dict, token: str, key: str) -> bool:
    source_path = source.get("source_path", "")
    if not source_path.lower().endswith(".pdf"):
        return False

    page = source.get("page")
    if page is not None and st.button("📄 Preview source page", key=f"_pdf_page_{key}"):
        if pdf_bytes := _fetch_pdf(source_path, token, page=page):
            st.pdf(
                pdf_bytes,
                height=650,
                key=f"_pdf_page_viewer_{key}",
                alt=f"Page {page + 1} of {source.get('source', 'source PDF')}",
            )

    size = int(source.get("pdf_size") or 0)
    size_label = f" ({size / 1024 / 1024:.1f} MB)" if size else ""
    if size and size > _MAX_INLINE_PDF_BYTES:
        if st.button(f"⬇️ Prepare PDF download{size_label}", key=f"_pdf_download_{key}"):
            if pdf_bytes := _fetch_pdf(source_path, token):
                st.download_button(
                    "Download PDF",
                    pdf_bytes,
                    file_name=source.get("source", "source.pdf"),
                    mime="application/pdf",
                    key=f"_pdf_download_ready_{key}",
                )
    elif st.button(f"📖 View full PDF{size_label}", key=f"_pdf_full_{key}"):
        if pdf_bytes := _fetch_pdf(source_path, token):
            st.pdf(
                pdf_bytes,
                height=750,
                key=f"_pdf_full_viewer_{key}",
                alt=source.get("source", "Source PDF"),
            )

    if source.get("type") == "image" and source.get("image_base64"):
        with st.expander("Show extracted figure", expanded=False):
            import base64

            st.image(base64.b64decode(source["image_base64"]), width="stretch")
    return True


def _fetch_pdf(source_path: str, token: str, page: int | None = None) -> bytes | None:
    params: dict[str, str | int] = {"path": source_path}
    if page is not None:
        params["page"] = page
    try:
        response = httpx.get(
            f"{_BACKEND}/documents/pdf",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        st.error(f"Could not load PDF: {exc}")
        return None
