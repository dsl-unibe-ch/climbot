import asyncio
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from loguru import logger

from app.auth.azure_auth import verify_token
from app.models.schemas import IngestRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


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


async def _run_ingest(data_dir: str | None) -> None:
    """Spawn ingestion as a subprocess so it gets its own Qdrant storage lock."""
    env = {**os.environ}
    if data_dir:
        env["DATA_DIR"] = data_dir

    loop = asyncio.get_event_loop()
    try:
        # run_in_executor avoids asyncio subprocess incompatibility on Windows
        result: subprocess.CompletedProcess = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, "-m", "app.core.ingestion"],
                cwd=_BACKEND_DIR,
                env=env,
                capture_output=True,
                text=True,
            ),
        )
        if result.returncode != 0:
            logger.error("Ingest subprocess failed: {}", result.stderr)
        else:
            logger.info("Ingest subprocess complete: {}", result.stdout)
    except Exception as exc:
        logger.error("Ingest subprocess error: {}", exc)
