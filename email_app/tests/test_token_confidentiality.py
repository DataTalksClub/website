"""A recipient token identifies a person and must not reach a log or a page.

These URLs put an opaque per-recipient token in the *path*, which is the one part
of a request the access log keeps and the one part Django writes to
`django.request` for every response of 400 or above.  So the token has to be kept
out of both, and out of the rendered page.
"""

from __future__ import annotations

import logging
from unittest import mock

from django.test import TestCase, override_settings

from core.gunicorn_logging import redact_token_path
from email_app import relay_links
from email_app.tests.support import FakeRelay, unreachable_relay

RELAY = "http://relay.website.internal:8000"
TOKEN = "kD3Yy8x-Ug2f_QwErTyUiOpAsDfGhJkLzXcVbNm1234"


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover - defensive
            self.lines.append(str(record.msg))


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY)
class TokenNeverReachesLoggingTests(TestCase):
    def _capture(self, path: str, relay: FakeRelay, **post) -> str:  # type: ignore[no-untyped-def]
        handler = CapturingHandler()
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            with mock.patch.object(relay_links, "_pool", return_value=relay):
                if post:
                    self.client.post(path, post)
                else:
                    self.client.get(path)
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)
        return "\n".join(handler.lines)

    def test_no_log_record_carries_the_token_on_any_outcome(self) -> None:
        cases: tuple[tuple[str, FakeRelay, dict[str, str]], ...] = (
            (f"/t/o/{TOKEN}.gif", FakeRelay(status_code=404), {}),
            (f"/t/o/{TOKEN}.gif", unreachable_relay(), {}),
            (f"/t/c/{TOKEN}", FakeRelay(status_code=400), {}),
            (f"/t/c/{TOKEN}", unreachable_relay(), {}),
            (f"/unsubscribe/{TOKEN}", FakeRelay(status_code=404), {}),
            (f"/unsubscribe/{TOKEN}", unreachable_relay(), {"scope": "client"}),
        )
        for path, relay, post in cases:
            with self.subTest(path=path, relay=type(relay).__name__):
                captured = self._capture(path, relay, **post)
                self.assertNotIn(TOKEN, captured)

    def test_the_rendered_page_never_repeats_the_token_back(self) -> None:
        with mock.patch.object(relay_links, "_pool", return_value=FakeRelay(status_code=200)):
            response = self.client.get(f"/unsubscribe/{TOKEN}")
        self.assertNotIn(TOKEN, response.content.decode())

    def test_a_transport_failure_raises_nothing_that_could_carry_the_token(self) -> None:
        # requests puts the full URL in its exception text, so the adapter must not
        # let one escape into a traceback or an error report.
        with mock.patch.object(relay_links, "_pool", return_value=unreachable_relay()):
            result = relay_links.record_open(TOKEN)
        self.assertIs(result.outcome, relay_links.BridgeOutcome.UNAVAILABLE)


class AccessLogRedactionTests(TestCase):
    def test_the_token_segment_is_replaced_and_the_route_is_kept(self) -> None:
        cases = {
            f"/t/o/{TOKEN}.gif": "/t/o/[token].gif",
            f"/t/c/{TOKEN}": "/t/c/[token]",
            f"/unsubscribe/{TOKEN}": "/unsubscribe/[token]",
            f"GET /unsubscribe/{TOKEN} HTTP/1.1": "GET /unsubscribe/[token] HTTP/1.1",
        }
        for value, expected in cases.items():
            with self.subTest(value=expected):
                redacted = redact_token_path(value)
                self.assertEqual(redacted, expected)
                self.assertNotIn(TOKEN, redacted)

    def test_ordinary_paths_are_left_alone(self) -> None:
        for path in ("/", "/courses", "/events/upcoming", "/podcast/s01e01"):
            self.assertEqual(redact_token_path(path), path)

    def test_the_deployed_gunicorn_logger_uses_the_redacting_atoms(self) -> None:
        from gunicorn.config import Config  # type: ignore[import-untyped]

        from core.gunicorn_logging import RecipientTokenSafeLogger

        config = Config()
        config.set("accesslog", "-")
        logger = RecipientTokenSafeLogger(config)
        atoms = logger.atoms(
            mock.Mock(status="200 OK", sent=43, headers=[]),
            mock.Mock(headers=[]),
            {
                "REQUEST_METHOD": "GET",
                "RAW_URI": f"/unsubscribe/{TOKEN}",
                "PATH_INFO": f"/unsubscribe/{TOKEN}",
                "SERVER_PROTOCOL": "HTTP/1.1",
            },
            __import__("datetime").timedelta(microseconds=42),
        )
        self.assertEqual(atoms["U"], "/unsubscribe/[token]")
        self.assertNotIn(TOKEN, " ".join(str(value) for value in atoms.values()))


@override_settings(RELAY_LINK_BRIDGE_BASE_URL=RELAY, OBSERVABILITY_EVENT_BACKENDS=["log"])
class DegradationEventTests(TestCase):
    """The bridge reports degradation without reporting who was degraded."""

    def _events(self, path: str, relay: FakeRelay, **post) -> list[dict]:  # type: ignore[no-untyped-def]
        recorded: list[dict] = []

        def capture(name, *, request=None, user=None, distinct_id=None, properties=None):  # type: ignore[no-untyped-def]
            recorded.append(
                {
                    "name": name,
                    "request": request,
                    "distinct_id": distinct_id,
                    "properties": dict(properties or {}),
                }
            )

        with (
            mock.patch.object(relay_links, "_pool", return_value=relay),
            mock.patch("email_app.views.record_event", side_effect=capture),
        ):
            if post:
                self.client.post(path, post)
            else:
                self.client.get(path)
        return recorded

    def test_a_degraded_click_reports_a_low_cardinality_event(self) -> None:
        events = self._events(f"/t/c/{TOKEN}?u=https://example.com/post", unreachable_relay())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "relay_link_bridge")
        self.assertEqual(events[0]["properties"], {"route": "click", "outcome": "unavailable"})

    def test_no_event_ever_carries_the_request_or_the_token(self) -> None:
        # The shared helper copies `request.path` into an event, and on these
        # routes the path is the recipient's token, so the request must not be
        # handed over at all.
        cases: tuple[tuple[str, FakeRelay, dict[str, str]], ...] = (
            (f"/t/c/{TOKEN}?u=https://example.com/post", unreachable_relay(), {}),
            (f"/unsubscribe/{TOKEN}", FakeRelay(status_code=404), {}),
            (f"/unsubscribe/{TOKEN}", unreachable_relay(), {"scope": "client"}),
        )
        for path, relay, post in cases:
            with self.subTest(path=path):
                for event in self._events(path, relay, **post):
                    self.assertIsNone(event["request"])
                    self.assertEqual(event["distinct_id"], "anonymous")
                    self.assertNotIn(TOKEN, str(event))

    def test_the_open_pixel_stays_silent(self) -> None:
        # 130,000 pixels can arrive in one window; the route's health lives in
        # Relay's own open counters rather than in a log line per recipient.
        self.assertEqual(self._events(f"/t/o/{TOKEN}.gif", unreachable_relay()), [])

    def test_a_successful_request_reports_nothing(self) -> None:
        self.assertEqual(
            self._events(f"/t/c/{TOKEN}?u=https://example.com/post", FakeRelay(status_code=302)),
            [],
        )
