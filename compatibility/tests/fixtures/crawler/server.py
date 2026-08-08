"""Local fixture server used only by crawler contract tests.

The production crawler must reject this loopback endpoint.  Tests that exercise
HTTP behavior inject a pinned fake connection instead of weakening SSRF policy.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        routes = {
            "/redirect-a": (HTTPStatus.MOVED_PERMANENTLY, "/redirect-b", b""),
            "/redirect-b": (HTTPStatus.FOUND, "/docs/", b""),
            "/loop-a": (HTTPStatus.FOUND, "/loop-b", b""),
            "/loop-b": (HTTPStatus.FOUND, "/loop-a", b""),
            "/soft-404": (HTTPStatus.OK, None, b"<h1>Not found</h1>"),
            "/api/data.json": (HTTPStatus.OK, None, b'{"ok":true}'),
        }
        status, location, body = routes.get(self.path, (HTTPStatus.NOT_FOUND, None, b"not found"))
        self.send_response(status)
        self.send_header(
            "Content-Type", "application/json" if self.path.endswith(".json") else "text/html"
        )
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return
