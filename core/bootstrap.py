"""Typed, fail-closed parsing for settings needed before Django can start."""

from __future__ import annotations

import re
from collections.abc import Collection
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn, cast

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

MAX_BOOTSTRAP_VALUE_LENGTH = 4096
MAX_BOOTSTRAP_LIST_ITEMS = 128
MAX_BOOTSTRAP_LIST_ITEM_LENGTH = 512
_INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)")
_MISSING = object()


class RuntimeEnvironment(StrEnum):
    """The environments with distinct bootstrap safety behavior."""

    LOCAL = "local"
    TEST = "test"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class BootstrapConfigurationError(ImproperlyConfigured):
    """A configuration error whose text never contains the rejected value."""


def _fail(name: str, reason: str) -> NoReturn:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        name = "BOOTSTRAP_SETTING"
    if not re.fullmatch(r"[a-z0-9-]+", reason):
        reason = "invalid-value"
    raise BootstrapConfigurationError(f"Invalid bootstrap setting {name}: {reason}") from None


def _text(name: str, value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(name, "expected-string")
    if len(value) > MAX_BOOTSTRAP_VALUE_LENGTH:
        _fail(name, "value-too-long")
    if not allow_empty and not value:
        _fail(name, "missing")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(name, "control-character")
    return value


def parse_bool(name: str, value: object, *, default: bool | object = _MISSING) -> bool:
    """Parse only canonical lower-case or numeric boolean literals."""

    if value is None:
        if default is _MISSING:
            _fail(name, "missing")
        if not isinstance(default, bool):
            _fail(name, "invalid-default")
        return default
    text = _text(name, value)
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    _fail(name, "expected-boolean")


def parse_int(
    name: str,
    value: object,
    *,
    default: int | object = _MISSING,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse a canonical base-ten integer and enforce optional inclusive bounds."""

    if value is None:
        if default is _MISSING:
            _fail(name, "missing")
        if isinstance(default, bool) or not isinstance(default, int):
            _fail(name, "invalid-default")
        result = default
    else:
        text = _text(name, value)
        if not _INTEGER_PATTERN.fullmatch(text):
            _fail(name, "expected-integer")
        try:
            result = int(text, 10)
        except ValueError:
            _fail(name, "expected-integer")
    if minimum is not None and result < minimum:
        _fail(name, "below-minimum")
    if maximum is not None and result > maximum:
        _fail(name, "above-maximum")
    return result


def parse_list(
    name: str,
    value: object,
    *,
    default: tuple[str, ...] = (),
    separator: str = ",",
    required: bool = False,
    unique: bool = True,
    maximum_items: int = MAX_BOOTSTRAP_LIST_ITEMS,
    maximum_item_length: int = MAX_BOOTSTRAP_LIST_ITEM_LENGTH,
) -> tuple[str, ...]:
    """Parse a bounded separator-delimited list without silently dropping empty items."""

    if len(separator) != 1 or separator.isspace():
        _fail(name, "invalid-separator")
    if maximum_items < 1 or maximum_item_length < 1:
        _fail(name, "invalid-bounds")
    if value is None:
        items = tuple(default)
    else:
        text = _text(name, value, allow_empty=True)
        if not text:
            items = ()
        else:
            split = text.split(separator)
            if any(not item.strip() for item in split):
                _fail(name, "empty-list-item")
            items = tuple(item.strip() for item in split)
    if required and not items:
        _fail(name, "missing")
    if len(items) > maximum_items:
        _fail(name, "too-many-items")
    for item in items:
        _text(name, item)
        if len(item) > maximum_item_length:
            _fail(name, "list-item-too-long")
    if unique and len(set(items)) != len(items):
        _fail(name, "duplicate-list-item")
    return items


def parse_environment(
    value: object,
    *,
    name: str = "DTC_ENVIRONMENT",
    default: RuntimeEnvironment = RuntimeEnvironment.LOCAL,
) -> RuntimeEnvironment:
    """Parse the exact runtime environment name used by bootstrap policy."""

    if value is None:
        return default
    text = _text(name, value)
    try:
        return RuntimeEnvironment(text)
    except ValueError:
        _fail(name, "unknown-environment")


def require_environment(
    actual: RuntimeEnvironment,
    expected: RuntimeEnvironment,
    *,
    name: str = "DTC_ENVIRONMENT",
) -> None:
    """Fail when a deployed settings module is paired with another environment."""

    if actual is not expected:
        _fail(name, "environment-mismatch")


def parse_secret(
    name: str,
    value: object,
    *,
    forbidden_values: Collection[str] = (),
    minimum_length: int = 50,
    minimum_unique_characters: int = 5,
) -> str:
    """Validate a bootstrap secret without ever including it in an error."""

    text = _text(name, value)
    stripped = text.strip()
    if not stripped:
        _fail(name, "missing")
    if minimum_length < 1 or minimum_unique_characters < 1:
        _fail(name, "invalid-bounds")
    if (
        text != stripped
        or stripped in forbidden_values
        or stripped.startswith("django-insecure-")
        or len(text) < minimum_length
        or len(set(text)) < minimum_unique_characters
    ):
        _fail(name, "unsafe-secret")
    return text


def parse_database_url(
    name: str,
    value: object,
    *,
    environment: RuntimeEnvironment,
    allow_sqlite: bool = False,
    conn_max_age: int = 60,
) -> dict[str, Any]:
    """Parse a database URL and require PostgreSQL for deployed environments.

    The rejected URL is deliberately omitted from all errors because it can contain a
    username and password.
    """

    text = _text(name, value)
    if any(character.isspace() for character in text):
        _fail(name, "invalid-database-url")
    try:
        database = cast(
            dict[str, Any],
            dj_database_url.parse(text, conn_max_age=conn_max_age, conn_health_checks=True),
        )
    except Exception:  # dj-database-url may wrap backend-specific parser errors.
        _fail(name, "invalid-database-url")
    engine = database.get("ENGINE")
    if not isinstance(engine, str) or not engine:
        _fail(name, "invalid-database-url")
    if (
        environment
        in {
            RuntimeEnvironment.DEVELOPMENT,
            RuntimeEnvironment.PRODUCTION,
        }
        and engine != "django.db.backends.postgresql"
    ):
        _fail(name, "deployed-requires-postgresql")
    if engine == "django.db.backends.sqlite3" and not allow_sqlite:
        _fail(name, "sqlite-not-allowed")
    return database


def database_configuration(
    *,
    environment: RuntimeEnvironment,
    database_url: object,
    sqlite_fallback: str | Path | None = None,
    name: str = "DATABASE_URL",
    conn_max_age: int = 60,
) -> dict[str, Any]:
    """Build a database setting while keeping SQLite an explicit local/test choice."""

    if database_url is not None:
        return parse_database_url(
            name,
            database_url,
            environment=environment,
            allow_sqlite=environment in {RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST},
            conn_max_age=conn_max_age,
        )
    if environment not in {RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST}:
        _fail(name, "missing")
    if sqlite_fallback is None:
        _fail(name, "missing")
    if not isinstance(sqlite_fallback, (str, Path)):
        _fail(name, "invalid-sqlite-path")
    path = Path(sqlite_fallback)
    if not str(path):
        _fail(name, "invalid-sqlite-path")
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": path}
