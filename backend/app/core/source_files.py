from pathlib import Path

import fitz
from loguru import logger


def resolve_pdf(data_dir: str | Path, source_path: str) -> Path:
    root = Path(data_dir).resolve()
    relative = Path(source_path)
    if relative.is_absolute() or relative.suffix.lower() != ".pdf":
        raise ValueError("Source must be a relative PDF path")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Source path is outside the data directory")
    if not resolved.is_file():
        raise FileNotFoundError(source_path)
    return resolved


def pdf_source_metadata(data_dir: str | Path, source_path: str) -> dict[str, int | str]:
    if not source_path.lower().endswith(".pdf"):
        return {}
    try:
        path = resolve_pdf(data_dir, source_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("PDF source unavailable for {}: {}", source_path, exc)
        return {}
    return {"source_path": source_path, "pdf_size": path.stat().st_size}


def extract_pdf_page(path: Path, page: int) -> bytes:
    source = fitz.open(path)
    output = fitz.open()
    try:
        if page >= source.page_count:
            raise IndexError(page)
        output.insert_pdf(source, from_page=page, to_page=page)
        return output.tobytes()
    finally:
        output.close()
        source.close()
