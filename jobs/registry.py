from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from django_q.models import Schedule  # type: ignore[import-untyped]

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JobPayload = dict[str, JsonValue]
type JobHandler = Callable[[JobContext, JobPayload], None]

HANDLER_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
SCHEDULE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,99}$")
PAYLOAD_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
SENSITIVE_KEY_WORDS = frozenset(
    {
        "authorization",
        "body",
        "cookie",
        "credential",
        "email",
        "password",
        "secret",
        "token",
    }
)
SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "apikey",
        "authorization",
        "body",
        "cookie",
        "credential",
        "email",
        "password",
        "privatekey",
        "secret",
        "token",
    }
)
OPAQUE_IDENTIFIER_SUFFIXES = frozenset({"id", "ids", "uuid", "uuids"})
MAX_PAYLOAD_BYTES = 32_768
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_ITEMS = 256
MAX_STRING_LENGTH = 4_096
EMAIL_VALUE_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
BEARER_VALUE_PATTERN = re.compile(r"(?i)^\s*(bearer|basic)\s+\S+")
JWT_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
CREDENTIAL_VALUE_PATTERN = re.compile(r"^(AKIA|ASIA|gh[pousr]_|github_pat_)[A-Za-z0-9_-]+$")
CAMEL_CASE_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
OPAQUE_INTEGER_PATTERN = re.compile(r"^[0-9]{1,20}$")


class RegistryError(ValueError):
    """Raised when a handler, schedule, or payload is outside the code-owned contract."""


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: uuid.UUID
    operation_id: uuid.UUID | None
    request_id: str | None
    correlation_id: str | None
    attempt_count: int
    worker_id: str
    lease_token: uuid.UUID


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    key: str
    func: str
    schedule_type: str
    minutes: int | None = None
    cron: str | None = None
    repeats: int = -1
    args: tuple[JsonScalar, ...] = ()
    kwargs: Mapping[str, JsonScalar] | None = None

    def __post_init__(self) -> None:
        validate_schedule_definition(self)


_handlers: dict[str, JobHandler] = {}
_schedules: dict[str, ScheduleDefinition] = {}


def register_handler(name: str) -> Callable[[JobHandler], JobHandler]:
    if not HANDLER_PATTERN.fullmatch(name):
        raise RegistryError("invalid durable job handler name")

    def decorator(handler: JobHandler) -> JobHandler:
        existing = _handlers.get(name)
        if existing is not None and existing is not handler:
            raise RegistryError(f"durable job handler is already registered: {name}")
        _handlers[name] = handler
        return handler

    return decorator


def get_handler(name: str) -> JobHandler:
    try:
        return _handlers[name]
    except KeyError as exc:
        raise RegistryError("durable job handler is not registered") from exc


def registered_handler_names() -> tuple[str, ...]:
    return tuple(sorted(_handlers))


def register_schedule(definition: ScheduleDefinition) -> ScheduleDefinition:
    existing = _schedules.get(definition.key)
    if existing is not None and existing != definition:
        raise RegistryError(f"code-owned schedule is already registered: {definition.key}")
    _schedules[definition.key] = definition
    return definition


def registered_schedules() -> tuple[ScheduleDefinition, ...]:
    return tuple(_schedules[key] for key in sorted(_schedules))


def validate_schedule_definition(definition: ScheduleDefinition) -> None:
    if not definition.key.startswith("dtc:") or not SCHEDULE_PATTERN.fullmatch(definition.key):
        raise RegistryError("invalid code-owned schedule key")
    if not HANDLER_PATTERN.fullmatch(definition.func):
        raise RegistryError("invalid scheduled function path")
    if definition.schedule_type not in {choice for choice, _ in Schedule.TYPE}:
        raise RegistryError("invalid schedule type")
    if definition.schedule_type == Schedule.MINUTES:
        if definition.minutes is None or not 1 <= definition.minutes <= 32_767:
            raise RegistryError("minute schedule requires a bounded positive interval")
    elif definition.minutes is not None:
        raise RegistryError("minutes are valid only for minute schedules")
    if definition.schedule_type == Schedule.CRON:
        if not definition.cron:
            raise RegistryError("cron schedule requires an expression")
    elif definition.cron is not None:
        raise RegistryError("cron is valid only for cron schedules")
    if definition.repeats == 0 or definition.repeats < -1:
        raise RegistryError("schedule repeats must be -1 or a positive count")
    validate_payload(
        {
            "args": list(definition.args),
            "kwargs": dict(definition.kwargs or {}),
        }
    )


def validate_payload(payload: Mapping[str, object]) -> JobPayload:
    if not isinstance(payload, Mapping):
        raise RegistryError("durable job payload must be an object")
    normalized = _validate_mapping(payload, depth=0, counter=[0])
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise RegistryError("durable job payload is too large")
    return normalized


def _validate_mapping(value: Mapping[str, object], *, depth: int, counter: list[int]) -> JobPayload:
    if depth > MAX_PAYLOAD_DEPTH:
        raise RegistryError("durable job payload is too deeply nested")
    result: JobPayload = {}
    for key, item in value.items():
        counter[0] += 1
        if counter[0] > MAX_PAYLOAD_ITEMS:
            raise RegistryError("durable job payload has too many items")
        if not isinstance(key, str) or not PAYLOAD_KEY_PATTERN.fullmatch(key):
            raise RegistryError("durable job payload contains an invalid key")
        key_words = _normalized_key_words(key)
        protected = _is_protected_key(key_words)
        safe_identifier = protected and _is_proven_opaque_identifier(key_words, item)
        if protected and not safe_identifier:
            raise RegistryError("durable job payload contains a protected field")
        result[key] = _validate_value(item, depth=depth + 1, counter=counter)
    return result


def _normalized_key_words(key: str) -> tuple[str, ...]:
    separated = CAMEL_CASE_BOUNDARY_PATTERN.sub("_", key)
    return tuple(part for part in re.split(r"[^a-z0-9]+", separated.casefold()) if part)


def _is_protected_key(key_words: tuple[str, ...]) -> bool:
    compact = "".join(key_words)
    return any(part in SENSITIVE_KEY_WORDS for part in key_words) or any(
        fragment in compact for fragment in SENSITIVE_KEY_FRAGMENTS
    )


def _is_proven_opaque_identifier(key_words: tuple[str, ...], value: object) -> bool:
    if not key_words:
        return False
    suffix = key_words[-1]
    if suffix in OPAQUE_IDENTIFIER_SUFFIXES:
        values = (
            value if suffix in {"ids", "uuids"} and isinstance(value, list | tuple) else (value,)
        )
        return bool(values) and all(_is_opaque_id_value(item) for item in values)
    return False


def _is_opaque_id_value(value: object) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return value >= 0
    if not isinstance(value, str):
        return False
    if OPAQUE_INTEGER_PATTERN.fullmatch(value):
        return True
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return False
    return value in {str(parsed), parsed.hex}


def _validate_value(value: object, *, depth: int, counter: list[int]) -> JsonValue:
    if depth > MAX_PAYLOAD_DEPTH:
        raise RegistryError("durable job payload is too deeply nested")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RegistryError("durable job payload contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise RegistryError("durable job payload contains an oversized string")
        if (
            "://" in value
            or EMAIL_VALUE_PATTERN.fullmatch(value)
            or BEARER_VALUE_PATTERN.match(value)
            or JWT_VALUE_PATTERN.fullmatch(value)
            or CREDENTIAL_VALUE_PATTERN.fullmatch(value)
        ):
            raise RegistryError("durable job payload contains a protected value")
        return value
    if isinstance(value, Mapping):
        return _validate_mapping(value, depth=depth, counter=counter)
    if isinstance(value, list | tuple):
        result: list[JsonValue] = []
        for item in value:
            counter[0] += 1
            if counter[0] > MAX_PAYLOAD_ITEMS:
                raise RegistryError("durable job payload has too many items")
            result.append(_validate_value(item, depth=depth + 1, counter=counter))
        return result
    raise RegistryError("durable job payload must contain only JSON values")
