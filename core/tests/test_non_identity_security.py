from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import cast
from unittest import mock

from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.middleware import RequestBoundaryMiddleware
from core.security import (
    MAX_REQUEST_BODY_BYTES,
    MAX_WEBHOOK_BODY_BYTES,
    UnsafeInputError,
    neutralize_csv_formula,
    resolve_bounded_path,
    validate_json_shape,
    validate_outbound_url,
    validate_relative_path,
    validate_url,
)
from course_management.observability.events import AppEvent
from scripts.security_artifact_scan import ArtifactScanError, scan_artifacts
from scripts.security_canary_artifact import SECURITY_CANARIES, build_canary_artifact


class BoundaryHelperTests(SimpleTestCase):
    def test_relative_and_absolute_urls_reject_ambiguous_or_unsafe_shapes(self) -> None:
        self.assertEqual(
            validate_url("/courses/ml-zoomcamp", allow_relative=True),
            "/courses/ml-zoomcamp",
        )
        for value in (
            "//evil.invalid/path",
            "/courses/../private",
            "javascript:alert(1)",
            "https://user:password@example.invalid/",
            "https://example.invalid/path#token",
        ):
            with self.subTest(value=value), self.assertRaises(UnsafeInputError):
                validate_url(value, allow_relative=True)

    def test_private_network_destinations_fail_closed_before_fetch(self) -> None:
        for value in (
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://169.254.169.254/latest/meta-data",
            "https://metadata.google.internal/computeMetadata/v1",
        ):
            with self.subTest(value=value), self.assertRaises(UnsafeInputError):
                validate_outbound_url(value)

        with mock.patch("core.security.socket.getaddrinfo", return_value=[]):
            with self.assertRaises(UnsafeInputError):
                validate_outbound_url("https://provider.example.invalid/")

    def test_relative_paths_and_symlink_components_cannot_escape_root(self) -> None:
        self.assertEqual(validate_relative_path("reports/2026.json"), "reports/2026.json")
        for value in ("../secret", "reports/../secret", "/absolute", "reports\\secret"):
            with self.subTest(value=value), self.assertRaises(UnsafeInputError):
                validate_relative_path(value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "reports" / "ok.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                resolve_bounded_path(root, "reports/ok.json"),
                root / "reports" / "ok.json",
            )
            (root / "reports" / "alias").symlink_to(root / "ok.json")
            with self.assertRaises(UnsafeInputError):
                resolve_bounded_path(root, "reports/alias")

    def test_json_shape_and_csv_formula_guards_are_bounded(self) -> None:
        validate_json_shape({"items": [{"name": "safe"}]})
        with self.assertRaises(UnsafeInputError):
            validate_json_shape({"items": [{"nested": {"value": 1}}]}, max_depth=1)
        with self.assertRaises(UnsafeInputError):
            validate_json_shape({"items": [1, 2, 3]}, max_items=2)
        self.assertEqual(
            neutralize_csv_formula('=HYPERLINK("https://evil.invalid")'),
            '\'=HYPERLINK("https://evil.invalid")',
        )
        self.assertEqual(neutralize_csv_formula("ordinary"), "ordinary")

    def test_event_properties_redact_sensitive_keys_and_distinct_ids(self) -> None:
        event = AppEvent(
            name="security.canary",
            distinct_id="member@example.invalid",
            properties={
                "password": "never-echo-this-password",
                "provider_payload": {"email": "member@example.invalid"},
            },
        )
        serialized = json.dumps(event.normalized_properties(), sort_keys=True)
        self.assertNotIn("never-echo-this-password", serialized)
        self.assertNotIn("member@example.invalid", serialized)
        self.assertEqual(event.distinct_id, "[REDACTED]")

    def test_artifact_scan_fails_without_echoing_canaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "browser.json"
            artifact.write_text("safe-artifact", encoding="utf-8")
            result = scan_artifacts((artifact,), canaries=("synthetic-secret",))
            self.assertEqual(result["status"], "pass")
            with self.assertRaises(ArtifactScanError):
                scan_artifacts((artifact,))
            artifact.write_text("synthetic-secret", encoding="utf-8")
            with self.assertRaises(ArtifactScanError) as caught:
                scan_artifacts((artifact,), canaries=("synthetic-secret",))
            self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_security_canary_artifact_covers_every_publication_surface(self) -> None:
        artifact = build_canary_artifact()
        canary_count = artifact["canary_count"]
        self.assertIsInstance(canary_count, int)
        self.assertGreater(cast(int, canary_count), 0)
        surfaces = artifact["surfaces"]
        self.assertIsInstance(surfaces, dict)
        self.assertEqual(
            set(cast(dict[str, object], surfaces)),
            {"browser", "log", "metric", "trace", "audit", "webhook", "csv"},
        )
        serialized = json.dumps(artifact, sort_keys=True)
        for canary in SECURITY_CANARIES:
            self.assertNotIn(canary, serialized)


class RequestBoundaryBodyTests(SimpleTestCase):
    @staticmethod
    def _asgi_request(
        body: bytes,
        *,
        path: str = "/upload",
        content_length: str | None = None,
        transfer_encoding: str | None = None,
    ) -> ASGIRequest:
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/octet-stream"),
        ]
        if content_length is not None:
            headers.append((b"content-length", content_length.encode("ascii")))
        if transfer_encoding is not None:
            headers.append((b"transfer-encoding", transfer_encoding.encode("ascii")))
        body_file = tempfile.SpooledTemporaryFile(max_size=1024, mode="w+b")
        body_file.write(body)
        body_file.seek(0)
        return ASGIRequest(
            {
                "method": "POST",
                "path": path,
                "query_string": b"",
                "headers": headers,
                "server": ("testserver", 80),
            },
            body_file,
        )

    def test_asgi_missing_content_length_over_limit_is_rejected_before_parser(self) -> None:
        request = self._asgi_request(
            b"x" * (MAX_WEBHOOK_BODY_BYTES + 1),
            path="/api/datamailer/events",
        )
        called = False

        def downstream(_request: object) -> HttpResponse:
            nonlocal called
            called = True
            return HttpResponse("downstream")

        response = RequestBoundaryMiddleware(downstream)(request)
        self.assertEqual(response.status_code, 413)
        self.assertFalse(called)
        request._stream.close()

    def test_asgi_missing_content_length_general_body_is_rejected(self) -> None:
        request = self._asgi_request(b"x" * (MAX_REQUEST_BODY_BYTES + 1))
        response = RequestBoundaryMiddleware(lambda _request: HttpResponse("downstream"))(request)
        self.assertEqual(response.status_code, 413)
        request._stream.close()

    def test_asgi_understated_content_length_is_rejected_before_parser(self) -> None:
        request = self._asgi_request(
            b"x" * (MAX_WEBHOOK_BODY_BYTES + 1),
            path="/api/datamailer/events",
            content_length="1",
        )
        response = RequestBoundaryMiddleware(lambda _request: HttpResponse("downstream"))(request)
        self.assertEqual(response.status_code, 413)
        request._stream.close()

    def test_asgi_under_limit_body_is_replayed_after_stream_inspection(self) -> None:
        body = b"under-limit-body"
        request = self._asgi_request(body)
        observed: dict[str, bytes] = {}

        def downstream(inner_request: HttpRequest) -> HttpResponse:
            observed["body"] = inner_request.body
            return HttpResponse("downstream")

        response = RequestBoundaryMiddleware(downstream)(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed["body"], body)

    def test_nonseekable_invalid_content_length_fails_closed(self) -> None:
        request = RequestFactory().post(
            "/upload",
            data=b"",
            content_type="application/octet-stream",
        )
        request.META["CONTENT_LENGTH"] = "not-a-length"
        response = RequestBoundaryMiddleware(lambda _request: HttpResponse("downstream"))(request)
        self.assertEqual(response.status_code, 413)

    def test_nonseekable_chunked_body_without_content_length_fails_closed(self) -> None:
        request = RequestFactory().post(
            "/upload",
            data=b"safe-body",
            content_type="application/octet-stream",
        )
        request.META.pop("CONTENT_LENGTH", None)
        request.META["HTTP_TRANSFER_ENCODING"] = "chunked"
        response = RequestBoundaryMiddleware(lambda _request: HttpResponse("downstream"))(request)
        self.assertEqual(response.status_code, 413)


class ResponseBoundaryTests(TestCase):
    def test_baseline_headers_are_present_and_cors_is_denied(self) -> None:
        response = self.client.get("/")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("https://creators.spotify.com", response.headers["Content-Security-Policy"])
        self.assertIn("https://open.spotify.com", response.headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-eval'", response.headers["Content-Security-Policy"])
        self.assertIn("geolocation=()", response.headers["Permissions-Policy"])
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
        self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    @override_settings(DATAMAILER_WEBHOOK_TOKEN="synthetic-webhook-token")
    def test_webhook_body_limit_returns_bounded_json_without_parsing(self) -> None:
        body = json.dumps(
            {
                "event_id": "evt-body-limit",
                "event_type": "contact.hard_bounced",
                "email": "student@example.com",
                "padding": "x" * (256 * 1024),
            }
        )
        response = self.client.post(
            "/api/datamailer/events",
            body,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer synthetic-webhook-token",
        )
        self.assertEqual(response.status_code, 413)
        self.assertNotIn("padding", response.content.decode())

    def test_webhook_unsupported_method_reaches_post_method_guard(self) -> None:
        response = self.client.get("/api/datamailer/events")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "POST")
