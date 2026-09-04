from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path

GUNICORN_ACCESS_LOG_FORMAT = (
    '%(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s '
    'request_id="%({x-request-id}o)s" correlation_id="%({x-correlation-id}o)s"'
)
# The format keeps the request path, and Relay's recipient links carry an opaque
# per-recipient token in the path.  The logger class below redacts that segment,
# so the entrypoint must keep using it for the format above to stay safe.
GUNICORN_LOGGER_CLASS = "core.gunicorn_logging.RecipientTokenSafeLogger"
GUNICORN_WEB_TIMEOUT_SECONDS = 90
ANALYTICS_HOSTS = ("googletagmanager.com", "google-analytics.com")
ANALYTICS_COOKIE_PREFIXES = ("_ga", "_gid", "_gat", "_gcl_")
_ANALYTICS_ID = re.compile(r"(?:GTM-[A-Z0-9]{6,}|G-[A-Z0-9]{8,}|UA-[0-9]+-[0-9]+)")
_RETIRED_HOST = re.compile(r"(?<![A-Za-z0-9.-])dev\.dtcdev\.click(?![A-Za-z0-9.-])")
_LINK_TAG = re.compile(r"<link\b[^>]{0,4096}>", re.IGNORECASE)
_CANONICAL_REL = re.compile(
    r"\brel\s*=\s*(?:[\"'][^\"']*\bcanonical\b[^\"']*[\"']|[^\s\"'=<>`]*canonical[^\s\"'=<>`]*)",
    re.IGNORECASE,
)
_GUNICORN_WEB_CASE = re.compile(r"(?ms)^[ \t]*web\)[ \t]*\n(?P<body>.*?)(?=^[ \t]*;;[ \t]*$)")
_GUNICORN_COMMAND = re.compile(
    r"^[ \t]*exec[ \t]+uv[ \t]+run[ \t]+--no-sync[ \t]+gunicorn(?:[ \t]+.*)?$"
)


class SourcePolicyError(ValueError):
    pass


def _gunicorn_command_lines(source: str) -> list[str]:
    return [line for line in source.splitlines() if _GUNICORN_COMMAND.fullmatch(line)]


def _gunicorn_web_command(source: str) -> str:
    web_cases = list(_GUNICORN_WEB_CASE.finditer(source))
    if len(web_cases) != 1:
        raise SourcePolicyError("gunicorn_web_command_mismatch")

    commands = _gunicorn_command_lines(web_cases[0].group("body"))
    if len(commands) != 1:
        raise SourcePolicyError("gunicorn_web_command_mismatch")
    return commands[0]


def parse_gunicorn_timeout(source: str) -> int:
    """Parse the fixed timeout from the target-owned ``web`` command."""

    command = _gunicorn_web_command(source)
    if _gunicorn_command_lines(source) != [command]:
        raise SourcePolicyError("gunicorn_web_command_mismatch")
    if source.count("--timeout") != 1 or command.count("--timeout") != 1:
        raise SourcePolicyError("gunicorn_timeout_mismatch")

    match = re.search(r"(?<![A-Za-z0-9_-])--timeout[ \t]+([^ \t]+)", command)
    if match is None or match.group(1) != str(GUNICORN_WEB_TIMEOUT_SECONDS):
        raise SourcePolicyError("gunicorn_timeout_mismatch")

    try:
        arguments = shlex.split(command, comments=False, posix=True)
    except ValueError as error:
        raise SourcePolicyError("gunicorn_timeout_mismatch") from error

    timeout_indexes = [index for index, argument in enumerate(arguments) if argument == "--timeout"]
    if len(timeout_indexes) != 1:
        raise SourcePolicyError("gunicorn_timeout_mismatch")
    timeout_index = timeout_indexes[0]
    if (
        timeout_index + 1 >= len(arguments)
        or arguments[timeout_index + 1] != str(GUNICORN_WEB_TIMEOUT_SECONDS)
        or any(argument.startswith("--timeout=") for argument in arguments)
    ):
        raise SourcePolicyError("gunicorn_timeout_mismatch")
    return int(arguments[timeout_index + 1])


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
    expected_logger_assignment = f"GUNICORN_LOGGER_CLASS='{GUNICORN_LOGGER_CLASS}'"
    expected_logger_argument = '--logger-class "$GUNICORN_LOGGER_CLASS"'
    if (
        source.count(expected_logger_assignment) != 1
        or source.count("GUNICORN_LOGGER_CLASS=") != 1
        or source.count(expected_logger_argument) != 1
        or source.count("--logger-class") != 1
    ):
        raise SourcePolicyError("gunicorn_logger_class_mismatch")
    parse_gunicorn_timeout(source)


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
