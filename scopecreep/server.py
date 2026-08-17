"""Local stdlib-only HTTP server for the ScopeCreep dashboard.

Binds 127.0.0.1 only. No web framework — http.server + ThreadingHTTPServer,
matching the sibling cvewatch / llm-ladder servers.

API contract (frozen — the frontend depends on it exactly):
    POST /api/check {pr}  -> {job_id}
    GET  /api/status/{id} -> {state, progress: [...], result: {...}}
Progress events are appended one per file group as it is classified, so the
page can fill in rows live instead of blinking once at the end.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scopecreep import jobs
from scopecreep.check import check

DEFAULT_PORT = 8200
PORT = int(os.environ.get("SCOPECREEP_PORT", DEFAULT_PORT))
WEB_DIR = (Path(__file__).resolve().parent.parent / "web").resolve()

_ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
_ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

_STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
}


class _BadRequestError(Exception):
    """Client-side request problems that map to HTTP 400."""


def _progress_event(group, verdict=None, noise_reason=None) -> dict:
    """One row of the live table, in the shape the frontend expects."""
    return {
        "group": group.key,
        "files": group.files,
        "adds": group.adds,
        "dels": group.dels,
        "classification": verdict.classification if verdict else None,
        "reason": verdict.reason if verdict else noise_reason,
    }


def _run_check(body: dict) -> dict:
    pr_ref = body.get("pr")
    if not isinstance(pr_ref, str) or not pr_ref.strip():
        raise _BadRequestError("'pr' is required, e.g. owner/repo#123")
    model = body.get("model")
    model = model if isinstance(model, str) and model.strip() else None

    def fn(report_progress):
        result = check(
            pr_ref.strip(),
            model=model,
            on_group=lambda g, v: report_progress(_progress_event(g, verdict=v)),
            on_noise=lambda g: report_progress(
                _progress_event(g, noise_reason=f"excluded: {g.noise_reason}")
            ),
        )
        return {
            "verdict": result.rollup.verdict,
            "drift_score": result.rollup.drift_score,
            "summary": result.rollup.summary,
            "ledger": result.ledger,
            "footer": result.footer,
        }

    return {"job_id": jobs.start_job(fn)}


class Handler(BaseHTTPRequestHandler):
    server_version = "scopecreep/0.1"

    # -- trust-boundary check -------------------------------------------------

    def _origin_allowed(self) -> bool:
        if self.headers.get("Host", "") not in _ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in _ALLOWED_ORIGINS

    # -- helpers --------------------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise _BadRequestError("missing Content-Length header")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise _BadRequestError("malformed Content-Length header")
        if length < 0:
            raise _BadRequestError("negative Content-Length header")
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise _BadRequestError(
                f"request body must be a JSON object, got {type(body).__name__}"
            )
        return body

    def _serve_static(self, url_path: str) -> None:
        rel = _STATIC_ROUTES.get(url_path) or unquote(url_path).lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR not in target.parents and target != WEB_DIR:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- dispatch -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle(self._route_get)

    def do_POST(self) -> None:  # noqa: N802
        self._handle(self._route_post)

    def _handle(self, route_fn) -> None:
        if not self._origin_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden host/origin"})
            return
        try:
            route_fn()
        except _BadRequestError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON body"})
        except Exception:  # noqa: BLE001 - never leak internals to the client
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"}
            )

    def _route_get(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/status/"):
            job_id = path[len("/api/status/") :]
            self._send_json(HTTPStatus.OK, jobs.get_status(job_id))
            return
        if path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._serve_static(path)

    def _route_post(self) -> None:
        if urlsplit(self.path).path == "/api/check":
            self._send_json(HTTPStatus.OK, _run_check(self._read_json_body()))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving on http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
