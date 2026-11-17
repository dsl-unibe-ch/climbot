"""Document ingestion pipeline: parse → chunk → embed → index into Qdrant."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger
from PIL import Image, ImageOps
from qdrant_client.models import PointStruct, SparseVector
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.core.embeddings import embed_image, embed_sparse, embed_texts
from app.core.index_state import write_index_state
from app.core.spreadsheets import (
    SpreadsheetSheet,
    SpreadsheetWorkbook,
    rebuild_spreadsheet_index,
    spreadsheet_db_path,
)
from app.core.vectorstore import drop_and_recreate_collections, ensure_collections, upsert_points

settings = get_settings()

# Fixed namespace for deterministic UUIDs; same content → same point ID
_ID_NS = uuid.UUID("a3b4c5d6-e7f8-5a9b-8c0d-1e2f3a4b5c6d")
_TEXT_EXTS = frozenset({".pdf", ".docx", ".txt", ".md"})
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_SPREADSHEET_EXTS = frozenset({".xlsx"})
_SUPPORTED_EXTS = _TEXT_EXTS | _IMAGE_EXTS | _SPREADSHEET_EXTS
_EXPORT_PREFIX = "OneDrive_1_"
_MIN_IMAGE_PX = 100  # skip tiny images (icons, decorations)
# Base64 adds one third; leave room for vectors and metadata in a 16 MiB upsert.
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass
class _FileCandidate:
    path: Path
    content_hash: str
    aliases: tuple[Path, ...]


def _prepare_image(img_bytes: bytes, image_ext: str) -> tuple[bytes, str]:
    """Keep small images unchanged; compress oversized images for vision and storage."""
    if len(img_bytes) <= _MAX_IMAGE_BYTES:
        return img_bytes, image_ext

    with Image.open(BytesIO(img_bytes)) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
    background = Image.new("RGB", image.size, "white")
    background.paste(image, mask=image.getchannel("A"))
    image = background

    while True:
        for quality in (90, 80, 70):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            compressed = output.getvalue()
            if len(compressed) <= _MAX_IMAGE_BYTES:
                logger.info(
                    "Compressed image: {} -> {} bytes (JPEG, {}x{}, quality {})",
                    len(img_bytes),
                    len(compressed),
                    image.width,
                    image.height,
                    quality,
                )
                return compressed, "jpg"

        size = (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4))
        if size == image.size:
            raise ValueError(f"Cannot compress image below {_MAX_IMAGE_BYTES} bytes")
        image = image.resize(size, Image.Resampling.LANCZOS)


def ingest_documents(data_dir: str | None = None) -> tuple[int, int]:
    root = Path(data_dir or settings.data_dir)
    ensure_collections()
    candidates, export = _build_manifest(root)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    docs_ok = images_ok = 0
    workbooks: list[SpreadsheetWorkbook] = []

    for candidate in candidates:
        path = candidate.path
        ext = path.suffix.lower()
        aliases = tuple(str(alias.relative_to(root)) for alias in candidate.aliases)

        if ext in _TEXT_EXTS:
            d, i = _ingest_text_file(
                path,
                root,
                ext,
                splitter,
                content_hash=candidate.content_hash,
                aliases=aliases,
            )
            docs_ok += d
            images_ok += i
        elif ext in _IMAGE_EXTS:
            images_ok += _ingest_standalone_image(
                path,
                root,
                content_hash=candidate.content_hash,
                aliases=aliases,
            )
        elif ext in _SPREADSHEET_EXTS:
            workbooks.append(
                SpreadsheetWorkbook(
                    path=path,
                    content_hash=candidate.content_hash,
                    source_path=str(path.relative_to(root)),
                    aliases=aliases,
                )
            )

    sheets = rebuild_spreadsheet_index(spreadsheet_db_path(root), workbooks)
    _index_spreadsheet_sheets(sheets)
    write_index_state(root, export, docs_ok, images_ok, len(sheets))

    logger.info(
        "Ingestion finished: {} docs, {} images, {} worksheets",
        docs_ok,
        images_ok,
        len(sheets),
    )
    return docs_ok, images_ok


def _build_manifest(root: Path) -> tuple[list[_FileCandidate], str | None]:
    paths, export = _discover_supported_files(root)
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        by_hash[_sha256_file(path)].append(path)

    candidates = []
    for content_hash, duplicates in by_hash.items():
        aliases = tuple(sorted(duplicates))
        canonical = min(aliases, key=_canonical_path_rank)
        candidates.append(
            _FileCandidate(path=canonical, content_hash=content_hash, aliases=aliases)
        )

    candidates = _prefer_equivalent_pdfs(candidates)
    candidates.sort(key=lambda candidate: str(candidate.path))
    duplicate_count = len(paths) - len(candidates)
    logger.info(
        "Manifest: {} canonical files, {} duplicates removed, export {}",
        len(candidates),
        duplicate_count,
        export or "none",
    )
    return candidates, export


def _discover_supported_files(root: Path) -> tuple[list[Path], str | None]:
    sharepoint = root / "sharepoint"
    if not sharepoint.is_dir() and root.name == "sharepoint":
        sharepoint = root

    selected_export: Path | None = None
    export_dirs: list[Path] = []
    if sharepoint.is_dir():
        export_dirs = [
            path
            for path in sharepoint.iterdir()
            if path.is_dir() and path.name.startswith(_EXPORT_PREFIX)
        ]
        if export_dirs:
            dated_exports = []
            for path in export_dirs:
                try:
                    export_date = datetime.strptime(
                        path.name.removeprefix(_EXPORT_PREFIX), "%m-%d-%Y"
                    ).date()
                except ValueError:
                    logger.warning("Ignoring export with unsupported date format: {}", path.name)
                    continue
                dated_exports.append((export_date, path.name, path))
            if not dated_exports:
                raise ValueError(
                    f"No {_EXPORT_PREFIX} directory has the expected MM-DD-YYYY date suffix"
                )
            latest_export = max(dated_exports)[2]
            selected_export = latest_export
            logger.info("Selected latest SharePoint export: {}", latest_export.name)

    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SUPPORTED_EXTS:
            continue
        containing_export = next(
            (export for export in export_dirs if path.is_relative_to(export)), None
        )
        if containing_export is not None and containing_export != selected_export:
            continue
        files.append(path)
    export_name = selected_export.name if selected_export is not None else None
    return files, export_name


def _prefer_equivalent_pdfs(candidates: list[_FileCandidate]) -> list[_FileCandidate]:
    by_stem: dict[tuple[Path, str], list[_FileCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.path.suffix.lower() in {".pdf", ".docx"}:
            by_stem[(candidate.path.parent, candidate.path.stem.casefold())].append(candidate)

    discarded: set[Path] = set()
    for group in by_stem.values():
        pdf = next(
            (item for item in group if item.path.suffix.lower() == ".pdf"),
            None,
        )
        docx = next(
            (item for item in group if item.path.suffix.lower() == ".docx"),
            None,
        )
        if pdf is None or docx is None:
            continue
        try:
            pdf_text = _read_text(pdf.path, ".pdf")
            docx_text = _read_text(docx.path, ".docx")
        except Exception as exc:
            logger.warning("Could not compare {} and {}: {}", pdf.path, docx.path, exc)
            continue

        if not pdf_text.strip():
            docx.aliases = tuple(sorted(set(docx.aliases + pdf.aliases)))
            discarded.add(pdf.path)
            logger.info("Using DOCX because paired PDF has no text: {}", docx.path.name)
        elif _text_similarity(pdf_text, docx_text) >= 0.9:
            pdf.aliases = tuple(sorted(set(pdf.aliases + docx.aliases)))
            discarded.add(docx.path)
            logger.info("Using PDF instead of equivalent DOCX: {}", pdf.path.name)

    return [candidate for candidate in candidates if candidate.path not in discarded]


def _text_similarity(left: str, right: str) -> float:
    left_words = Counter(re.findall(r"\w+", left.casefold()))
    right_words = Counter(re.findall(r"\w+", right.casefold()))
    if not left_words or not right_words:
        return 0
    intersection = sum((left_words & right_words).values())
    union = sum((left_words | right_words).values())
    return intersection / union


def _canonical_path_rank(path: Path) -> tuple[bool, str]:
    return "scraped_docs" in path.parts, str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ingest_text_file(
    path: Path,
    root: Path,
    ext: str,
    splitter: RecursiveCharacterTextSplitter,
    content_hash: str | None = None,
    aliases: tuple[str, ...] = (),
) -> tuple[int, int]:
    try:
        text = _read_text(path, ext)
        if not text.strip():
            return 0, 0

        chunks = splitter.split_text(text)
        if not chunks:
            return 0, 0

        embeddings = _embed_with_retry(chunks)
        sparse_vecs = embed_sparse(chunks)
        rel = str(path.relative_to(root))
        document_hash = content_hash or _sha256_file(path)
        source_aliases = aliases or (rel,)
        points = [
            PointStruct(
                id=str(
                    uuid.uuid5(
                        _ID_NS,
                        f"{document_hash}:text:{i}:{hashlib.sha256(chunk.encode()).hexdigest()}",
                    )
                ),
                vector={
                    "dense": emb,
                    "sparse": SparseVector(indices=sp[0], values=sp[1]),
                },
                payload={
                    "content": chunk,
                    "source": path.name,
                    "source_path": rel,
                    "source_aliases": source_aliases,
                    "content_hash": document_hash,
                    "type": "text",
                },
            )
            for i, (chunk, emb, sp) in enumerate(zip(chunks, embeddings, sparse_vecs, strict=True))
        ]
        upsert_points(settings.qdrant_collection_text, points)
        logger.info("Indexed {}: {} text chunks", rel, len(chunks))

        images_ok = (
            _ingest_pdf_images(
                path,
                root,
                content_hash=document_hash,
                aliases=source_aliases,
            )
            if ext == ".pdf"
            else 0
        )
        return 1, images_ok

    except Exception as exc:
        logger.error("Failed to ingest {}: {}", path, exc)
        return 0, 0


def _ingest_pdf_images(
    pdf_path: Path,
    root: Path,
    content_hash: str | None = None,
    aliases: tuple[str, ...] = (),
) -> int:
    ok = 0
    seen_image_hashes: set[str] = set()
    doc = fitz.open(str(pdf_path))
    rel = str(pdf_path.relative_to(root))
    document_hash = content_hash or _sha256_file(pdf_path)
    source_aliases = aliases or (rel,)

    for page_num in range(doc.page_count):
        page = doc.load_page(page_num)
        for img_ref in page.get_images(full=True):
            try:
                xref = img_ref[0]
                pix = fitz.Pixmap(doc, xref)

                if pix.width < _MIN_IMAGE_PX or pix.height < _MIN_IMAGE_PX:
                    continue
                if pix.n - pix.alpha > 3:  # convert CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)

                original_bytes = pix.tobytes("png")
                image_hash = hashlib.sha256(original_bytes).hexdigest()
                if image_hash in seen_image_hashes:
                    logger.debug("Skipped duplicate image in {} page {}", rel, page_num)
                    continue
                seen_image_hashes.add(image_hash)

                img_bytes, image_ext = _prepare_image(original_bytes, "png")
                emb, description = embed_image(img_bytes)
                sp = embed_sparse([description])[0]
                img_id = str(uuid.uuid5(_ID_NS, f"{document_hash}:img:{page_num}:{xref}"))

                upsert_points(
                    settings.qdrant_collection_images,
                    [
                        PointStruct(
                            id=img_id,
                            vector={
                                "dense": emb,
                                "sparse": SparseVector(indices=sp[0], values=sp[1]),
                            },
                            payload={
                                "content": description,
                                "source": pdf_path.name,
                                "source_path": rel,
                                "source_aliases": source_aliases,
                                "image_base64": base64.b64encode(img_bytes).decode(),
                                "image_ext": image_ext,
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


def _ingest_standalone_image(
    path: Path,
    root: Path,
    content_hash: str | None = None,
    aliases: tuple[str, ...] = (),
) -> int:
    try:
        img_bytes = path.read_bytes()
        img_hash = content_hash or hashlib.sha256(img_bytes).hexdigest()
        img_bytes, image_ext = _prepare_image(img_bytes, path.suffix.lstrip("."))
        emb, description = embed_image(img_bytes)
        sp = embed_sparse([description])[0]
        rel = str(path.relative_to(root))
        source_aliases = aliases or (rel,)
        upsert_points(
            settings.qdrant_collection_images,
            [
                PointStruct(
                    id=str(uuid.uuid5(_ID_NS, f"{img_hash}:image")),
                    vector={
                        "dense": emb,
                        "sparse": SparseVector(indices=sp[0], values=sp[1]),
                    },
                    payload={
                        "content": description,
                        "source": path.name,
                        "source_path": rel,
                        "source_aliases": source_aliases,
                        "content_hash": img_hash,
                        "image_base64": base64.b64encode(img_bytes).decode(),
                        "image_ext": image_ext,
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


def _index_spreadsheet_sheets(sheets: list[SpreadsheetSheet]) -> None:
    if not sheets:
        return
    contents = [sheet.content for sheet in sheets]
    embeddings = _embed_with_retry(contents)
    sparse_vecs = embed_sparse(contents)
    points = [
        PointStruct(
            id=str(uuid.uuid5(_ID_NS, f"{sheet.workbook_id}:sheet:{sheet.name}")),
            vector={
                "dense": embedding,
                "sparse": SparseVector(indices=sparse[0], values=sparse[1]),
            },
            payload={
                "content": sheet.content,
                "source": sheet.source,
                "source_path": sheet.source_path,
                "source_aliases": sheet.aliases,
                "content_hash": sheet.workbook_id,
                "sheet": sheet.name,
                "type": "spreadsheet",
            },
        )
        for sheet, embedding, sparse in zip(sheets, embeddings, sparse_vecs, strict=True)
    ]
    upsert_points(settings.qdrant_collection_text, points)
    logger.info("Indexed {} spreadsheet worksheets", len(points))


def _read_text(path: Path, ext: str) -> str:
    if ext == ".pdf":
        doc = fitz.open(str(path))
        try:
            return "\n".join(
                doc.load_page(index).get_textpage().extractText() for index in range(doc.page_count)
            )
        finally:
            doc.close()
    if ext == ".docx":
        return "\n".join(p.text for p in DocxDocument(str(path)).paragraphs)
    return path.read_text(encoding="utf-8", errors="ignore")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
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
