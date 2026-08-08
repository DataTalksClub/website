from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

GUNICORN_ACCESS_LOG_FORMAT = (
    '%(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s '
    'request_id="%({x-request-id}o)s" correlation_id="%({x-correlation-id}o)s"'
)
ANALYTICS_HOSTS = ("googletagmanager.com", "google-analytics.com")
ANALYTICS_COOKIE_PREFIXES = ("_ga", "_gid", "_gat", "_gcl_")
_ANALYTICS_ID = re.compile(r"(?:GTM-[A-Z0-9]{6,}|G-[A-Z0-9]{8,}|UA-[0-9]+-[0-9]+)")
_RETIRED_HOST = re.compile(r"(?<![A-Za-z0-9.-])dev\.dtcdev\.click(?![A-Za-z0-9.-])")
_LINK_TAG = re.compile(r"<link\b[^>]{0,4096}>", re.IGNORECASE)
_CANONICAL_REL = re.compile(
    r"\brel\s*=\s*(?:[\"'][^\"']*\bcanonical\b[^\"']*[\"']|[^\s\"'=<>`]*canonical[^\s\"'=<>`]*)",
    re.IGNORECASE,
)


class SourcePolicyError(ValueError):
    pass


def validate_gunicorn_entrypoint(source: str) -> None:
    expected_assignment = f"GUNICORN_ACCESS_LOG_FORMAT='{GUNICORN_ACCESS_LOG_FORMAT}'"
    expected_argument = '--access-logformat "$GUNICORN_ACCESS_LOG_FORMAT"'
    if (
        source.count(expected_assignment) != 1
        or source.count("GUNICORN_ACCESS_LOG_FORMAT=") != 1
        or source.count(expected_argument) != 1
        or source.count("--access-logformat") != 1
    ):
        raise SourcePolicyError("gunicorn_access_log_format_mismatch")
    if any(atom in GUNICORN_ACCESS_LOG_FORMAT for atom in ("%(r)s", "%(q)s", "%(h)s")):
        raise SourcePolicyError("gunicorn_access_log_format_contains_unsafe_atom")


def _analytics_text_violations(text: str) -> set[str]:
    lowered = text.casefold()
    violations = {f"analytics_host:{host}" for host in ANALYTICS_HOSTS if host in lowered}
    violations.update(f"analytics_id:{match.group(0)}" for match in _ANALYTICS_ID.finditer(text))
    return violations


def analytics_runtime_violations(
    *, html: str, request_urls: Iterable[str], cookie_names: Iterable[str]
) -> tuple[str, ...]:
    violations = _analytics_text_violations(html)
    for url in request_urls:
        lowered = url.casefold()
        if any(host in lowered for host in ANALYTICS_HOSTS):
            violations.add("analytics_request")
    for name in cookie_names:
        lowered = name.casefold()
        if any(
            lowered == prefix or lowered.startswith(prefix) for prefix in ANALYTICS_COOKIE_PREFIXES
        ):
            violations.add("analytics_cookie")
    return tuple(sorted(violations))


def validate_development_source(files: Mapping[str, str]) -> None:
    violations: list[str] = []
    for name, source in files.items():
        if _RETIRED_HOST.search(source):
            violations.append(f"retired_hostname:{name}")
        if any(
            "https://web.dtcdev.click" in tag.group(0).casefold()
            and _CANONICAL_REL.search(tag.group(0))
            for tag in _LINK_TAG.finditer(source)
        ):
            violations.append(f"development_canonical:{name}")
        if _is_rendering_or_runtime_config(name):
            violations.extend(
                f"{violation}:{name}" for violation in _analytics_text_violations(source)
            )
    if violations:
        raise SourcePolicyError(",".join(sorted(violations)))


def _is_rendering_or_runtime_config(name: str) -> bool:
    path = Path(name)
    if path.name == ".env.example" or name == "deploy/task_definitions.py":
        return True
    if name.startswith("website/settings/"):
        return True
    return path.suffix in {".html", ".js"} and bool(
        {"templates", "static"}.intersection(path.parts)
    )
