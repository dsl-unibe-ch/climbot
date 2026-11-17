from fastapi import APIRouter

from app.config import get_settings
from app.core.index_state import read_index_state

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "index": read_index_state(settings.data_dir)}
