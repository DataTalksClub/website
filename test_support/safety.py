from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast
from urllib.parse import urlsplit

SAFETY_MARKERS = frozenset({"remote_readonly", "remote_mutation", "live_email", "live_provider"})
LOCAL_MARKERS = frozenset({"core", "full"})
DEVELOPMENT_HOSTS = frozenset({"web.dtcdev.click"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
_RECIPIENT_REFERENCE_RE = re.compile(r"^[A-Z][A-Z0-9_]{7,63}$")
DJANGO_SAFETY_ATTRIBUTE = "dtc_test_safety"
_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


class TestSafetyError(RuntimeError):
    """An opt-in test did not prove its complete pre-connection authority."""

    __test__ = False


def django_test_safety(marker: str) -> Callable[[_CallableT], _CallableT]:
    """Declare and enforce safety metadata for Django/service unittest code."""

    if marker not in SAFETY_MARKERS:
        raise ValueError("unknown Django/service test safety marker")

    def decorate(target: _CallableT) -> _CallableT:
        @wraps(target)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            authorize_from_environment(marker)
            return target(*args, **kwargs)

        setattr(guarded, DJANGO_SAFETY_ATTRIBUTE, marker)
        return cast(_CallableT, guarded)

    return decorate


def django_test_safety_marker(target: object) -> str | None:
    marker = getattr(target, DJANGO_SAFETY_ATTRIBUTE, None)
    if marker is None:
        return None
    if marker not in SAFETY_MARKERS:
        raise TestSafetyError("Django/service test has unknown safety metadata")
    return cast(str, marker)


@dataclass(frozen=True, slots=True)
class SafetyAuthorization:
    marker: str
    base_url: str
    hostname: str
    namespace: str
    recipient_reference: str | None = None

    def authorize_request(self, method: str, url: str) -> None:
        target = _parse_development_url(url)
        base = urlsplit(self.base_url)
        if (target.scheme, target.hostname, target.port) != (
            base.scheme,
            base.hostname,
            base.port,
        ):
            raise TestSafetyError("remote request left the exact approved origin")
        if self.marker == "remote_readonly" and method.upper() not in SAFE_METHODS:
            raise TestSafetyError("remote_readonly denies state-changing methods")


def authorize_from_environment(marker: str) -> SafetyAuthorization:
    if marker not in SAFETY_MARKERS:
        raise TestSafetyError("unknown safety marker")
    selected = os.environ.get("DTC_TEST_SAFETY_COMMAND", "")
    if selected != marker:
        raise TestSafetyError("safety marker requires its exact opt-in command")
    if os.environ.get("DTC_TEST_TARGET_CLASS") != "isolated_development":
        raise TestSafetyError("remote tests require the isolated_development target class")
    namespace = os.environ.get("DTC_TEST_REMOTE_NAMESPACE", "")
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise TestSafetyError("remote tests require a bounded synthetic namespace")
    base_url = os.environ.get("DTC_TEST_BASE_URL", "")
    parsed = _parse_development_url(base_url)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise TestSafetyError("remote base URL must be one exact origin")

    recipient_reference = None
    if marker == "live_email":
        recipient_reference = os.environ.get("DTC_TEST_SMOKE_RECIPIENT_REFERENCE", "")
        if not _RECIPIENT_REFERENCE_RE.fullmatch(recipient_reference):
            raise TestSafetyError("live_email requires an allowlisted recipient reference")
        recipient = os.environ.get(recipient_reference, "")
        if not recipient or "@" not in recipient or recipient.endswith("@example.invalid"):
            raise TestSafetyError("live_email recipient secret is missing or synthetic")
    return SafetyAuthorization(
        marker=marker,
        base_url=base_url.rstrip("/"),
        hostname=parsed.hostname or "",
        namespace=namespace,
        recipient_reference=recipient_reference,
    )


def _parse_development_url(value: str):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise TestSafetyError("remote URL is malformed") from error
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname not in DEVELOPMENT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise TestSafetyError("remote URL is not on the closed development allowlist")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise TestSafetyError("IP-literal remote targets are forbidden")
    return parsed
