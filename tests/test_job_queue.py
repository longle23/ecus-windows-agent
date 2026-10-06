from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi import HTTPException, Response

from app.completed_jobs import mark_succeeded
from app.config import Settings
from app.job_queue import JobQueue
from app.models import EcusJobRequest
from app.rpa import RpaResult
from app.routes import jobs as jobs_route


def _settings(tmp: Path, **kwargs) -> Settings:
    return Settings(
        log_dir=tmp,
        screenshot_dir=tmp / "screenshots",
        api_key="test-key",
        rpa_backend="stub",
        stub_delay_sec=0,
        job_queue_max=kwargs.get("job_queue_max", 200),
        job_timeout_sec=30,
    )


def _job(job_id: str) -> EcusJobRequest:
    return EcusJobRequest.model_validate(
        {
            "jobId": job_id,
            "sendId": f"send-{job_id}",
            "danh_sach_hang": {"hang_hoa": [{"stt": "1"}]},
        }
    )


def _ok(job: EcusJobRequest) -> RpaResult:
    return RpaResult(
        success=True,
        message="ok",
        so_to_khai=f"TK-{job.jobId}",
        hang_hoa_requested=1,
        hang_hoa_filled=1,
    )


def _fail(job: EcusJobRequest) -> RpaResult:
    return RpaResult(
        success=False,
        message="nope",
        hang_hoa_requested=1,
        hang_hoa_filled=0,
        warnings=["nope"],
    )


def _wait(queue: JobQueue, job_id: str, statuses: set[str], timeout: float = 5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = queue.get(job_id)
        if last is not None and last.status in statuses:
            return last
        time.sleep(0.01)
    seen = None if last is None else last.status
    raise AssertionError(f"{job_id} stayed {seen}, wanted {statuses}")


class JobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_runs_in_arrival_order(self) -> None:
        started: list[str] = []
        release = threading.Event()

        def runner(job: EcusJobRequest) -> RpaResult:
            started.append(job.jobId)
            if len(started) == 1:
                self.assertTrue(release.wait(5))
            return _ok(job)

        queue = JobQueue(_settings(self.tmp), runner=runner)
        self.addCleanup(queue.stop)
        self.addCleanup(release.set)
        for index in range(5):
            decision = queue.submit(_job(f"j{index}"))
            self.assertEqual(decision.code, "created")
            self.assertEqual(decision.response.status, "queued")
            self.assertEqual(decision.response.position, index + 1)
        self.assertTrue((self.tmp / "job_queue.json").exists())

        queue.start()
        running = _wait(queue, "j0", {"running"})
        self.assertEqual(running.position, 1)
        self.assertEqual(queue.snapshot(), {"busy": True, "holder": "j0", "queueDepth": 4})
        release.set()
        for index in range(5):
            done = _wait(queue, f"j{index}", {"success"})
            self.assertEqual(done.so_to_khai, f"TK-j{index}")
            self.assertEqual(done.hang_hoa_filled, 1)
        self.assertEqual(started, [f"j{index}" for index in range(5)])

        again = JobQueue(_settings(self.tmp), runner=runner)
        self.assertEqual(again.submit(_job("j0")).code, "succeeded")

    def test_duplicate_job_id_is_not_queued_twice(self) -> None:
        queue = JobQueue(_settings(self.tmp), runner=_ok)
        self.addCleanup(queue.stop)
        job = _job("same")
        self.assertEqual(queue.submit(job).code, "created")
        second = queue.submit(job)
        self.assertEqual(second.code, "existing")
        self.assertEqual(second.response.status, "queued")
        self.assertEqual(queue.snapshot()["queueDepth"], 1)

    def test_queue_full_rejects_without_dropping_waiters(self) -> None:
        queue = JobQueue(_settings(self.tmp, job_queue_max=2), runner=_ok)
        self.addCleanup(queue.stop)
        self.assertEqual(queue.submit(_job("a")).code, "created")
        self.assertEqual(queue.submit(_job("b")).code, "created")
        self.assertEqual(queue.submit(_job("c")).code, "full")
        self.assertEqual(queue.snapshot()["queueDepth"], 2)
        self.assertIsNone(queue.get("c"))

    def test_queued_jobs_resume_after_restart(self) -> None:
        first = JobQueue(_settings(self.tmp), runner=_ok)
        self.addCleanup(first.stop)
        first.submit(_job("a"))
        first.submit(_job("b"))

        ran: list[str] = []

        def runner(job: EcusJobRequest) -> RpaResult:
            ran.append(job.jobId)
            return _ok(job)

        second = JobQueue(_settings(self.tmp), runner=runner)
        self.addCleanup(second.stop)
        self.assertEqual(second.get("a").status, "queued")
        self.assertEqual(second.get("b").status, "queued")
        self.assertEqual(second.get("a").position, 1)
        self.assertEqual(second.get("b").position, 2)
        second.start()
        _wait(second, "a", {"success"})
        _wait(second, "b", {"success"})
        self.assertEqual(ran, ["a", "b"])

    def test_restart_marks_running_job_failed_unless_already_saved(self) -> None:
        settings = _settings(self.tmp)
        mark_succeeded(settings, "done-job", "TK9")
        payload = {
            "order": ["done-job", "open-job", "wait-job"],
            "jobs": {
                "done-job": _running_record("done-job"),
                "open-job": _running_record("open-job"),
                "wait-job": _queued_record("wait-job"),
            },
        }
        (self.tmp / "job_queue.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

        queue = JobQueue(settings, runner=_ok)
        self.addCleanup(queue.stop)
        done = queue.get("done-job")
        self.assertIsNotNone(done)
        assert done is not None
        self.assertEqual(done.status, "success")
        self.assertEqual(done.so_to_khai, "TK9")
        interrupted = queue.get("open-job")
        self.assertIsNotNone(interrupted)
        assert interrupted is not None
        self.assertEqual(interrupted.status, "failed")
        self.assertIn("restarted", interrupted.message)
        self.assertEqual(queue.get("wait-job").status, "queued")
        self.assertEqual(queue.snapshot()["queueDepth"], 1)

        reloaded = JobQueue(settings, runner=_ok)
        self.addCleanup(reloaded.stop)
        self.assertEqual(reloaded.get("open-job").status, "failed")
        self.assertEqual(reloaded.snapshot()["queueDepth"], 1)

    def test_failed_job_rejoins_the_tail(self) -> None:
        release = threading.Event()
        calls: list[str] = []

        def runner(job: EcusJobRequest) -> RpaResult:
            calls.append(job.jobId)
            if job.jobId == "a" and calls.count("a") == 1:
                return _fail(job)
            if job.jobId == "b":
                self.assertTrue(release.wait(5))
            return _ok(job)

        queue = JobQueue(_settings(self.tmp), runner=runner)
        self.addCleanup(queue.stop)
        self.addCleanup(release.set)
        queue.submit(_job("a"))
        queue.submit(_job("b"))
        queue.start()
        _wait(queue, "a", {"failed"})
        _wait(queue, "b", {"running"})
        again = queue.submit(_job("a"))
        self.assertEqual(again.code, "created")
        self.assertEqual(again.response.status, "queued")
        self.assertEqual(again.response.position, 2)
        self.assertEqual(queue.snapshot()["holder"], "b")
        release.set()
        done = _wait(queue, "a", {"success"})
        self.assertTrue(done.success)
        self.assertEqual(calls, ["a", "b", "a"])

    def test_already_succeeded_is_not_enqueued(self) -> None:
        settings = _settings(self.tmp)
        mark_succeeded(settings, "old", "TK")
        calls: list[str] = []

        def runner(job: EcusJobRequest) -> RpaResult:
            calls.append(job.jobId)
            return _ok(job)

        queue = JobQueue(settings, runner=runner)
        self.addCleanup(queue.stop)
        decision = queue.submit(_job("old"))
        self.assertEqual(decision.code, "succeeded")
        queue.start()
        time.sleep(0.05)
        self.assertEqual(calls, [])
        self.assertEqual(queue.snapshot()["queueDepth"], 0)

    def test_http_status_codes(self) -> None:
        settings = _settings(self.tmp, job_queue_max=1)
        queue = JobQueue(settings, runner=_ok)
        self.addCleanup(queue.stop)
        original = jobs_route.get_job_queue
        jobs_route.get_job_queue = lambda settings=None: queue
        self.addCleanup(lambda: setattr(jobs_route, "get_job_queue", original))

        response = Response()
        created = jobs_route.create_job(_job("n1"), response, settings)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(created.status, "queued")

        response = Response()
        existing = jobs_route.create_job(_job("n1"), response, settings)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(existing.status, "queued")

        response = Response()
        with self.assertRaises(HTTPException) as full:
            jobs_route.create_job(_job("n2"), response, settings)
        self.assertEqual(full.exception.status_code, 429)

        mark_succeeded(settings, "done", "TK")
        with self.assertRaises(HTTPException) as conflict:
            jobs_route.create_job(_job("done"), Response(), settings)
        self.assertEqual(conflict.exception.status_code, 409)

        with self.assertRaises(HTTPException) as missing:
            jobs_route.read_job("missing", settings)
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(jobs_route.read_job("n1", settings).status, "queued")


def _running_record(job_id: str) -> dict:
    record = _queued_record(job_id)
    record["status"] = "running"
    record["startedAt"] = "2026-01-01T00:00:01+00:00"
    return record


def _queued_record(job_id: str) -> dict:
    return {
        "jobId": job_id,
        "status": "queued",
        "enqueuedAt": "2026-01-01T00:00:00+00:00",
        "startedAt": None,
        "finishedAt": None,
        "request": {
            "jobId": job_id,
            "sendId": f"send-{job_id}",
            "danh_sach_hang": {"hang_hoa": [{"stt": "1"}]},
        },
        "result": None,
    }


if __name__ == "__main__":
    unittest.main()
