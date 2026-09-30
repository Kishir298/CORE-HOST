"""Deterministic local R.E.S.C.S. HTTP fixture for data-layer tests.

Implements the R.E.S.C.S. HTTP data contract over ``127.0.0.1`` using only
the standard library. No real R.E.S.C.S. installation required.

Endpoints (all under ``/api/v1/`` per R.E.S.C.S. contract):

- ``GET /api/v1/records?namespace=&key_prefix=&owner=&query=`` (``query`` switches
  to substring search; without it this is a filtered list)
- ``GET /api/v1/records/search?query=&namespace=&key_prefix=&owner=``
- ``GET /api/v1/records/{namespace}/{key}?owner=`` (404 when absent)
- ``GET /api/v1/files/{file_id}/metadata`` (404 when absent)
- ``GET /api/v1/files/{file_id}/content`` (``{"file", "content_base64"}``)

Failure injection via :attr:`RescsFixture.fail_mode`:

- ``None`` — normal operation
- ``"unauthorized"`` — all reads answer 403
- ``"error"`` — all reads answer 500
- ``"timeout"`` — all reads sleep past the client timeout
- ``"garbage"`` — all reads answer 200 with a non-JSON body
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _seed_records():
    records = {}
    specs = [
        # (namespace, key, owner, topic, updated_day)
        ("core.memory", "flight-plan", "device-a", "pilot checklist", 12),
        ("core.memory", "pilot-notes", "device-a", "pilot observations", 10),
        ("core.memory", "shopping", "device-a", "groceries", 11),
        ("core.memory", "pilot-log", "device-b", "pilot hours", 9),
        ("core.memory", "shared-cache", "device-b", "pilot shared", 8),
        ("core.other", "pilot-misc", "device-a", "pilot misc", 7),
    ]
    for namespace, key, owner, topic, day in specs:
        records[(namespace, key)] = {
            "id": f"{namespace}/{key}",
            "namespace": namespace,
            "key": key,
            "value": {"topic": topic, "index": day},
            "metadata": {"name": key},
            "owner": owner,
            "version": 1,
            "etag": f"etag-{namespace}-{key}",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": f"2026-02-{day:02d}T00:00:00+00:00",
        }
    return records


def _seed_files():
    small = b"pilot-manual-bytes"
    big = b"y" * (1024 * 1024 + 8)
    files = {}
    for file_id, content, owner in (
        ("manual", small, "device-a"),
        ("big-blob", big, "device-a"),
    ):
        files[file_id] = (
            {
                "id": file_id,
                "filename": f"{file_id}.bin",
                "mime_type": "application/octet-stream",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "etag": f"etag-{file_id}",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-02-01T00:00:00+00:00",
                "owner": owner,
            },
            content,
        )
    return files


class _Handler(BaseHTTPRequestHandler):
    server_version = "RescsFixture/0.1"

    def log_message(self, *args):  # silence test output
        pass

    def _send(self, status, payload, content_type="application/json"):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        fixture = self.server.fixture  # type: ignore[attr-defined]
        mode = fixture.fail_mode
        if mode == "timeout":
            time.sleep(fixture.timeout_delay)
            return self._send(200, {"records": []})
        if mode == "unauthorized":
            return self._send(403, {"error": "forbidden"})
        if mode == "error":
            return self._send(500, {"error": "boom"})
        if mode == "garbage":
            return self._send(200, "not-json{{{", "text/plain")
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        parts = [p for p in parsed.path.split("/") if p]
        try:
            # All endpoints under /api/v1/
            if parts[:2] == ["api", "v1"] and parts[2:3] == ["records"] and len(parts) == 5:
                return self._record_one(fixture, parts[3], parts[4], params)
            if parts[:2] == ["api", "v1"] and parts[2:4] == ["records", "search"]:
                return self._record_search(fixture, params)
            if parts[:2] == ["api", "v1"] and parts[2:3] == ["records"] and len(parts) == 3:
                return self._record_list(fixture, params)
            if len(parts) == 5 and parts[:3] == ["api", "v1", "files"] and parts[4] == "metadata":
                return self._file_metadata(fixture, parts[3])
            if len(parts) == 5 and parts[:3] == ["api", "v1", "files"] and parts[4] == "content":
                return self._file_content(fixture, parts[3])
        except (BrokenPipeError, ConnectionResetError):
            return None
        return self._send(404, {"error": "not found"})

    def _filtered(self, fixture, params, query=None):
        records = []
        for record in fixture.records.values():
            if params.get("namespace") and record["namespace"] != params["namespace"]:
                continue
            if params.get("key_prefix") and not record["key"].startswith(params["key_prefix"]):
                continue
            if params.get("owner") and record["owner"] != params["owner"]:
                continue
            if query and query.lower() not in json.dumps(record["value"]).lower() \
                    and query.lower() not in record["key"].lower():
                continue
            records.append(record)
        return records

    def _record_list(self, fixture, params):
        if params.get("query"):
            return self._send(200, {"records": self._filtered(fixture, params, params["query"])})
        return self._send(200, {"records": self._filtered(fixture, params)})

    def _record_search(self, fixture, params):
        return self._send(200, {"records": self._filtered(fixture, params, params.get("query", ""))})

    def _record_one(self, fixture, namespace, key, params):
        namespace = urllib.parse.unquote(namespace)
        key = urllib.parse.unquote(key)
        record = fixture.records.get((namespace, key))
        if record is None:
            return self._send(404, {"error": "not found"})
        if params.get("owner") and record["owner"] != params["owner"]:
            return self._send(404, {"error": "not found"})
        return self._send(200, {"record": record})

    def _file_metadata(self, fixture, file_id):
        entry = fixture.files.get(urllib.parse.unquote(file_id))
        if entry is None:
            return self._send(404, {"error": "not found"})
        meta, _content = entry
        return self._send(200, {"file": meta})

    def _file_content(self, fixture, file_id):
        entry = fixture.files.get(urllib.parse.unquote(file_id))
        if entry is None:
            return self._send(404, {"error": "not found"})
        meta, content = entry
        return self._send(
            200,
            {
                "file": meta,
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
        )


class RescsFixture:
    """Running deterministic R.E.S.C.S. HTTP fixture (context-managed)."""

    def __init__(self):
        self.records = _seed_records()
        self.files = _seed_files()
        self.fail_mode: str | None = None
        self.timeout_delay = 5.0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """Return the fixture base URL (valid after start)."""
        assert self._server is not None, "fixture is not running"
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> RescsFixture:
        """Start serving on an ephemeral localhost port."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.fixture = self  # type: ignore[attr-defined]
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Shut down cleanly."""
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread is not None:
            try:
                self._thread.join(timeout=5)
            except Exception:
                pass
            self._thread = None

    def __enter__(self) -> RescsFixture:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


__all__ = ["RescsFixture"]
