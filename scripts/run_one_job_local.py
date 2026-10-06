"""POST samples/job.sample.json to the local agent, then poll until it finishes."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_POLL_SEC = 2
_WAIT_SEC = 1800


def _api_key() -> str:
    env = ROOT / ".env"
    if not env.exists():
        return "CHANGE_ME"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    return "CHANGE_ME"


def _call(method: str, url: str, body: bytes | None = None) -> tuple[int, dict, str]:
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Api-Key": _api_key(),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed, raw


def main() -> int:
    sample = ROOT / "samples" / "job.sample.json"
    print("POST http://127.0.0.1:8787/jobs ...")
    try:
        code, data, raw = _call("POST", "http://127.0.0.1:8787/jobs", sample.read_bytes())
        print(raw)
    except urllib.error.URLError as exc:
        print(
            f"ERROR: cannot reach agent ({exc}). "
            "Start agent first: run.bat  (uvicorn on :8787)"
        )
        return 2
    if code >= 400:
        return 1
    job_id = str(data.get("jobId") or "")
    if data.get("status") in {"success", "failed"}:
        return 0 if data.get("success") else 1
    if not job_id:
        print("ERROR: agent did not return a jobId")
        return 1

    deadline = time.time() + _WAIT_SEC
    while time.time() < deadline:
        time.sleep(_POLL_SEC)
        try:
            _code, data, raw = _call("GET", f"http://127.0.0.1:8787/jobs/{job_id}")
        except urllib.error.URLError as exc:
            print(f"ERROR: cannot reach agent ({exc})")
            return 2
        status_name = data.get("status")
        print(f"status={status_name} position={data.get('position')}")
        if status_name in {"success", "failed"}:
            print(raw)
            return 0 if data.get("success") else 1
    print(f"ERROR: job {job_id} still {data.get('status')} after {_WAIT_SEC}s")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
