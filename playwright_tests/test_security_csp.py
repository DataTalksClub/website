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
