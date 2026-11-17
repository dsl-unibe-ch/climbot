import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def index_state_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / ".index" / "state.json"


def read_index_state(data_dir: str | Path) -> dict[str, Any] | None:
    path = index_state_path(data_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_index_state(
    data_dir: str | Path,
    export: str | None,
    documents: int,
    images: int,
    worksheets: int,
) -> dict[str, Any]:
    state = {
        "indexed_at": datetime.now(UTC).isoformat(),
        "export": export,
        "documents": documents,
        "images": images,
        "worksheets": worksheets,
    }
    path = index_state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary_path.replace(path)
    return state
