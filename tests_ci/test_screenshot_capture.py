from __future__ import annotations

import pytest

from ci.screenshot_capture import (
    PAGE_BODY_READY_TIMEOUT_MS,
    _assert_page_is_safe,
)


class FakeBody:
    def __init__(self, text: str) -> None:
        self.text = text
        self.timeout: int | None = None

    def inner_text(self, *, timeout: int) -> str:
        self.timeout = timeout
        return self.text


class FakePage:
    def __init__(self, *, body_text: str, fits_viewport: bool = True) -> None:
        self.body = FakeBody(body_text)
        self.fits_viewport = fits_viewport

    def locator(self, selector: str) -> FakeBody:
        assert selector == "body"
        return self.body

    def evaluate(self, _script: str) -> bool:
        return self.fits_viewport


def test_page_safety_uses_a_bounded_ten_second_timeout() -> None:
    page = FakePage(body_text="ready")

    _assert_page_is_safe(page, route_state="home", status=200)

    assert PAGE_BODY_READY_TIMEOUT_MS == 10_000
    assert page.body.timeout == PAGE_BODY_READY_TIMEOUT_MS


@pytest.mark.parametrize(
    ("body_text", "fits_viewport", "message"),
    [
        ("", True, "empty document"),
        ("Traceback (most recent call last): details", True, "debug error marker"),
        ("ready", False, "horizontal overflow"),
    ],
)
def test_page_safety_fails_closed_for_unsafe_render_output(
    body_text: str,
    fits_viewport: bool,
    message: str,
) -> None:
    page = FakePage(body_text=body_text, fits_viewport=fits_viewport)

    with pytest.raises(RuntimeError, match=message):
        _assert_page_is_safe(page, route_state="home", status=200)


@pytest.mark.parametrize(
    ("route_state", "status", "message"),
    [
        ("home", 500, "application route returned unexpected status 500"),
        ("not-found", 200, "not-found route returned unexpected status 200"),
    ],
)
def test_page_safety_rejects_unexpected_http_status(
    route_state: str,
    status: int,
    message: str,
) -> None:
    page = FakePage(body_text="ready")

    with pytest.raises(RuntimeError, match=message):
        _assert_page_is_safe(page, route_state=route_state, status=status)
