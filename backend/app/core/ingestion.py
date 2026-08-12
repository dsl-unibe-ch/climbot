"""Document ingestion pipeline: parse → chunk → embed → index into Qdrant."""

from __future__ import annotations

import argparse
import base64
import hashlib
import uuid
from pathlib import Path

import fitz
from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger
from qdrant_client.models import PointStruct
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.core.embeddings import embed_image, embed_texts
from app.core.vectorstore import drop_and_recreate_collections, ensure_collections, upsert_points

settings = get_settings()

# Fixed namespace for deterministic UUIDs; same content → same point ID
_ID_NS = uuid.UUID("a3b4c5d6-e7f8-5a9b-8c0d-1e2f3a4b5c6d")
_TEXT_EXTS = frozenset({".pdf", ".docx", ".txt", ".md"})
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_MIN_IMAGE_PX = 100  # skip tiny images (icons, decorations)


def ingest_documents(data_dir: str | None = None) -> tuple[int, int]:
    root = Path(data_dir or settings.data_dir)
    ensure_collections()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    docs_ok = images_ok = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()

        if ext in _TEXT_EXTS:
            d, i = _ingest_text_file(path, root, ext, splitter)
            docs_ok += d
            images_ok += i
        elif ext in _IMAGE_EXTS:
            images_ok += _ingest_standalone_image(path, root)

    logger.info("Ingestion finished: {} docs, {} images", docs_ok, images_ok)
    return docs_ok, images_ok


def _ingest_text_file(
    path: Path,
    root: Path,
    ext: str,
    splitter: RecursiveCharacterTextSplitter,
) -> tuple[int, int]:
    try:
        text = _read_text(path, ext)
        if not text.strip():
            return 0, 0

        chunks = splitter.split_text(text)
        if not chunks:
            return 0, 0

        embeddings = _embed_with_retry(chunks)
        rel = str(path.relative_to(root))
        points = [
            PointStruct(
                id=str(uuid.uuid5(_ID_NS, f"{rel}:text:{i}:{chunk[:120]}")),
                vector=emb,
                payload={"content": chunk, "source": rel, "type": "text"},
            )
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True))
        ]
        upsert_points(settings.qdrant_collection_text, points)
        logger.info("Indexed {}: {} text chunks", rel, len(chunks))

        images_ok = _ingest_pdf_images(path, root) if ext == ".pdf" else 0
        return 1, images_ok

    except Exception as exc:
        logger.error("Failed to ingest {}: {}", path, exc)
        return 0, 0


def _ingest_pdf_images(pdf_path: Path, root: Path) -> int:
    ok = 0
    doc = fitz.open(str(pdf_path))
    rel = str(pdf_path.relative_to(root))

    for page_num, page in enumerate(doc):
        for img_ref in page.get_images(full=True):
            try:
                xref = img_ref[0]
                pix = fitz.Pixmap(doc, xref)

                if pix.width < _MIN_IMAGE_PX or pix.height < _MIN_IMAGE_PX:
                    continue
                if pix.n - pix.alpha > 3:  # convert CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)

                img_bytes = pix.tobytes("png")
                emb, description = embed_image(img_bytes)
                img_id = str(uuid.uuid5(_ID_NS, f"{rel}:img:{page_num}:{xref}"))

                upsert_points(
                    settings.qdrant_collection_images,
                    [
                        PointStruct(
                            id=img_id,
                            vector=emb,
                            payload={
                                "content": description,
                                "source": rel,
                                "image_base64": base64.b64encode(img_bytes).decode(),
                                "image_ext": "png",
                                "page": page_num,
                                "type": "image",
                            },
                        )
                    ],
                )
                ok += 1
            except Exception as exc:
                logger.warning("Skipped image in {} page {}: {}", rel, page_num, exc)
    return ok


def _ingest_standalone_image(path: Path, root: Path) -> int:
    try:
        img_bytes = path.read_bytes()
        emb, description = embed_image(img_bytes)
        rel = str(path.relative_to(root))
        img_hash = hashlib.sha1(img_bytes).hexdigest()[:16]  # noqa: S324
        upsert_points(
            settings.qdrant_collection_images,
            [
                PointStruct(
                    id=str(uuid.uuid5(_ID_NS, f"{rel}:{img_hash}")),
                    vector=emb,
                    payload={
                        "content": description,
                        "source": rel,
                        "image_base64": base64.b64encode(img_bytes).decode(),
                        "image_ext": path.suffix.lstrip("."),
                        "type": "image",
                    },
                )
            ],
        )
        logger.info("Indexed image: {}", rel)
        return 1
    except Exception as exc:
        logger.error("Failed to ingest image {}: {}", path, exc)
        return 0


def _read_text(path: Path, ext: str) -> str:
    if ext == ".pdf":
        doc = fitz.open(str(path))
        return "\n".join(page.get_text() for page in doc)
    if ext == ".docx":
        return "\n".join(p.text for p in DocxDocument(str(path)).paragraphs)
    return path.read_text(encoding="utf-8", errors="ignore")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _embed_with_retry(texts: list[str]) -> list[list[float]]:
    return embed_texts(texts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fresh", action="store_true", help="Drop and recreate Qdrant collections before ingesting"
    )
    args = parser.parse_args()

    if args.fresh:
        logger.info("--fresh: dropping and recreating Qdrant collections")
        drop_and_recreate_collections()

    docs, imgs = ingest_documents()
    logger.info("Done: {} documents, {} images", docs, imgs)
