from __future__ import annotations

import pytest

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


def test_public_homepage_enforces_strict_csp_without_bypass(
    strict_csp_page,
    live_server,
) -> None:
    page_errors: list[str] = []
    strict_csp_page.on("pageerror", lambda error: page_errors.append(str(error)))

    response = strict_csp_page.goto(f"{live_server.url}/", wait_until="domcontentloaded")

    assert response is not None
    assert response.status == 200
    policy = response.headers.get("content-security-policy", "")
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "'unsafe-eval'" not in policy
    assert strict_csp_page.locator("body").is_visible()
    assert page_errors == []


def test_admin_login_does_not_load_csp_incompatible_unfold_overlays(
    strict_csp_page,
    live_server,
) -> None:
    page_errors: list[str] = []
    strict_csp_page.on("pageerror", lambda error: page_errors.append(str(error)))

    response = strict_csp_page.goto(
        f"{live_server.url}/admin/login/?next=/admin/",
        wait_until="networkidle",
    )

    assert response is not None
    assert response.status == 200
    assert "'unsafe-eval'" not in response.headers.get("content-security-policy", "")
    assert strict_csp_page.locator("#login-form").is_visible()
    assert strict_csp_page.get_by_text("Available shortcuts", exact=True).count() == 0
    assert strict_csp_page.get_by_text("Open command tool", exact=True).count() == 0
    assert strict_csp_page.get_by_text("Toggle sidebar", exact=True).count() == 0
    assert page_errors == []
