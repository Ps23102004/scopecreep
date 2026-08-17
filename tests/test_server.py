"""The job runner and the frozen API contract."""

from __future__ import annotations

import os
import time

import pytest

from scopecreep import jobs


def _wait(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = jobs.get_status(job_id)
        if status["state"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError("job never finished")


def test_progress_accumulates_one_event_per_group():
    """The dashboard renders rows as they land, so events must not overwrite."""
    def fn(report_progress):
        for key in ("src/*.py", "tests/*.py", "web/*.css"):
            report_progress({"group": key, "classification": "core"})
        return {"verdict": "in_scope"}

    status = _wait(jobs.start_job(fn))
    assert status["state"] == "done"
    assert [e["group"] for e in status["progress"]] == ["src/*.py", "tests/*.py", "web/*.css"]
    assert status["result"] == {"verdict": "in_scope"}


def test_progress_is_visible_before_the_job_finishes():
    """A poll mid-run must already see the groups classified so far."""
    import threading

    release = threading.Event()

    def fn(report_progress):
        report_progress({"group": "src/*.py"})
        release.wait(timeout=5)
        return {"verdict": "in_scope"}

    job_id = jobs.start_job(fn)
    deadline = time.monotonic() + 5
    while not jobs.get_status(job_id)["progress"] and time.monotonic() < deadline:
        time.sleep(0.01)

    mid = jobs.get_status(job_id)
    assert mid["state"] == "running"
    assert mid["progress"] == [{"group": "src/*.py"}]
    release.set()
    assert _wait(job_id)["state"] == "done"


def test_failed_job_surfaces_the_error():
    status = _wait(jobs.start_job(lambda _: (_ for _ in ()).throw(ValueError("nope"))))
    assert status["state"] == "error"
    assert "ValueError: nope" in status["error"]


def test_unknown_job_id_is_an_error_not_a_crash():
    status = jobs.get_status("does-not-exist")
    assert status["state"] == "error" and status["progress"] == []


def test_progress_event_matches_the_api_contract():
    from scopecreep.groups import FileGroup
    from scopecreep.judge import GroupVerdict
    from scopecreep.server import _progress_event

    event = _progress_event(
        FileGroup(key="src/*.py", files=["src/a.py"], adds=3, dels=1),
        verdict=GroupVerdict("src/*.py", "core", "does the thing"),
    )
    assert set(event) == {"group", "files", "adds", "dels", "classification", "reason"}
    assert event["classification"] == "core"

    noise = _progress_event(
        FileGroup(key="*.lock", files=["poetry.lock"], adds=800, dels=700),
        noise_reason="excluded: lockfile",
    )
    assert noise["classification"] is None and noise["reason"] == "excluded: lockfile"


@pytest.mark.network
def test_fetch_a_real_public_pr():
    from scopecreep.github import fetch_pull_request

    pr = fetch_pull_request("octocat/Hello-World", 1, token=os.environ.get("GITHUB_TOKEN"))
    assert pr.title and isinstance(pr.files, list)
