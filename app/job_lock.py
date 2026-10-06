from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class JobBusyError(RuntimeError):
    """Raised when another ECUS job is already running."""


class JobLock:
    """Legacy lock. Job admission is the FIFO queue in app.job_queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None
        self._started_at: float | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def holder(self) -> str | None:
        return self._holder

    @contextmanager
    def acquire(self, job_id: str, timeout_sec: float) -> Iterator[None]:
        got = self._lock.acquire(timeout=timeout_sec)
        if not got:
            raise JobBusyError(
                f"ECUS agent is busy with job {self._holder or 'unknown'}; try again later."
            )
        self._holder = job_id
        self._started_at = time.time()
        try:
            yield
        finally:
            self._holder = None
            self._started_at = None
            self._lock.release()


job_lock = JobLock()
