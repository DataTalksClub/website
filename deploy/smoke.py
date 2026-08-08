from __future__ import annotations

import argparse
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.source_policy import analytics_runtime_violations
from deploy.contracts import ReleaseContractError, validate_source_sha

SANDBOX_ORIGIN = "https://web.dtcdev.click"
ROBOTS_VALUE = "noindex, nofollow"
ROBOTS_BODY = b"User-agent: *\nDisallow: /\n"
SITEMAP_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n'
)
_STATIC_REFERENCE = re.compile(r'(?:href|src)="(?P<path>/static/[^"?#]+)')


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseContractError("expected a JSON response") from error
        if not isinstance(value, dict):
            raise ReleaseContractError("expected a JSON object")
        return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _request(origin: str, path: str) -> Response:
    request = urllib.request.Request(f"{origin}{path}", method="GET")
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        raw = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        raw = error
    with raw:
        return Response(
            status=raw.status,
            headers={name.lower(): value for name, value in raw.headers.items()},
            body=raw.read(),
        )


def _assert_noindex(response: Response, path: str) -> None:
    if response.headers.get("x-robots-tag") != ROBOTS_VALUE:
        raise ReleaseContractError(f"{path} lacks exact X-Robots-Tag")


def _assert_private(response: Response, path: str) -> None:
    directives = {
        item.strip().lower() for item in response.headers.get("cache-control", "").split(",")
    }
    if not {"private", "no-store"}.issubset(directives):
        raise ReleaseContractError(f"{path} must be private, no-store")
    if "public" in directives or any(
        item.startswith("s-maxage=") and item != "s-maxage=0" for item in directives
    ):
        raise ReleaseContractError(f"{path} permits shared caching")


def _assert_status(response: Response, expected: int, path: str) -> None:
    if response.status != expected:
        raise ReleaseContractError(f"{path} returned {response.status}, expected {expected}")


def validate_origin(origin: str) -> str:
    normalized = origin.rstrip("/")
    if normalized != SANDBOX_ORIGIN:
        raise ReleaseContractError(f"deployed smoke is restricted to {SANDBOX_ORIGIN}")
    return normalized


def verify_health(origin: str, source_sha: str) -> None:
    origin = validate_origin(origin)
    validate_source_sha(source_sha)
    live = _request(origin, "/health/live")
    _assert_status(live, 200, "/health/live")
    _assert_noindex(live, "/health/live")
    if live.json() != {"status": "ok", "version": source_sha}:
        raise ReleaseContractError("liveness does not report the exact source SHA")

    ready = _request(origin, "/health/ready")
    _assert_status(ready, 200, "/health/ready")
    _assert_noindex(ready, "/health/ready")
    payload = ready.json()
    if payload.get("status") != "ready":
        raise ReleaseContractError("readiness status is not ready")
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        raise ReleaseContractError("readiness checks are missing")
    for name in ("configuration", "database", "migrations"):
        if not isinstance(checks.get(name), dict) or checks[name].get("status") != "ok":
            raise ReleaseContractError(f"readiness {name} check is not successful")


def run_http_smoke(
    origin: str,
    source_sha: str,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    origin = validate_origin(origin)
    verify_health(origin, source_sha)

    home = _request(origin, "/")
    _assert_status(home, 200, "/")
    _assert_noindex(home, "/")
    html = home.body.decode("utf-8")
    for expected in (
        "Welcome to DataTalks.Club",
        "The place to talk about data",
        "Global online community of data science professionals, ML engineers, and AI practitioners",
    ):
        if expected not in html:
            raise ReleaseContractError(f"home page lacks expected content: {expected}")
    canonical = '<link rel="canonical" href="https://datatalks.club/">'
    if html.count(canonical) != 1 or html.count('rel="canonical"') != 1:
        raise ReleaseContractError("home page production canonical differs")
    if "Learn data skills. For free. Together." in html:
        raise ReleaseContractError("home page regressed to adopted course discovery")
    lowered = html.lower()
    if "traceback" in lowered or "page not found" in lowered or "debug=true" in lowered:
        raise ReleaseContractError("home page contains debug or 404 output")
    if analytics_runtime_violations(html=html, request_urls=(), cookie_names=()):
        raise ReleaseContractError("home page contains production analytics")
    static_match = _STATIC_REFERENCE.search(html)
    if static_match is None:
        raise ReleaseContractError("home page lacks an application static reference")

    mapped = _request(origin, "/unified/")
    _assert_status(mapped, 200, "/unified/")
    _assert_noindex(mapped, "/unified/")
    mapped_html = mapped.body.decode("utf-8")
    if mapped_html.count(canonical) != 1 or mapped_html.count('rel="canonical"') != 1:
        raise ReleaseContractError("explicit production canonical differs")
    if "The place to talk about data" not in mapped_html:
        raise ReleaseContractError("unified compatibility page lacks the main-site identity")

    courses = _request(origin, "/courses/")
    _assert_status(courses, 200, "/courses/")
    _assert_noindex(courses, "/courses/")
    courses_html = courses.body.decode("utf-8")
    if "Learn data skills. For free. Together." not in courses_html:
        raise ReleaseContractError("course discovery lacks expected content")
    if "The place to talk about data" in courses_html:
        raise ReleaseContractError("course discovery regressed to the main-site home")
    if 'rel="canonical"' in courses_html:
        raise ReleaseContractError("adopted course discovery has a guessed canonical")

    studio = _request(origin, "/studio/")
    if studio.status not in {301, 302, 303, 307, 308}:
        raise ReleaseContractError("/studio/ did not initially redirect")
    _assert_noindex(studio, "/studio/")
    _assert_private(studio, "/studio/")
    location = studio.headers.get("location", "")
    parsed = urllib.parse.urlparse(location)
    if parsed.netloc and f"{parsed.scheme}://{parsed.netloc}" != origin:
        raise ReleaseContractError("/studio/ redirected away from the sandbox origin")
    if parsed.path != "/accounts/login/" or parsed.query != "next=%2Fstudio%2F":
        raise ReleaseContractError("/studio/ redirect target is not the exact sign-in route")

    login = _request(origin, "/accounts/login/?next=%2Fstudio%2F")
    _assert_status(login, 200, "/accounts/login/")
    _assert_noindex(login, "/accounts/login/")
    _assert_private(login, "/accounts/login/")
    if "Sign In" not in login.body.decode("utf-8"):
        raise ReleaseContractError("sign-in page lacks the expected heading")

    admin = _request(origin, "/api/v1/admin/health")
    _assert_status(admin, 401, "/api/v1/admin/health")
    _assert_noindex(admin, "/api/v1/admin/health")
    _assert_private(admin, "/api/v1/admin/health")
    if "location" in admin.headers:
        raise ReleaseContractError("anonymous admin API health redirected")
    if admin.headers.get("www-authenticate") != "Bearer":
        raise ReleaseContractError("anonymous admin API health lacks the Bearer challenge")
    request_id = admin.headers.get("x-request-id", "")
    if not request_id or admin.json() != {
        "error": {
            "code": "authentication_required",
            "message": "Valid Bearer authentication is required.",
            "request_id": request_id,
        }
    }:
        raise ReleaseContractError("anonymous admin API health payload differs")

    missing_path = "/__dtc_deployed_smoke_missing__"
    missing = _request(origin, missing_path)
    _assert_status(missing, 404, missing_path)
    _assert_noindex(missing, missing_path)
    missing_html = missing.body.decode("utf-8", errors="replace").lower()
    if any(marker in missing_html for marker in ("traceback", "technical 404", "debug=true")):
        raise ReleaseContractError("missing page exposes debug output")
    if 'rel="canonical"' in missing_html:
        raise ReleaseContractError("missing page has a canonical")

    robots = _request(origin, "/robots.txt")
    _assert_status(robots, 200, "/robots.txt")
    _assert_noindex(robots, "/robots.txt")
    if robots.headers.get("content-type") != "text/plain; charset=utf-8":
        raise ReleaseContractError("robots content type differs")
    if robots.body != ROBOTS_BODY:
        raise ReleaseContractError("robots body differs")

    sitemap = _request(origin, "/sitemap.xml")
    _assert_status(sitemap, 200, "/sitemap.xml")
    _assert_noindex(sitemap, "/sitemap.xml")
    if sitemap.headers.get("content-type") != "application/xml; charset=utf-8":
        raise ReleaseContractError("sitemap content type differs")
    if sitemap.body != SITEMAP_BODY:
        raise ReleaseContractError("sitemap body differs")

    static_path = static_match.group("path")
    static_response = _request(origin, static_path)
    _assert_status(static_response, 200, "static asset")
    _assert_noindex(static_response, "static asset")

    evidence: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "read_only",
        "origin": origin,
        "source_sha": source_sha,
        "checks": [
            {"path": "/health/live", "status": 200, "noindex": True, "exact_version": True},
            {
                "path": "/health/ready",
                "status": 200,
                "noindex": True,
                "configuration": True,
                "database": True,
                "migrations": True,
            },
            {
                "path": "/",
                "status": 200,
                "noindex": True,
                "main_site_identity": True,
                "explicit_canonical": True,
            },
            {
                "path": "/unified/",
                "status": 200,
                "noindex": True,
                "explicit_canonical": True,
            },
            {
                "path": "/courses/",
                "status": 200,
                "noindex": True,
                "course_discovery": True,
                "canonical_absent": True,
            },
            {
                "path": "/studio/",
                "status": studio.status,
                "noindex": True,
                "private_no_store": True,
                "exact_login_redirect": True,
            },
            {
                "path": "/accounts/login/",
                "status": 200,
                "noindex": True,
                "private_no_store": True,
            },
            {
                "path": "/api/v1/admin/health",
                "status": 401,
                "noindex": True,
                "private_no_store": True,
                "anonymous_denial": True,
                "bearer_challenge": True,
                "request_id": True,
            },
            {"path": missing_path, "status": 404, "noindex": True, "debug_safe": True},
            {"path": "/robots.txt", "status": 200, "noindex": True, "exact_body": True},
            {"path": "/sitemap.xml", "status": 200, "noindex": True, "empty": True},
            {"path_group": "static", "status": 200, "noindex": True},
            {"runtime_group": "analytics", "denied": True},
        ],
    }
    if evidence_path is not None:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = evidence_path.with_suffix(evidence_path.suffix + ".new")
        temporary_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        temporary_path.replace(evidence_path)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only sandbox HTTP smoke")
    parser.add_argument("--base-url", default=SANDBOX_ORIGIN)
    parser.add_argument("--source-sha", required=True)
    arguments = parser.parse_args()
    run_http_smoke(arguments.base_url, arguments.source_sha)


if __name__ == "__main__":
    main()
