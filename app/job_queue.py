"""FIFO queue so ECUS runs one declaration at a time without dropping the rest.

POST /jobs used to wait on a lock for 30 seconds and then answer 409. Callers
that arrived together, or while another user was still running, lost the job.
This queue keeps every accepted job on disk and runs them in arrival order.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.completed_jobs import already_succeeded, mark_succeeded, succeeded_info
from app.config import Settings, get_settings
from app.models import EcusJobRequest, EcusJobResponse
from app.rpa import RpaResult

logger = logging.getLogger(__name__)

_MAX_FINISHED = 500
_ACTIVE = {"queued", "running"}
_INTERRUPTED = (
    "Agent restarted while this job was running on ECUS. "
    "Check the declaration before resubmitting."
)

Runner = Callable[[EcusJobRequest], RpaResult]


@dataclass
class QueueDecision:
    """What POST /jobs should do with this submission."""

    code: str  # created | existing | succeeded | full
    response: EcusJobResponse


class JobQueue:
    """One worker, arrival order, queue file survives a process restart."""

    def __init__(self, settings: Settings, runner: Runner | None = None) -> None:
        self.settings = settings
        self._runner = runner or self._run_rpa
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._order: list[str] = []
        self._records: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._started = False
        self._start_lock = threading.Lock()
        self._load()

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._thread = threading.Thread(
                target=self._loop,
                name="ecus-job-queue",
                daemon=True,
            )
            self._thread.start()
            self._started = True

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def submit(self, job: EcusJobRequest) -> QueueDecision:
        if already_succeeded(self.settings, job.jobId):
            return QueueDecision("succeeded", self._completed_response(job))

        created_position = 0
        created_depth = 0
        with self._cv:
            record = self._records.get(job.jobId)
            if record and record["status"] in _ACTIVE:
                return QueueDecision("existing", self._to_response_unlocked(record))
            if record and record["status"] == "success":
                return QueueDecision("succeeded", self._to_response_unlocked(record))

            waiting = self._queued_count_unlocked()
            if waiting >= self.settings.job_queue_max:
                return QueueDecision("full", self._to_response_unlocked(record) if record else _empty_response(job))

            if record and record["status"] == "failed":
                self._order = [job_id for job_id in self._order if job_id != job.jobId]

            now = _now()
            self._order.append(job.jobId)
            self._records[job.jobId] = {
                "jobId": job.jobId,
                "status": "queued",
                "enqueuedAt": now,
                "startedAt": None,
                "finishedAt": None,
                "request": job.model_dump(mode="json"),
                "result": None,
            }
            self._persist_unlocked()
            created_position = self._position_unlocked(job.jobId)
            created_depth = self._queued_count_unlocked()
            self._cv.notify()

        logger.info(
            "JOB QUEUE  %s   position=%s   waiting=%s",
            job.jobId,
            created_position,
            created_depth,
        )
        with self._cv:
            current = self._records.get(job.jobId)
            response = self._to_response_unlocked(current) if current else _empty_response(job)
        return QueueDecision("created", response)

    def get(self, job_id: str) -> EcusJobResponse | None:
        with self._cv:
            record = self._records.get(job_id)
            if record is not None:
                return self._to_response_unlocked(record)
        info = succeeded_info(self.settings, job_id)
        if info is None:
            return None
        return EcusJobResponse(
            success=True,
            message="ECUS Ghi completed",
            so_to_khai=str(info.get("so_to_khai") or ""),
            jobId=job_id,
            status="success",
        )

    def snapshot(self) -> dict[str, Any]:
        with self._cv:
            holder = next(
                (
                    job_id
                    for job_id in self._order
                    if self._records.get(job_id, {}).get("status") == "running"
                ),
                None,
            )
            return {
                "busy": holder is not None,
                "holder": holder,
                "queueDepth": self._queued_count_unlocked(),
            }

    def _loop(self) -> None:
        logger.info("ECUS job queue worker ready")
        while not self._stop.is_set():
            claimed: tuple[str, dict[str, Any]] | None = None
            with self._cv:
                while not self._has_queued_unlocked() and not self._stop.is_set():
                    self._cv.wait(timeout=0.5)
                if self._stop.is_set():
                    return
                claimed = self._claim_next_unlocked()
            if claimed is None:
                continue
            job_id, request = claimed
            try:
                self._execute(job_id, request)
            except Exception:
                logger.exception("job queue worker failed id=%s", job_id)
                self._finish(
                    job_id,
                    request,
                    RpaResult(
                        success=False,
                        message="RPA error: job queue worker failed",
                        hang_hoa_requested=_requested_from_payload(request),
                        warnings=["job queue worker failed"],
                    ),
                )

    def _execute(self, job_id: str, request: dict[str, Any]) -> None:
        job = EcusJobRequest.model_validate(request)
        hang = len(job.danh_sach_hang.hang_hoa)
        logger.info("")
        logger.info(
            "JOB START  %s   %s/%s   hang=%s   backend=%s",
            job_id,
            job.jobIndex,
            job.jobTotal,
            hang,
            self.settings.rpa_backend,
        )
        try:
            result = self._runner(job)
            if not isinstance(result, RpaResult):
                raise TypeError(f"runner returned {type(result).__name__}")
        except TimeoutError:
            logger.error("job timed out id=%s after %ss", job_id, self.settings.job_timeout_sec)
            result = RpaResult(
                success=False,
                message=f"RPA timed out after {self.settings.job_timeout_sec}s",
                hang_hoa_requested=hang,
                hang_hoa_filled=0,
                warnings=[f"RPA timed out after {self.settings.job_timeout_sec}s"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s failed", job_id)
            result = RpaResult(
                success=False,
                message=f"RPA error: {exc}",
                hang_hoa_requested=hang,
                hang_hoa_filled=0,
                warnings=[str(exc)],
            )
        self._finish(job_id, request, result)

    def _finish(self, job_id: str, request: dict[str, Any], result: RpaResult) -> None:
        status_name = "success" if result.success else "failed"
        send_id = str(request.get("sendId") or "")
        if result.success:
            mark_succeeded(self.settings, job_id, result.so_to_khai)
        payload = _result_payload(
            job_id,
            send_id,
            result,
            status_name,
            backend=self.settings.rpa_backend,
        )
        with self._cv:
            record = self._records.get(job_id)
            if record is None:
                return
            record["status"] = status_name
            record["finishedAt"] = _now()
            record["result"] = payload
            self._persist_unlocked()
        if result.success:
            logger.info(
                "JOB OK     %s   hang %s/%s   so_to_khai=%s",
                job_id,
                result.hang_hoa_filled,
                result.hang_hoa_requested,
                result.so_to_khai or "-",
            )
        else:
            reason = "; ".join(result.warnings) or result.message
            logger.info(
                "JOB FAIL   %s   hang %s/%s   %s",
                job_id,
                result.hang_hoa_filled,
                result.hang_hoa_requested,
                reason,
            )

    def _run_rpa(self, job: EcusJobRequest) -> RpaResult:
        from app.rpa.factory import build_rpa_backend
        from app.rpa.sta_worker import run_on_sta_thread

        backend = build_rpa_backend(self.settings)
        return run_on_sta_thread(
            lambda: backend.register_declaration(job),
            timeout_sec=self.settings.job_timeout_sec,
        )

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("could not read job queue file: %s", exc)
            return
        jobs = raw.get("jobs") if isinstance(raw, dict) else None
        order = raw.get("order") if isinstance(raw, dict) else None
        if not isinstance(jobs, dict):
            logger.warning("job queue file has no jobs object")
            return
        self._records = {job_id: rec for job_id, rec in jobs.items() if isinstance(rec, dict)}
        seen = [job_id for job_id in order if isinstance(job_id, str) and job_id in self._records] if isinstance(order, list) else []
        for job_id in self._records:
            if job_id not in seen:
                seen.append(job_id)
        self._order = seen
        if self._reconcile_unlocked():
            self._persist_unlocked()

    def _reconcile_unlocked(self) -> bool:
        """A job left in `running` did not finish inside this process."""
        changed = False
        for job_id in list(self._order):
            record = self._records.get(job_id)
            if not record or record.get("status") != "running":
                continue
            request = record.get("request") if isinstance(record.get("request"), dict) else {}
            send_id = str(request.get("sendId") or "")
            info = succeeded_info(self.settings, job_id)
            if info is not None:
                result = RpaResult(
                    success=True,
                    message="ECUS Ghi completed",
                    so_to_khai=str(info.get("so_to_khai") or ""),
                    hang_hoa_requested=_requested_from_payload(request),
                    hang_hoa_filled=_requested_from_payload(request),
                )
                record["status"] = "success"
                record["finishedAt"] = str(info.get("finishedAt") or _now())
                record["result"] = _result_payload(
                    job_id,
                    send_id,
                    result,
                    "success",
                    backend=self.settings.rpa_backend,
                )
            else:
                result = RpaResult(
                    success=False,
                    message=_INTERRUPTED,
                    hang_hoa_requested=_requested_from_payload(request),
                    hang_hoa_filled=0,
                    warnings=[_INTERRUPTED],
                )
                record["status"] = "failed"
                record["finishedAt"] = _now()
                record["result"] = _result_payload(
                    job_id,
                    send_id,
                    result,
                    "failed",
                    backend=self.settings.rpa_backend,
                )
                logger.warning("job %s was running when the agent stopped", job_id)
            changed = True
        return changed

    def _claim_next_unlocked(self) -> tuple[str, dict[str, Any]] | None:
        for job_id in self._order:
            record = self._records.get(job_id)
            if not record or record.get("status") != "queued":
                continue
            record["status"] = "running"
            record["startedAt"] = _now()
            self._persist_unlocked()
            request = record.get("request") if isinstance(record.get("request"), dict) else {}
            return job_id, dict(request)
        return None

    def _has_queued_unlocked(self) -> bool:
        return self._queued_count_unlocked() > 0

    def _queued_count_unlocked(self) -> int:
        return sum(1 for record in self._records.values() if record.get("status") == "queued")

    def _position_unlocked(self, job_id: str) -> int:
        active = [
            item_id
            for item_id in self._order
            if self._records.get(item_id, {}).get("status") in _ACTIVE
        ]
        try:
            return active.index(job_id) + 1
        except ValueError:
            return 0

    def _to_response_unlocked(self, record: dict[str, Any]) -> EcusJobResponse:
        job_id = str(record.get("jobId") or "")
        status_name = str(record.get("status") or "")
        request = record.get("request") if isinstance(record.get("request"), dict) else {}
        send_id = str(request.get("sendId") or "")
        requested = _requested_from_payload(request)
        position = self._position_unlocked(job_id) if status_name in _ACTIVE else 0
        if status_name == "queued":
            return EcusJobResponse(
                success=False,
                message=f"Queued at position {position}",
                jobId=job_id,
                sendId=send_id,
                status="queued",
                hang_hoa_requested=requested,
                position=position,
            )
        if status_name == "running":
            return EcusJobResponse(
                success=False,
                message=f"Running on ECUS (position {position})",
                jobId=job_id,
                sendId=send_id,
                status="running",
                hang_hoa_requested=requested,
                position=position,
                backend=self.settings.rpa_backend,
            )
        stored = record.get("result") if isinstance(record.get("result"), dict) else {}
        filled = int(stored.get("hang_hoa_filled") or 0)
        return EcusJobResponse(
            success=bool(stored.get("success")),
            message=str(stored.get("message") or ""),
            so_to_khai=str(stored.get("so_to_khai") or ""),
            jobId=job_id,
            sendId=send_id or str(stored.get("sendId") or ""),
            status=str(stored.get("status") or status_name),
            screenshot=stored.get("screenshot"),
            hang_hoa_requested=int(stored.get("hang_hoa_requested") or 0),
            hang_hoa_filled=filled,
            hang_hoa_count=int(stored.get("hang_hoa_count") or filled),
            warnings=list(stored.get("warnings") or []),
            backend=str(stored.get("backend") or ""),
            position=0,
        )

    def _completed_response(self, job: EcusJobRequest) -> EcusJobResponse:
        info = succeeded_info(self.settings, job.jobId) or {}
        return EcusJobResponse(
            success=True,
            message="job already completed successfully",
            so_to_khai=str(info.get("so_to_khai") or ""),
            jobId=job.jobId,
            sendId=job.sendId,
            status="success",
        )

    def _persist_unlocked(self) -> None:
        self._trim_unlocked()
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"order": self._order, "jobs": self._records}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _trim_unlocked(self) -> None:
        finished = [
            job_id
            for job_id in self._order
            if self._records.get(job_id, {}).get("status") in {"success", "failed"}
        ]
        overflow = len(finished) - _MAX_FINISHED
        if overflow <= 0:
            return
        drop = set(finished[:overflow])
        self._order = [job_id for job_id in self._order if job_id not in drop]
        for job_id in drop:
            self._records.pop(job_id, None)

    def _path(self):
        return self.settings.log_dir / "job_queue.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _requested_from_payload(request: dict[str, Any]) -> int:
    hang = request.get("danh_sach_hang")
    if not isinstance(hang, dict):
        return 0
    rows = hang.get("hang_hoa")
    return len(rows) if isinstance(rows, list) else 0


def _empty_response(job: EcusJobRequest) -> EcusJobResponse:
    return EcusJobResponse(
        success=False,
        message="ECUS job queue is full",
        jobId=job.jobId,
        sendId=job.sendId,
        status="failed",
    )


def _result_payload(
    job_id: str,
    send_id: str,
    result: RpaResult,
    status_name: str,
    *,
    backend: str,
) -> dict[str, Any]:
    return {
        "success": result.success,
        "message": result.message,
        "so_to_khai": result.so_to_khai,
        "jobId": job_id,
        "sendId": send_id,
        "status": status_name,
        "screenshot": result.screenshot,
        "hang_hoa_requested": result.hang_hoa_requested,
        "hang_hoa_filled": result.hang_hoa_filled,
        "hang_hoa_count": result.hang_hoa_filled,
        "warnings": list(result.warnings),
        "backend": backend,
    }


_queue: JobQueue | None = None
_queue_guard = threading.Lock()


def get_job_queue(settings: Settings | None = None) -> JobQueue:
    global _queue
    with _queue_guard:
        if _queue is None:
            _queue = JobQueue(settings or get_settings())
        return _queue
