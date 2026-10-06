from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.routes import health, jobs


_LOG_NAMES = {
    "app.main": "agent",
    "app.routes.jobs": "job",
    "app.rpa.ecus_pywinauto": "rpa",
    "app.rpa.ui_helpers": "ui",
    "app.rpa.sta_worker": "sta",
    "app.completed_jobs": "done",
    "app.job_queue": "queue",
}


class _AgentFormatter(logging.Formatter):
    """One readable line: time, level, short source, message."""

    def format(self, record: logging.LogRecord) -> str:
        name = _LOG_NAMES.get(record.name, record.name.rsplit(".", 1)[-1])
        ts = self.formatTime(record, "%H:%M:%S")
        line = f"{ts}  {record.levelname:<5}  {name:<5}  {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    if root.handlers:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)
    fmt = _AgentFormatter()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        settings.log_dir / "ecus-agent.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="ECUS Windows Agent",
        version=__version__,
        description="Receives Men-Chuen Gửi ECUS jobs from n8n and drives ECUS WinForms.",
    )
    app.include_router(health.router)
    app.include_router(jobs.router)

    @app.on_event("startup")
    def _startup() -> None:
        logging.getLogger(__name__).info(
            "ECUS agent v%s starting backend=%s host=%s:%s",
            __version__,
            settings.rpa_backend,
            settings.host,
            settings.port,
        )
        from app.job_queue import get_job_queue

        get_job_queue(settings).start()
        if settings.rpa_backend == "pywinauto":
            from app.rpa.sta_worker import sta_worker

            sta_worker.start()

    return app


app = create_app()
