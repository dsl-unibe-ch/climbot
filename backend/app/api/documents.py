from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.auth.azure_auth import verify_token
from app.config import get_settings
from app.core.source_files import extract_pdf_page, resolve_pdf

router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()


@router.get("/pdf")
async def pdf(
    path: str,
    page: int | None = Query(default=None, ge=0),
    _user: dict = Depends(verify_token),
) -> Response:
    try:
        pdf_path = resolve_pdf(settings.data_dir, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PDF not found") from exc

    if page is None:
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
            content_disposition_type="inline",
        )

    try:
        page_pdf = extract_pdf_page(pdf_path, page)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="PDF page not found") from exc
    return Response(
        page_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{pdf_path.stem}-page-{page + 1}.pdf"',
            "X-Source-PDF-Size": str(pdf_path.stat().st_size),
        },
    )
