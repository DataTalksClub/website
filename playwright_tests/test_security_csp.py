from __future__ import annotations

import pytest

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]


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


def test_admin_login_uses_non_alpine_unfold_surface_under_strict_csp(
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
    policy = response.headers.get("content-security-policy", "")
    assert "'unsafe-eval'" not in policy

    scripts = strict_csp_page.locator("script[src]").evaluate_all(
        "(nodes) => nodes.map((node) => node.src)"
    )
    assert any("/unfold/js/htmx/htmx.js" in script for script in scripts)
    assert any("/unfold/js/chart/chart.js" in script for script in scripts)
    assert not any("/unfold/js/alpine/" in script for script in scripts)
    assert not any(script.endswith("/unfold/js/app.js") for script in scripts)

    assert strict_csp_page.locator("#login-form").is_visible()
    assert strict_csp_page.locator("#modal-overlay").count() == 0
    assert strict_csp_page.locator("#command-results").count() == 0
    for marker in (
        "Unfold 0.103",
        "Available shortcuts",
        "Open command tool",
        "Toggle sidebar",
    ):
        assert strict_csp_page.get_by_text(marker, exact=True).count() == 0
    assert page_errors == []
