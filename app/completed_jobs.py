from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_MAX_ENTRIES = 500


def _path(settings: Settings) -> Path:
    return Path(settings.log_dir) / "completed_jobs.json"


def _read(settings: Settings) -> dict:
    path = _path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read completed jobs file: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write(settings: Settings, data: dict) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(data) > _MAX_ENTRIES:
        ranked = sorted(
            data.items(),
            key=lambda item: str((item[1] or {}).get("finishedAt") or ""),
        )
        data = dict(ranked[-_MAX_ENTRIES:])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def succeeded_info(settings: Settings, job_id: str) -> dict | None:
    if not job_id:
        return None
    with _lock:
        record = _read(settings).get(job_id)
    return record if isinstance(record, dict) else None


def already_succeeded(settings: Settings, job_id: str) -> bool:
    return succeeded_info(settings, job_id) is not None


def mark_succeeded(settings: Settings, job_id: str, so_to_khai: str = "") -> None:
    if not job_id:
        return
    with _lock:
        data = _read(settings)
        data[job_id] = {
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "so_to_khai": so_to_khai or "",
        }
        _write(settings, data)
