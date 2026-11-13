import base64
import os
from pathlib import Path

import httpx
import streamlit as st
from dotenv import load_dotenv

from components.pdf_viewer import render_pdf_source

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")


def render_image_results(query: str, include_images: bool, access_token: str) -> None:
    st.subheader("🔍 Document Search")

    with st.spinner("Searching…"):
        try:
            resp = httpx.post(
                f"{_BACKEND}/search",
                json={"query": query, "top_k": 6, "include_images": include_images},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as exc:
            st.error(f"Search failed: {exc}")
            return

    if not results:
        st.info("No results found.")
        return

    for result in results:
        kind = result["result_type"].upper()
        label = f"[{kind}] {result['source']}  —  score: {result['score']:.3f}"
        with st.expander(label):
            source = {"type": result["result_type"], "source": result["source"]} | result.get(
                "metadata", {}
            )
            if result.get("image_base64"):
                source["image_base64"] = result["image_base64"]
            is_pdf = render_pdf_source(source, access_token, result["id"])
            if not is_pdf and result["result_type"] == "image" and result.get("image_base64"):
                img_bytes = base64.b64decode(result["image_base64"])
                st.image(img_bytes, width="stretch")
                if result.get("content"):
                    st.caption(result["content"])
            elif result["result_type"] == "spreadsheet":
                st.caption(result["content"])
                records = result.get("metadata", {}).get("records", [])
                if records:
                    st.dataframe(records, use_container_width=True, hide_index=True)
                else:
                    st.info("No matching worksheet rows found.")
            else:
                st.write(result["content"])
                src = result.get("metadata", {}).get("source") or result.get("source")
                if src:
                    st.caption(f"Source: {src}")
