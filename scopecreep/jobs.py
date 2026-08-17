"""Tiny thread-based background job runner.

    start_job(fn) -> str   # returns job_id immediately, fn runs in a thread
    get_status(job_id) -> dict

Same shape as cvewatch's runner with one deliberate difference: progress is a
*list* that grows, not a single last-value slot. The dashboard renders each
file group the moment it's classified, so every event has to survive.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

# How long a finished job's record is kept before it's swept.
_TTL_S = 3600.0


def _sweep() -> None:
    """Drop finished jobs older than _TTL_S. Caller must hold _LOCK.

    Lazy: runs on start_job/get_status, not on a timer. An idle server never
    reclaims, but the ceiling is bounded by past activity.
    """
    now = time.monotonic()
    expired = [
        job_id
        for job_id, record in _JOBS.items()
        if record["finished_at"] is not None and now - record["finished_at"] > _TTL_S
    ]
    for job_id in expired:
        del _JOBS[job_id]


def start_job(fn: Callable[[Callable[[dict], None]], Any]) -> str:
    """Run ``fn(report_progress)`` in a background thread; return the job id."""
    job_id = str(uuid.uuid4())
    with _LOCK:
        _sweep()
        _JOBS[job_id] = {
            "state": "running",
            "progress": [],
            "result": None,
            "error": None,
            "finished_at": None,
        }

    def report_progress(event: dict) -> None:
        """Append one progress event. Called once per file group."""
        with _LOCK:
            record = _JOBS.get(job_id)
            if record is not None:
                record["progress"].append(event)

    def _run() -> None:
        record = _JOBS[job_id]
        try:
            result = fn(report_progress)
        except BaseException as exc:  # noqa: BLE001 - surface any failure
            with _LOCK:
                record["state"] = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["finished_at"] = time.monotonic()
        else:
            with _LOCK:
                record["state"] = "done"
                record["result"] = result
                record["finished_at"] = time.monotonic()

    threading.Thread(target=_run, daemon=True, name=f"job-{job_id[:8]}").start()
    return job_id


def get_status(job_id: str) -> dict:
    """Return {'state', 'progress', 'result', 'error'}; unknown ids are errors."""
    with _LOCK:
        _sweep()
        record = _JOBS.get(job_id)
        if record is None:
            return {
                "state": "error",
                "progress": [],
                "result": None,
                "error": f"unknown or expired job_id: {job_id}",
            }
        return {
            "state": record["state"],
            "progress": list(record["progress"]),
            "result": record["result"],
            "error": record["error"],
        }
