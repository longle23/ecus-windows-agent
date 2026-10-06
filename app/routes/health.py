from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import get_settings
from app.job_queue import get_job_queue
from app.rpa.factory import build_rpa_backend

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    settings = get_settings()
    backend = build_rpa_backend(settings)
    queue = get_job_queue(settings).snapshot()
    return {
        "ok": True,
        "version": __version__,
        "backend": backend.name,
        "busy": queue["busy"],
        "holder": queue["holder"],
        "queueDepth": queue["queueDepth"],
        "api_key_configured": bool(settings.api_key and settings.api_key != "CHANGE_ME"),
        "ecus_exe_path": settings.ecus_exe_path,
    }
