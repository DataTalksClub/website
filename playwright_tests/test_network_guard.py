from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from conftest import NavigationCancellationTracker

pytestmark = pytest.mark.full

ORIGIN = "http://127.0.0.1:8000"


@dataclass(eq=False)
class FakePage:
    main_frame: object = field(default_factory=object)


@dataclass(eq=False)
class FakeRequest:
    url: str
    frame: object
    method: str = "GET"
    resource_type: str = "document"
    navigation: bool = False

    def is_navigation_request(self) -> bool:
        return self.navigation


def start_navigation(
    tracker: NavigationCancellationTracker,
    page: FakePage,
    path: str,
) -> FakeRequest:
    navigation = FakeRequest(
        f"{ORIGIN}{path}",
        page.main_frame,
        navigation=True,
    )
    tracker.request_started(page, navigation)
    return navigation


@pytest.mark.parametrize(
    ("path", "method", "resource_type"),
    [
        ("/static/new-layout.css", "GET", "stylesheet"),
        ("/static/new-font.woff2", "GET", "font"),
        ("/static/new-navigation.js", "GET", "script"),
        ("/accounts/settings/timezone/", "POST", "fetch"),
    ],
)
def test_same_origin_requests_cancelled_by_rapid_navigation_are_expected(
    path: str,
    method: str,
    resource_type: str,
) -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    initial_navigation = start_navigation(tracker, page, "/initial")
    tracker.navigation_committed(page)
    tracker.request_finished(initial_navigation)
    request = FakeRequest(
        f"{ORIGIN}{path}",
        page.main_frame,
        method=method,
        resource_type=resource_type,
    )
    tracker.request_started(page, request)

    # Playwright can report cancelled subresources before it emits the request
    # event for the navigation started by page.goto(). The API intent is the
    # earlier lifecycle signal in that ordering.
    tracker.begin_navigation(page)

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")


def test_main_frame_request_is_also_a_navigation_lifecycle_signal() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    request = FakeRequest(f"{ORIGIN}/static/app.js", page.main_frame)
    tracker.request_started(page, request)

    start_navigation(tracker, page, "/next")

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")


def test_unrelated_same_origin_abort_remains_reported() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    start_navigation(tracker, page, "/initial")
    tracker.navigation_committed(page)
    request = FakeRequest(f"{ORIGIN}/static/app.js", page.main_frame)
    tracker.request_started(page, request)

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")
    assert tracker.take_unexplained_aborts() == [(request.url, "net::ERR_ABORTED")]


def test_later_api_navigation_does_not_hide_unrelated_abort() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    request = FakeRequest(f"{ORIGIN}/static/app.js", page.main_frame)
    tracker.request_started(page, request)
    assert tracker.track_abort(page, request, "net::ERR_ABORTED")

    tracker.begin_navigation(page)

    assert tracker.take_unexplained_aborts() == [(request.url, "net::ERR_ABORTED")]


def test_renderer_navigation_signal_resolves_preceding_abort_batch() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    request = FakeRequest(f"{ORIGIN}/static/app.js", page.main_frame)
    tracker.request_started(page, request)
    assert tracker.track_abort(page, request, "net::ERR_ABORTED")

    start_navigation(tracker, page, "/next")

    assert tracker.take_unexplained_aborts() == []


def test_failed_renderer_navigation_does_not_authorize_later_fetch_abort() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    failed_navigation = start_navigation(tracker, page, "/navigation-that-aborts")
    assert tracker.track_abort(page, failed_navigation, "net::ERR_ABORTED")

    unrelated_fetch = FakeRequest(
        f"{ORIGIN}/api/unrelated",
        page.main_frame,
        method="POST",
        resource_type="fetch",
    )
    tracker.request_started(page, unrelated_fetch)
    assert tracker.track_abort(page, unrelated_fetch, "net::ERR_ABORTED")

    assert tracker.take_unexplained_aborts() == [
        (failed_navigation.url, "net::ERR_ABORTED"),
        (unrelated_fetch.url, "net::ERR_ABORTED"),
    ]


def test_non_abort_navigation_failure_ends_attempt_before_later_fetch() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    failed_navigation = start_navigation(tracker, page, "/navigation-that-fails")
    assert not tracker.track_abort(page, failed_navigation, "net::ERR_FAILED")

    unrelated_fetch = FakeRequest(
        f"{ORIGIN}/api/unrelated",
        page.main_frame,
        method="POST",
        resource_type="fetch",
    )
    tracker.request_started(page, unrelated_fetch)
    assert tracker.track_abort(page, unrelated_fetch, "net::ERR_ABORTED")

    assert tracker.take_unexplained_aborts() == [(unrelated_fetch.url, "net::ERR_ABORTED")]


def test_finished_navigation_request_ends_uncommitted_attempt() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    finished_navigation = start_navigation(tracker, page, "/finished-without-commit")
    tracker.request_finished(finished_navigation)

    unrelated_fetch = FakeRequest(
        f"{ORIGIN}/api/unrelated",
        page.main_frame,
        method="POST",
        resource_type="fetch",
    )
    tracker.request_started(page, unrelated_fetch)
    assert tracker.track_abort(page, unrelated_fetch, "net::ERR_ABORTED")

    assert tracker.take_unexplained_aborts() == [(unrelated_fetch.url, "net::ERR_ABORTED")]


def test_adjacent_successor_resolves_failed_renderer_navigation() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    superseded = start_navigation(tracker, page, "/superseded")
    assert tracker.track_abort(page, superseded, "net::ERR_ABORTED")

    start_navigation(tracker, page, "/successor")

    assert tracker.take_unexplained_aborts() == []


def test_navigation_abort_after_successor_start_is_generation_scoped() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    superseded = start_navigation(tracker, page, "/superseded")
    start_navigation(tracker, page, "/successor")

    assert tracker.track_abort(page, superseded, "net::ERR_ABORTED")
    assert tracker.take_unexplained_aborts() == []


def test_later_successor_does_not_resolve_failed_renderer_navigation() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    failed_navigation = start_navigation(tracker, page, "/failed")
    assert tracker.track_abort(page, failed_navigation, "net::ERR_ABORTED")

    intervening = FakeRequest(f"{ORIGIN}/api/intervening", page.main_frame)
    tracker.request_started(page, intervening)
    tracker.request_finished(intervening)
    start_navigation(tracker, page, "/too-late")

    assert tracker.take_unexplained_aborts() == [(failed_navigation.url, "net::ERR_ABORTED")]


def test_adjacent_commit_resolves_failed_renderer_navigation() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    committed_navigation = start_navigation(tracker, page, "/committed")
    assert tracker.track_abort(page, committed_navigation, "net::ERR_ABORTED")

    tracker.navigation_committed(page)

    assert tracker.take_unexplained_aborts() == []


def test_late_commit_does_not_hide_navigation_or_unrelated_fetch_abort() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    failed_navigation = start_navigation(tracker, page, "/failed")
    assert tracker.track_abort(page, failed_navigation, "net::ERR_ABORTED")

    unrelated_fetch = FakeRequest(
        f"{ORIGIN}/api/unrelated",
        page.main_frame,
        method="POST",
        resource_type="fetch",
    )
    tracker.request_started(page, unrelated_fetch)
    assert tracker.track_abort(page, unrelated_fetch, "net::ERR_ABORTED")
    tracker.navigation_committed(page)

    assert tracker.take_unexplained_aborts() == [
        (failed_navigation.url, "net::ERR_ABORTED"),
        (unrelated_fetch.url, "net::ERR_ABORTED"),
    ]


def test_much_later_renderer_navigation_does_not_hide_unrelated_abort() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    unrelated = FakeRequest(f"{ORIGIN}/api/unrelated", page.main_frame)
    tracker.request_started(page, unrelated)
    assert tracker.track_abort(page, unrelated, "net::ERR_ABORTED")

    for index in range(100):
        completed = FakeRequest(
            f"{ORIGIN}/api/completed/{index}",
            page.main_frame,
            resource_type="fetch",
        )
        tracker.request_started(page, completed)
        tracker.request_finished(completed)

    start_navigation(tracker, page, "/much-later")

    assert tracker.take_unexplained_aborts() == [(unrelated.url, "net::ERR_ABORTED")]


def test_only_consecutive_same_generation_abort_batch_is_navigation_cancelled() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    requests = [
        FakeRequest(f"{ORIGIN}/static/one.js", page.main_frame),
        FakeRequest(f"{ORIGIN}/static/two.css", page.main_frame),
    ]
    for request in requests:
        tracker.request_started(page, request)
    for request in requests:
        assert tracker.track_abort(page, request, "net::ERR_ABORTED")

    start_navigation(tracker, page, "/immediate")

    assert tracker.take_unexplained_aborts() == []


def test_intervening_request_start_seals_abort_before_renderer_navigation() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    aborted = FakeRequest(f"{ORIGIN}/api/aborted", page.main_frame)
    tracker.request_started(page, aborted)
    assert tracker.track_abort(page, aborted, "net::ERR_ABORTED")

    intervening = FakeRequest(f"{ORIGIN}/api/intervening", page.main_frame)
    tracker.request_started(page, intervening)
    start_navigation(tracker, page, "/later")

    assert tracker.take_unexplained_aborts() == [(aborted.url, "net::ERR_ABORTED")]


def test_renderer_navigation_is_page_scoped() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    aborted_page = FakePage()
    navigating_page = FakePage()
    aborted = FakeRequest(f"{ORIGIN}/api/aborted", aborted_page.main_frame)
    tracker.request_started(aborted_page, aborted)
    assert tracker.track_abort(aborted_page, aborted, "net::ERR_ABORTED")

    start_navigation(tracker, navigating_page, "/other-page")

    assert tracker.take_unexplained_aborts() == [(aborted.url, "net::ERR_ABORTED")]


def test_other_page_commit_does_not_resolve_failed_navigation() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    failed_page = FakePage()
    other_page = FakePage()
    failed_navigation = start_navigation(tracker, failed_page, "/failed")
    assert tracker.track_abort(
        failed_page,
        failed_navigation,
        "net::ERR_ABORTED",
    )

    start_navigation(tracker, other_page, "/committed-elsewhere")
    tracker.navigation_committed(other_page)

    assert tracker.take_unexplained_aborts() == [(failed_navigation.url, "net::ERR_ABORTED")]


def test_genuine_same_origin_failure_is_not_hidden_by_navigation() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    start_navigation(tracker, page, "/initial")
    tracker.navigation_committed(page)
    request = FakeRequest(f"{ORIGIN}/static/app.js", page.main_frame)
    tracker.request_started(page, request)
    start_navigation(tracker, page, "/next")

    assert not tracker.track_abort(page, request, "net::ERR_FAILED")


def test_abort_during_navigation_is_resolved_by_adjacent_commit() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    tracker.begin_navigation(page)
    request = FakeRequest(f"{ORIGIN}/static/new-font.woff2", page.main_frame)
    tracker.request_started(page, request)

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")
    tracker.navigation_committed(page)
    assert tracker.take_unexplained_aborts() == []


def test_attempt_scoped_abort_remains_pending_until_that_attempt_commits() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    tracker.begin_navigation(page)
    request = FakeRequest(f"{ORIGIN}/static/new-font.woff2", page.main_frame)
    tracker.request_started(page, request)
    assert tracker.track_abort(page, request, "net::ERR_ABORTED")

    for index in range(100):
        completed = FakeRequest(
            f"{ORIGIN}/static/completed/{index}.js",
            page.main_frame,
        )
        tracker.request_started(page, completed)
        tracker.request_finished(completed)
    tracker.navigation_committed(page)

    assert tracker.take_unexplained_aborts() == []


def test_abort_after_commit_is_not_authorized_by_request_start_during_attempt() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    tracker.begin_navigation(page)
    request = FakeRequest(f"{ORIGIN}/api/unrelated", page.main_frame)
    tracker.request_started(page, request)
    tracker.navigation_committed(page)

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")
    assert tracker.take_unexplained_aborts() == [(request.url, "net::ERR_ABORTED")]


def test_duplicate_commit_does_not_authorize_abort_after_first_commit() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    tracker.begin_navigation(page)
    request = FakeRequest(f"{ORIGIN}/api/unrelated", page.main_frame)
    tracker.request_started(page, request)
    tracker.navigation_committed(page)
    assert tracker.track_abort(page, request, "net::ERR_ABORTED")

    tracker.navigation_committed(page)

    assert tracker.take_unexplained_aborts() == [(request.url, "net::ERR_ABORTED")]


def test_request_started_after_navigation_commit_remains_guarded() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    tracker.begin_navigation(page)
    tracker.navigation_committed(page)
    request = FakeRequest(f"{ORIGIN}/static/new-font.woff2", page.main_frame)
    tracker.request_started(page, request)

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")
    assert tracker.take_unexplained_aborts() == [(request.url, "net::ERR_ABORTED")]


def test_external_abort_is_not_hidden_by_navigation_or_context_close() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    start_navigation(tracker, page, "/initial")
    request = FakeRequest("https://example.com/tracker.js", page.main_frame)
    tracker.request_started(page, request)
    start_navigation(tracker, page, "/next")
    tracker.begin_context_close()

    assert not tracker.track_abort(page, request, "net::ERR_ABORTED")


@pytest.mark.parametrize("close_target", ["page", "context"])
def test_same_origin_abort_during_close_is_expected(close_target: str) -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    request = FakeRequest(f"{ORIGIN}/static/app.js", page.main_frame)
    tracker.request_started(page, request)
    if close_target == "page":
        tracker.begin_page_close(page)
    else:
        tracker.begin_context_close()

    assert tracker.track_abort(page, request, "net::ERR_ABORTED")
    assert tracker.take_unexplained_aborts() == []


@pytest.mark.parametrize("close_target", ["page", "context"])
def test_close_does_not_hide_abort_reported_before_close(close_target: str) -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    request = FakeRequest(f"{ORIGIN}/api/unrelated", page.main_frame)
    tracker.request_started(page, request)
    assert tracker.track_abort(page, request, "net::ERR_ABORTED")

    if close_target == "page":
        tracker.begin_page_close(page)
    else:
        tracker.begin_context_close()

    assert tracker.take_unexplained_aborts() == [(request.url, "net::ERR_ABORTED")]


def test_context_close_does_not_hide_failed_navigation_reported_before_close() -> None:
    tracker = NavigationCancellationTracker(ORIGIN)
    page = FakePage()
    failed_navigation = start_navigation(tracker, page, "/failed")
    assert tracker.track_abort(page, failed_navigation, "net::ERR_ABORTED")

    tracker.begin_context_close()

    assert tracker.take_unexplained_aborts() == [(failed_navigation.url, "net::ERR_ABORTED")]
