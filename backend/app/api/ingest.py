from fastapi import APIRouter, BackgroundTasks, Depends
from loguru import logger

from app.auth.azure_auth import verify_token
from app.core.ingestion import ingest_documents
from app.models.schemas import IngestRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse)
async def trigger_ingest(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    _user: dict = Depends(verify_token),
) -> IngestResponse:
    background_tasks.add_task(_run_ingest, request.data_dir)
    return IngestResponse(
        message="Ingestion started in background",
        documents_processed=0,
        images_processed=0,
    )


def _run_ingest(data_dir: str | None) -> None:
    try:
        docs, imgs = ingest_documents(data_dir)
        logger.info("Background ingest complete: {} docs, {} images", docs, imgs)
    except Exception as exc:
        logger.error("Background ingest failed: {}", exc)
