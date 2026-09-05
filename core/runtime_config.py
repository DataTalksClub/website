"""Resolve registered operational settings at runtime, without a restart.

A value an operator tunes -- a mailer endpoint, a bucket, a timeout, a feature
switch -- used to be readable only from the environment, so changing one meant
editing a task definition and replacing every running container.  This module
is the other path: the same value, declared once in the registry, resolved on
every use from the database first.

The order is deliberate and always the same::

    database row  ->  environment variable  ->  django.conf.settings  ->  default

The database wins because that is the surface an operator can reach through the
admin API.  The environment comes next because that is how a task definition
boots before anyone has written a row.  ``django.conf.settings`` is the value
this process actually started with, and the definition default is the last
honest answer.  Nothing here can invent a value that code did not declare.

**Secrets are not in this path.**  ``core.configuration`` refuses to register a
setting whose key, env var, settings attribute, validation metadata or value
looks secret-bearing, and that refusal is not relaxed here.  API keys, webhook
secrets and the homework answer keyring stay in the environment.  So do the
settings a process needs before it can reach a database at all -- the secret
key, the database URL, the allowed hosts, the environment name -- because a
setting that must be read to open the connection cannot be stored behind it.

Caching and how a write reaches the other containers
----------------------------------------------------

Resolving through the database on every call would put a query in front of
every template render, so resolved values are cached in the process.  A
per-process cache alone would be wrong: the site runs several containers and
several workers, so a value written through one of them has to reach the rest.

Each cache entry is therefore tagged with a *stamp* -- the row count and the
newest ``updated_at`` of the settings table.  Any write to any setting, from any
process, moves the stamp.  Each process re-reads the stamp at most once every
``STAMP_TTL_SECONDS`` (one cheap aggregate), and drops its whole cache when the
stamp it reads differs from the one it cached under.

A write made through ``core.settings_batch`` drops this process's cache on
commit, so the consequence is worth stating plainly: a write is visible to the
process that made it immediately, and to every other process within
``STAMP_TTL_SECONDS``.  It is a bounded propagation delay, not a restart.

When there is no settings table at all
--------------------------------------

System checks run before ``migrate``, and management commands run against
databases that may not exist yet.  A tunable is not worth failing either over,
so a database error demotes the database layer for that call: the environment
answers, and nothing is cached, so the first call after the table appears reads
it.  This is a fallback, never a silent substitute for a row that exists.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from time import monotonic as _monotonic
from typing import Any

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.db import Error as DatabaseLayerError
from django.db.models import Count, Max

from core.configuration import (
    InvalidOperationalSetting,
    OperationalSettingDefinition,
    UnknownOperationalSetting,
    registered_operational_settings,
    validate_operational_setting_value,
)
from core.idempotency import JsonValue
from core.models import OperationalSetting

#: How long a process may reuse a stamp before it looks again.  Small enough
#: that an operator watching the site sees their change land, large enough that
#: a busy page render does not add a query per setting read.
STAMP_TTL_SECONDS = 5.0

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", ""})

_lock = threading.Lock()
_cache: dict[str, JsonValue] = {}
_cached_stamp: tuple[int, str] | None = None
_stamp_checked_at: float = 0.0


def _database_unreachable(error: BaseException) -> bool:
    """Whether the database layer simply could not answer this call.

    A real database error is the ordinary case: no configured connection, an
    unmigrated database, a closed socket.  The two message-matched cases are the
    test guards -- ``SimpleTestCase`` raises ``AssertionError`` and the pytest
    guard raises ``RuntimeError`` when a unit that declares it needs no database
    touches one.  They are recognised for the same reason the database errors
    are: a runtime without a usable settings table serves the value the process
    booted with instead of failing every read.  Anything else propagates.
    """

    if isinstance(error, (DatabaseLayerError, ImproperlyConfigured)):
        return True
    if isinstance(error, AssertionError):
        return "Database queries to" in str(error)
    if isinstance(error, RuntimeError):
        return "Database access not allowed" in str(error)
    return False


#: The four layers, named the way an operator reading the admin API sees them.
DATABASE_LAYER = "database"
ENVIRONMENT_LAYER = "environment"
SETTINGS_LAYER = "settings"
DEFAULT_LAYER = "code_default"


class RuntimeSettingUnavailable(RuntimeError):
    """The registry declares the key, but no layer could produce a valid value."""


@dataclass(frozen=True, slots=True)
class RuntimeSettingResolution:
    """One resolved value and the layer that actually produced it."""

    key: str
    value: JsonValue
    layer: str


def _stamped_definitions() -> dict[str, OperationalSettingDefinition]:
    return {
        definition.key: definition
        for definition in registered_operational_settings()
        if definition.cache_policy == "stamped"
    }


def _read_stamp(*, using: str) -> tuple[int, str]:
    """One aggregate that changes whenever any setting row is written."""

    aggregate = OperationalSetting.objects.using(using).aggregate(
        rows=Count("id"),
        latest=Max("updated_at"),
    )
    latest = aggregate["latest"]
    return (aggregate["rows"] or 0, latest.isoformat() if latest is not None else "")


def _current_stamp(*, using: str, now: float) -> tuple[int, str] | None:
    """The stamp this process should resolve against, re-read at most per TTL."""

    global _cached_stamp, _stamp_checked_at

    if _cached_stamp is not None and now - _stamp_checked_at < STAMP_TTL_SECONDS:
        return _cached_stamp

    try:
        stamp = _read_stamp(using=using)
    except Exception as error:
        if not _database_unreachable(error):
            raise
        # No usable settings table -- a system check before ``migrate``, a
        # command run without a database, a unit that declares it needs none.
        # Resolve from the environment and do not cache, so the first call after
        # the table becomes readable sees it.
        _cache.clear()
        _cached_stamp = None
        _stamp_checked_at = 0.0
        return None
    _stamp_checked_at = now
    if stamp != _cached_stamp:
        # Somebody wrote a setting -- in this process or another one.  Which key
        # changed is not worth tracking; the whole cache is small and rebuilding
        # it is one query per key actually read afterwards.
        _cache.clear()
        _cached_stamp = stamp
    return stamp


def _coerce_text(definition: OperationalSettingDefinition, raw: str) -> JsonValue:
    """Read one environment-shaped string as the type the definition declares."""

    value_type = definition.value_type
    if value_type == OperationalSetting.ValueType.BOOLEAN:
        token = raw.strip().casefold()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        raise InvalidOperationalSetting(
            f"setting {definition.key} requires a boolean environment value"
        )
    if value_type == OperationalSetting.ValueType.INTEGER:
        try:
            return int(raw.strip())
        except ValueError as error:
            raise InvalidOperationalSetting(
                f"setting {definition.key} requires an integer environment value"
            ) from error
    if value_type == OperationalSetting.ValueType.STRING_LIST:
        return [item.strip() for item in raw.split(",") if item.strip()]
    if value_type == OperationalSetting.ValueType.JSON_OBJECT:
        try:
            decoded = json.loads(raw)
        except ValueError as error:
            raise InvalidOperationalSetting(
                f"setting {definition.key} requires a JSON object environment value"
            ) from error
        return decoded
    return raw


def _coerce(definition: OperationalSettingDefinition, value: Any) -> JsonValue:
    """Normalize one layer's value, reading strings in the declared type."""

    if isinstance(value, str) and definition.value_type != OperationalSetting.ValueType.STRING:
        value = _coerce_text(definition, value)
    elif isinstance(value, str):
        value = _coerce_text(definition, value)
    return validate_operational_setting_value(definition.key, value)


def _from_environment(definition: OperationalSettingDefinition) -> JsonValue | None:
    if not definition.env_var:
        return None
    raw = os.environ.get(definition.env_var)
    if raw is None:
        return None
    return _coerce(definition, raw)


def _from_django_settings(definition: OperationalSettingDefinition) -> JsonValue | None:
    attribute = definition.settings_attr
    if not attribute or not hasattr(django_settings, attribute):
        return None
    value = getattr(django_settings, attribute)
    if value is None:
        return None
    # Settings modules hold real Python objects -- a Path, a float timeout, a
    # tuple of hosts -- so widen the few shapes that map onto a declared type
    # without inventing a conversion the registry would not accept anyway.
    if definition.value_type == OperationalSetting.ValueType.INTEGER and isinstance(value, float):
        value = int(value)
    elif definition.value_type == OperationalSetting.ValueType.STRING and not isinstance(
        value, str
    ):
        value = str(value)
    elif definition.value_type == OperationalSetting.ValueType.STRING_LIST and isinstance(
        value, tuple
    ):
        value = list(value)
    return _coerce(definition, value)


def _resolve_uncached(
    definition: OperationalSettingDefinition,
    *,
    using: str,
) -> tuple[JsonValue, str]:
    try:
        stored = OperationalSetting.objects.using(using).filter(key=definition.key).first()
    except Exception as error:
        if not _database_unreachable(error):
            raise
        stored = None
    if stored is not None:
        if stored.value_type != definition.value_type:
            raise InvalidOperationalSetting(
                f"stored setting {definition.key} has type {stored.value_type}, "
                f"expected {definition.value_type}"
            )
        return validate_operational_setting_value(definition.key, stored.value), DATABASE_LAYER

    for layer, name in (
        (_from_environment, ENVIRONMENT_LAYER),
        (_from_django_settings, SETTINGS_LAYER),
    ):
        try:
            value = layer(definition)
        except InvalidOperationalSetting:
            # A deployment that boots with an unreadable value should not take
            # the site down over a tunable; fall through to the next layer and
            # let the definition default be the floor.
            continue
        if value is not None:
            return value, name

    return validate_operational_setting_value(definition.key, definition.default), DEFAULT_LAYER


def _stamped_definition(key: str) -> OperationalSettingDefinition:
    definition = _stamped_definitions().get(key)
    if definition is None:
        raise UnknownOperationalSetting(
            f"operational setting {key} is not registered for runtime resolution"
        )
    return definition


def get_setting(key: str, *, using: str = "default") -> JsonValue:
    """Resolve one registered setting: database, environment, settings, default.

    The value is cached per process and invalidated by the settings table's
    stamp, so a write made anywhere is picked up here within
    ``STAMP_TTL_SECONDS`` and never needs a restart.
    """

    definition = _stamped_definition(key)

    # The clock is bound at import on purpose.  A caller that mocks
    # ``time.monotonic`` to drive its own deadline must not also drain this
    # cache's sense of time; the two have nothing to do with each other.
    now = _monotonic()
    with _lock:
        stamp = _current_stamp(using=using, now=now)
        if stamp is not None and key in _cache:
            return _cache[key]

    # Resolve outside the lock: this touches the database, and holding the lock
    # across a query would serialize every reader behind the slowest one.
    value, _layer = _resolve_uncached(definition, using=using)

    if stamp is not None:
        with _lock:
            _cache[key] = value
    return value


def get_int_setting(key: str, *, using: str = "default") -> int:
    """One integer setting, typed at the boundary so callers need no cast.

    The registry has already refused any value that is not an integer of the
    declared kind, so this narrows a type rather than converting one.
    """

    value = get_setting(key, using=using)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidOperationalSetting(f"setting {key} did not resolve to an integer")
    return value


def get_str_setting(key: str, *, using: str = "default") -> str:
    value = get_setting(key, using=using)
    if not isinstance(value, str):
        raise InvalidOperationalSetting(f"setting {key} did not resolve to a string")
    return value


def get_bool_setting(key: str, *, using: str = "default") -> bool:
    value = get_setting(key, using=using)
    if not isinstance(value, bool):
        raise InvalidOperationalSetting(f"setting {key} did not resolve to a boolean")
    return value


def resolve_runtime_setting(key: str, *, using: str = "default") -> RuntimeSettingResolution:
    """Resolve one setting *and name the layer it came from*, without the cache.

    The admin API reads this so an operator can see not just the effective value
    but why it is the effective value -- whether the row they are about to write
    would shadow an environment variable or only the definition default.
    """

    definition = _stamped_definition(key)
    value, layer = _resolve_uncached(definition, using=using)
    return RuntimeSettingResolution(key=key, value=value, layer=layer)


def runtime_setting_snapshot(*, using: str = "default") -> dict[str, JsonValue]:
    """Every runtime-resolved setting and its effective value."""

    return {key: get_setting(key, using=using) for key in sorted(_stamped_definitions())}


def reset_runtime_settings_cache() -> None:
    """Drop this process's cache and stamp.

    Tests rebuild the settings table between cases, and a stamp cached from a
    previous case would otherwise look unchanged.  Production code does not
    need this: the stamp already carries writes between processes.
    """

    global _cached_stamp, _stamp_checked_at
    with _lock:
        _cache.clear()
        _cached_stamp = None
        _stamp_checked_at = 0.0
