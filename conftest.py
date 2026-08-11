from __future__ import annotations

import hashlib
import re
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, Route
from pytest_django import live_server_helper

from test_support.email_backend import SYNTHETIC_EMAIL_BACKEND
from test_support.messaging import (
    CaptureSafetyError,
    artifact_canaries,
    owned_publication_roots,
    redact_trace_emails,
    scan_artifacts,
)
from test_support.network import NetworkGuard
from test_support.provenance import validate_public_fixture
from test_support.runtime import (
    DEFAULT_FROZEN_AT,
    current_worker_id,
    get_test_runtime,
)
from test_support.safety import (
    LOCAL_MARKERS,
    SAFETY_MARKERS,
    SafetyAuthorization,
    authorize_from_environment,
)

PUBLIC_FIXTURE_ROOT = Path(__file__).resolve().parent / "test_support" / "fixtures" / "public"
OFFLINE_ROUTE_FIXTURES: dict[str, tuple[str, str, str]] = {}
EXPECTED_LOCAL_RESPONSES: dict[str, tuple[tuple[re.Pattern[str], int], ...]] = {
    "test_complete_accessibility_registry": (
        (re.compile(r"^/_accessibility/identity-conflict/$"), 409),
        (re.compile(r"^/__accessibility_missing__$"), 404),
        (re.compile(r"^/people(?:/|\.html)?$"), 404),
        (re.compile(r"^/studio/$"), 403),
        (re.compile(r"^/studio/audit/$"), 403),
        (re.compile(r"^/studio/access/api-credentials/$"), 403),
        (re.compile(r"^/studio/events/historical-registration-totals/$"), 403),
        (
            re.compile(r"^/studio/events/historical-registration-totals/mappings/$"),
            409,
        ),
        (
            re.compile(r"^/studio/events/historical-registration-totals/[0-9a-f-]{36}/activate/$"),
            409,
        ),
        (re.compile(r"^/register/synthetic-[a-z0-9-]+/$"), 404),
    ),
    "test_reflow_zoom_spacing_reduced_motion_and_forced_colors": (
        (re.compile(r"^/_accessibility/identity-conflict/$"), 409),
    ),
    "test_deployed_public_and_studio_html_are_exact_and_read_only": (
        (re.compile(r"^/__dtc_deployed_smoke_missing__$"), 404),
    ),
    "test_fixture_redirect_gone_and_unknown_are_direct": (
        (re.compile(r"^/gone$"), 410),
        (re.compile(r"^/does-not-exist$"), 404),
    ),
    "test_preview_token_and_response_matrix_are_safe": (
        (re.compile(r"^/private/preview/$"), 400),
        (re.compile(r"^/deliberate-missing$"), 404),
    ),
    "test_wiki_search_graph_and_removed_mount": ((re.compile(r"^/podwiki$"), 404),),
    "test_no_database_course_catalog_uses_copied_cmp_empty_state": (
        (re.compile(r"^/courses/de-zoomcamp-2026$"), 404),
    ),
    "test_empty_optional_and_error_states_are_responsive": (
        (re.compile(r"^/events/not-a-real-event$"), 404),
    ),
    "test_studio_stage_replay_map_validate_activate_preview_rollback_and_denial": (
        (
            re.compile(r"^/studio/events/historical-registration-totals/mappings/$"),
            409,
        ),
        (
            re.compile(
                r"^/studio/events/historical-registration-totals/[0-9a-f-]{36}/"
                r"(?:activate|validate)/$"
            ),
            409,
        ),
        (
            re.compile(r"^/studio/events/historical-registration-totals/$"),
            403,
        ),
    ),
    "test_revocation_prevents_back_cache_and_reload": ((re.compile(r"^/studio/audit/$"), 403),),
    "test_stale_browser_save_is_atomic_and_retryable": ((re.compile(r"^/studio/settings$"), 409),),
    "test_duplicate_conflict_guidance_is_generic_and_accessible": (
        (re.compile(r"^/_identity-fixtures/link-conflict/$"), 409),
    ),
    "test_returning_learner_keeps_one_account_across_site_and_courses": (
        (re.compile(r"^/accounts/settings/email-preferences/$"), 503),
    ),
    "test_fixture_exercises_exact_case_unicode_query_and_slash_contracts": (
        (re.compile(r"^/docs/(?:exact/|Exact)$"), 404),
    ),
}
_STATUS_CONSOLE_RE = re.compile(r"server responded with a status of ([1-5][0-9]{2})")
_EMAIL_PREFERENCES_CONSOLE_RE = re.compile(
    r"^Error loading email preferences: Error: Email preferences fetch failed\n"
    r"\s+at http://127\.0\.0\.1:[0-9]+/static/settings_toggles\.js:44:17$"
)
_OFFLINE_ROUTE_BYTES: dict[str, tuple[bytes, str]] = {}


class _PageLifecycle(Protocol):
    @property
    def main_frame(self) -> object: ...


class _RequestLifecycle(Protocol):
    url: str

    @property
    def frame(self) -> object: ...

    def is_navigation_request(self) -> bool: ...


@dataclass
class _PendingAbortBatch:
    generation: int
    commit_generation: int | None
    last_event: int
    failures: list[tuple[str, str]]


@dataclass
class _NavigationAttempt:
    generation: int
    main_request: object | None = None


@dataclass(frozen=True)
class _TrackedRequest:
    page: _PageLifecycle
    generation: int
    same_origin: bool
    attempt_generation: int | None
    main_navigation: bool


class NavigationCancellationTracker:
    """Classify same-origin aborts using browser navigation/close lifecycle."""

    def __init__(self, allowed_origin: str | None) -> None:
        self._allowed_origin = allowed_origin
        self._context_closing = False
        self._page_generations: dict[_PageLifecycle, int] = {}
        self._page_events: dict[_PageLifecycle, int] = {}
        self._committed_generations: dict[_PageLifecycle, int] = {}
        self._active_attempts: dict[_PageLifecycle, _NavigationAttempt] = {}
        self._closing_pages: set[_PageLifecycle] = set()
        self._requests: dict[object, _TrackedRequest] = {}
        self._pending_aborts: dict[_PageLifecycle, _PendingAbortBatch] = {}
        self._unexplained_aborts: list[tuple[str, str]] = []

    def request_started(
        self,
        page: _PageLifecycle,
        request: _RequestLifecycle,
    ) -> None:
        event = self._next_event(page)
        generation = self._page_generations.setdefault(page, 0)
        same_origin = self._is_same_origin(request.url)
        main_navigation = (
            same_origin and request.is_navigation_request() and request.frame == page.main_frame
        )
        attempt_generation: int | None = None
        if main_navigation:
            attempt = self._active_attempts.get(page)
            if attempt is not None and attempt.main_request is None:
                self._seal_inactive_pending(page)
                attempt.main_request = request
            else:
                self._resolve_renderer_navigation_batch(page, generation, event)
                attempt = self._start_navigation(page, main_request=request)
            generation = attempt.generation
            attempt_generation = attempt.generation
        else:
            self._seal_inactive_pending(page)
            attempt = self._active_attempts.get(page)
            if same_origin and attempt is not None:
                attempt_generation = attempt.generation
        self._requests[request] = _TrackedRequest(
            page=page,
            generation=generation,
            same_origin=same_origin,
            attempt_generation=attempt_generation,
            main_navigation=main_navigation,
        )

    def request_finished(self, request: object) -> None:
        state = self._requests.pop(request, None)
        if state is None:
            return
        page = state.page
        self._next_event(page)
        if state.main_navigation:
            self._end_attempt(page, state.attempt_generation)
            self._finalize_pending(page)
        else:
            self._seal_inactive_pending(page)

    def begin_page_close(self, page: _PageLifecycle) -> None:
        self._next_event(page)
        self._finalize_pending(page)
        self._active_attempts.pop(page, None)
        self._closing_pages.add(page)

    def begin_navigation(self, page: _PageLifecycle) -> int:
        self._next_event(page)
        self._finalize_pending(page)
        return self._start_navigation(page).generation

    def _start_navigation(
        self,
        page: _PageLifecycle,
        *,
        main_request: object | None = None,
    ) -> _NavigationAttempt:
        generation = self._page_generations.setdefault(page, 0) + 1
        self._page_generations[page] = generation
        attempt = _NavigationAttempt(generation, main_request)
        self._active_attempts[page] = attempt
        return attempt

    def navigation_committed(self, page: _PageLifecycle) -> None:
        self._next_event(page)
        generation = self._page_generations.setdefault(page, 0)
        if self._committed_generations.get(page) == generation:
            self._finalize_pending(page)
        else:
            self._resolve_commit_batch(page, generation)
            self._committed_generations[page] = generation
        self._active_attempts.pop(page, None)

    def navigation_failed(self, page: _PageLifecycle) -> None:
        self._next_event(page)
        self._finalize_pending(page)
        self._active_attempts.pop(page, None)

    def begin_context_close(self) -> None:
        for page in tuple(self._pending_aborts):
            self._finalize_pending(page)
        self._active_attempts.clear()
        self._context_closing = True

    def track_abort(
        self,
        page: _PageLifecycle,
        request: _RequestLifecycle,
        failure: str | None,
    ) -> bool:
        state = self._requests.pop(request, None)
        event = self._next_event(page)
        if failure != "net::ERR_ABORTED" or not self._is_same_origin(request.url):
            self._finalize_pending(page)
            if state is not None and state.main_navigation:
                self._end_attempt(state.page, state.attempt_generation)
            return False
        if self._context_closing or page in self._closing_pages:
            self._finalize_pending(page)
            if state is not None and state.main_navigation:
                self._end_attempt(state.page, state.attempt_generation)
            return True
        if state is None:
            self._finalize_pending(page)
            return False
        if state.page is not page or not state.same_origin:
            self._finalize_pending(page)
            if state.main_navigation:
                self._end_attempt(state.page, state.attempt_generation)
            return False
        superseded = state.generation < self._page_generations.get(page, 0)
        if superseded:
            self._seal_inactive_pending(page)
            return True
        if state.main_navigation:
            self._end_attempt(page, state.attempt_generation)

        pending = self._pending_aborts.get(page)
        if (
            pending is None
            or pending.generation != state.generation
            or pending.commit_generation != state.attempt_generation
            or (pending.last_event != event - 1 and pending.commit_generation is None)
        ):
            self._finalize_pending(page)
            pending = _PendingAbortBatch(
                state.generation,
                state.attempt_generation,
                event,
                [],
            )
            self._pending_aborts[page] = pending
        pending.last_event = event
        pending.failures.append((request.url, failure))
        return True

    def take_unexplained_aborts(self) -> list[tuple[str, str]]:
        for page in tuple(self._pending_aborts):
            self._finalize_pending(page)
        unexplained = self._unexplained_aborts
        self._unexplained_aborts = []
        return unexplained

    def _finalize_pending(self, page: _PageLifecycle) -> None:
        pending = self._pending_aborts.pop(page, None)
        if pending is not None:
            self._unexplained_aborts.extend(pending.failures)

    def _seal_inactive_pending(self, page: _PageLifecycle) -> None:
        pending = self._pending_aborts.get(page)
        attempt = self._active_attempts.get(page)
        if pending is not None and (
            pending.commit_generation is None
            or attempt is None
            or pending.commit_generation != attempt.generation
        ):
            self._finalize_pending(page)

    def _next_event(self, page: _PageLifecycle) -> int:
        event = self._page_events.setdefault(page, 0) + 1
        self._page_events[page] = event
        return event

    def _resolve_renderer_navigation_batch(
        self,
        page: _PageLifecycle,
        generation: int,
        event: int,
    ) -> None:
        pending = self._pending_aborts.get(page)
        if (
            pending is not None
            and pending.generation == generation
            and pending.last_event == event - 1
        ):
            self._pending_aborts.pop(page)
            return
        self._finalize_pending(page)

    def _resolve_commit_batch(
        self,
        page: _PageLifecycle,
        generation: int,
    ) -> None:
        pending = self._pending_aborts.get(page)
        if pending is not None and pending.commit_generation == generation:
            self._pending_aborts.pop(page)
            return
        self._finalize_pending(page)

    def _end_attempt(
        self,
        page: _PageLifecycle,
        generation: int | None,
    ) -> None:
        attempt = self._active_attempts.get(page)
        if attempt is not None and attempt.generation == generation:
            self._active_attempts.pop(page)

    def _is_same_origin(self, url: str) -> bool:
        parsed = urlsplit(url)
        return self._allowed_origin == f"{parsed.scheme}://{parsed.netloc}"


def pytest_configure(config: pytest.Config) -> None:
    for url, (fixture_name, provenance_name, content_type) in OFFLINE_ROUTE_FIXTURES.items():
        fixture = PUBLIC_FIXTURE_ROOT / fixture_name
        provenance = PUBLIC_FIXTURE_ROOT / provenance_name
        validate_public_fixture(fixture, provenance)
        _OFFLINE_ROUTE_BYTES[url] = (fixture.read_bytes(), content_type)
    runtime = get_test_runtime(Path(__file__).resolve().parent)
    if config.option.basetemp is None:
        config.option.basetemp = runtime.worker(current_worker_id()).browser / "pytest"
    for marker, description in (
        ("core", "bounded release-critical local Playwright coverage"),
        ("full", "additional deterministic local Playwright coverage"),
        ("remote_readonly", "explicit deployed read-only coverage"),
        ("remote_mutation", "explicit isolated-development synthetic mutation"),
        ("live_email", "explicit controlled-recipient live email smoke"),
        ("live_provider", "explicit provider/webhook integration smoke"),
    ):
        config.addinivalue_line("markers", f"{marker}: {description}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.path)).as_posix()
        markers = {marker.name for marker in item.iter_markers()}
        local = markers & LOCAL_MARKERS
        safety = markers & SAFETY_MARKERS
        if "/playwright_tests/" in f"/{path}" and len(local) != 1:
            raise pytest.UsageError(
                f"{item.nodeid}: every Playwright test requires exactly one core/full marker"
            )
        if len(safety) > 1:
            raise pytest.UsageError(
                f"{item.nodeid}: a test cannot combine multiple live/remote safety markers"
            )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    safety = sorted({marker.name for marker in item.iter_markers()} & SAFETY_MARKERS)
    if safety:
        authorize_from_environment(safety[0])


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def _deny_external_network() -> Generator[None]:
    from django.conf import settings

    if settings.configured:
        settings.EMAIL_BACKEND = SYNTHETIC_EMAIL_BACKEND
    with NetworkGuard():
        yield


@pytest.fixture(scope="session")
def live_server(request: pytest.FixtureRequest):
    from django.conf import settings

    server = live_server_helper.LiveServer("127.0.0.1")
    settings.TEST_RUNTIME.record_server(origin=server.url, worker_id=current_worker_id())
    yield server
    server.stop()


@pytest.fixture
def browser_context_args() -> dict[str, object]:
    return {
        "accept_downloads": False,
        "color_scheme": "light",
        "locale": "en-US",
        "reduced_motion": "reduce",
        "service_workers": "block",
        "timezone_id": "UTC",
    }


@pytest.fixture
def context(
    browser: Browser,
    browser_context_args: dict[str, object],
    request: pytest.FixtureRequest,
) -> Generator[BrowserContext]:
    from django.conf import settings

    safety_marker = next(
        iter({marker.name for marker in request.node.iter_markers()} & SAFETY_MARKERS),
        None,
    )
    authorization: SafetyAuthorization | None = None
    allowed_origin: str | None = None
    if safety_marker:
        authorization = authorize_from_environment(safety_marker)
        allowed_origin = authorization.base_url
    elif "live_server" in request.fixturenames:
        allowed_origin = request.getfixturevalue("live_server").url

    worker = settings.TEST_RUNTIME.worker(current_worker_id())
    artifact_name = _bounded_artifact_name(request.node.nodeid)
    trace_path = worker.artifacts / f"{artifact_name}.zip"
    screenshot_path = worker.artifacts / f"{artifact_name}.png"
    del browser_context_args
    context = browser.new_context(
        accept_downloads=False,
        color_scheme="light",
        locale="en-US",
        reduced_motion="reduce",
        service_workers="block",
        timezone_id="UTC",
    )
    denied_urls: set[str] = set()
    failures: list[str] = []
    cancellation_tracker = NavigationCancellationTracker(allowed_origin)
    context.add_init_script(_frozen_clock_script())

    def route_request(route: Route) -> None:
        url = route.request.url
        parsed = urlsplit(url)
        if parsed.scheme in {"about", "blob", "data"}:
            route.continue_()
            return
        if authorization is None and url in _OFFLINE_ROUTE_BYTES:
            body, content_type = _OFFLINE_ROUTE_BYTES[url]
            route.fulfill(
                status=200,
                content_type=content_type,
                body=body,
                headers={"access-control-allow-origin": "*"},
            )
            return
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if allowed_origin and origin == allowed_origin:
            if authorization is not None:
                authorization.authorize_request(route.request.method, url)
            route.continue_()
            return
        denied_urls.add(url)
        failures.append(
            f"external request denied: {parsed.hostname or 'unknown host'}{parsed.path}"
        )
        route.abort("blockedbyclient")

    context.route("**/*", route_request)
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    expected_statuses: set[int] = set()

    def configure_page(page) -> None:
        original_screenshot = page.screenshot
        original_close = page.close

        def screenshot_without_private_values(*args, **kwargs):
            page.evaluate(_SCREENSHOT_REDACTION_SCRIPT)
            try:
                return original_screenshot(*args, **kwargs)
            finally:
                page.evaluate(
                    "globalThis.__dtcRestoreScreenshotValues?.(); "
                    "delete globalThis.__dtcRestoreScreenshotValues;"
                )

        page.screenshot = screenshot_without_private_values

        def navigation_with_lifecycle(original_navigation):
            def navigate(*args, **kwargs):
                cancellation_tracker.begin_navigation(page)
                try:
                    result = original_navigation(*args, **kwargs)
                except BaseException:
                    cancellation_tracker.navigation_failed(page)
                    raise
                else:
                    cancellation_tracker.navigation_committed(page)
                    return result

            return navigate

        for navigation_method in (
            "goto",
            "reload",
            "go_back",
            "go_forward",
            "set_content",
        ):
            original_navigation = getattr(page, navigation_method)
            setattr(
                page,
                navigation_method,
                navigation_with_lifecycle(original_navigation),
            )

        def close_with_lifecycle(*args, **kwargs):
            cancellation_tracker.begin_page_close(page)
            return original_close(*args, **kwargs)

        page.close = close_with_lifecycle

        def record_response(response) -> None:
            parsed = urlsplit(response.url)
            if allowed_origin == f"{parsed.scheme}://{parsed.netloc}" and _expected_local_response(
                request.node.name,
                response.status,
                parsed.path,
            ):
                expected_statuses.add(response.status)

        def record_console(message) -> None:
            if message.type != "error":
                return
            test_name = request.node.name.partition("[")[0]
            if (
                test_name == "test_returning_learner_keeps_one_account_across_site_and_courses"
                and _EMAIL_PREFERENCES_CONSOLE_RE.fullmatch(message.text)
            ):
                return
            status = _STATUS_CONSOLE_RE.search(message.text)
            if status and int(status.group(1)) in expected_statuses:
                return
            if not _expected_denial_message(message.text, denied_urls):
                failures.append(f"console error: {message.text}")

        def reject_dialog(dialog) -> None:
            failures.append(f"unexpected dialog: {dialog.type}")
            dialog.dismiss()

        def record_failed_request(failed) -> None:
            tracked_navigation_abort = cancellation_tracker.track_abort(
                page,
                failed,
                failed.failure,
            )
            if failed.url in denied_urls:
                return
            if _expected_offline_asset_abort(failed.url, failed.failure):
                return
            if _expected_local_request_failure(request.node.name, failed.url):
                return
            if tracked_navigation_abort:
                return
            failures.append(
                f"request failed: {urlsplit(failed.url).path} ({failed.failure or 'unknown'})"
            )

        page.on("pageerror", lambda error: failures.append(f"page error: {error}"))
        page.on("console", record_console)
        page.on(
            "request",
            lambda started: cancellation_tracker.request_started(page, started),
        )
        page.on("response", record_response)
        page.on("requestfinished", cancellation_tracker.request_finished)
        page.on("requestfailed", record_failed_request)
        page.on(
            "framenavigated",
            lambda frame: (
                cancellation_tracker.navigation_committed(page)
                if frame == page.main_frame
                else None
            ),
        )
        page.on("close", lambda _closed_page: cancellation_tracker.begin_page_close(page))
        page.on("dialog", reject_dialog)
        page.on("download", lambda download: failures.append("unexpected download"))

    context.on("page", configure_page)
    try:
        yield context
    finally:
        for failed_url, failure in cancellation_tracker.take_unexplained_aborts():
            failures.append(f"request failed: {urlsplit(failed_url).path} ({failure})")
        cancellation_tracker.begin_context_close()
        report = getattr(request.node, "rep_call", None)
        if report is not None and report.failed:
            pages = context.pages
            if pages:
                pages[0].screenshot(path=screenshot_path, full_page=True)
        context.tracing.stop(path=trace_path)
        redact_trace_emails(trace_path)
        context.close()
        for root in owned_publication_roots(settings.BASE_DIR, worker.artifacts):
            scan_artifacts(root, canaries=artifact_canaries())
        if failures:
            pytest.fail("; ".join(sorted(set(failures))))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    from django.conf import settings
    from django.db import connections

    del exitstatus
    if settings.configured and hasattr(settings, "TEST_RUNTIME"):
        try:
            worker = settings.TEST_RUNTIME.worker(current_worker_id())
            for root in owned_publication_roots(settings.BASE_DIR, worker.artifacts):
                scan_artifacts(root, canaries=artifact_canaries())
        except CaptureSafetyError as error:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_line(
                    f"ERROR: protected value detected in owned test artifacts ({error})",
                    red=True,
                )
        finally:
            connections.close_all()
            settings.TEST_RUNTIME.cleanup()


def _bounded_artifact_name(nodeid: str) -> str:
    stem = "".join(character if character.isalnum() else "-" for character in nodeid)
    stem = "-".join(part for part in stem.split("-") if part)[:72]
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
    return f"{stem or 'test'}-{digest}"


_SCREENSHOT_REDACTION_SCRIPT = r"""
    (() => {
      const email = /[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9-]+(?:\.[a-z0-9-]+)+/gi;
      const restores = [];
      const redact = (value) => value.replace(email, "[REDACTED]");
      const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const parent = node.parentElement;
        if (parent && ["SCRIPT", "STYLE"].includes(parent.tagName)) continue;
        const replacement = redact(node.nodeValue || "");
        if (replacement !== node.nodeValue) {
          const current = node;
          const original = current.nodeValue;
          restores.push(() => { current.nodeValue = original; });
          current.nodeValue = replacement;
        }
      }
      for (const element of document.querySelectorAll("input, textarea")) {
        const replacement = redact(element.value || "");
        if (replacement !== element.value) {
          const original = element.value;
          restores.push(() => { element.value = original; });
          element.value = replacement;
        }
      }
      globalThis.__dtcRestoreScreenshotValues = () => {
        for (const restore of restores.reverse()) restore();
      };
    })();
"""


def _frozen_clock_script() -> str:
    milliseconds = int(DEFAULT_FROZEN_AT.timestamp() * 1000)
    return f"""
        (() => {{
          const NativeDate = Date;
          const fixed = {milliseconds};
          class FrozenDate extends NativeDate {{
            constructor(...args) {{ super(...(args.length ? args : [fixed])); }}
            static now() {{ return fixed; }}
          }}
          Object.setPrototypeOf(FrozenDate, NativeDate);
          globalThis.Date = FrozenDate;
        }})();
    """


def _expected_denial_message(message: str, denied_urls: set[str]) -> bool:
    if "ERR_BLOCKED_BY_CLIENT" not in message and "ERR_FAILED" not in message:
        return False
    return bool(denied_urls)


def _expected_local_response(node_name: str, status: int, path: str) -> bool:
    test_name = node_name.partition("[")[0]
    return any(
        expected_status == status and pattern.fullmatch(path)
        for pattern, expected_status in EXPECTED_LOCAL_RESPONSES.get(test_name, ())
    )


def _expected_local_request_failure(node_name: str, url: str) -> bool:
    test_name = node_name.partition("[")[0]
    path = urlsplit(url).path
    return any(
        pattern.fullmatch(path)
        for pattern, _expected_status in EXPECTED_LOCAL_RESPONSES.get(test_name, ())
    )


def _expected_offline_asset_abort(url: str, failure: str | None) -> bool:
    return failure == "net::ERR_ABORTED" and url in _OFFLINE_ROUTE_BYTES
