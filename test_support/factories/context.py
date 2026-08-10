from __future__ import annotations

import hashlib
import json
import locale
import os
import random
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_UUID_NAMESPACE = uuid.UUID("a09655c8-cc9f-5ddd-82f9-1c4778656267")


def _canonical(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        values = [_canonical(item) for item in value]
        return sorted(values, key=lambda item: canonical_json_bytes(item))
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetimes must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(slots=True)
class FactoryContext:
    """All deterministic values enter factories through this explicit context.

    ``seed`` and ``frozen_at`` control logical values. ``execution_namespace`` is
    deliberately confined to physical identities used to avoid parallel collisions.
    """

    seed: str
    execution_namespace: str
    frozen_at: datetime
    _sequences: dict[str, int] = field(init=False, default_factory=dict, repr=False)
    _streams: dict[str, random.Random] = field(init=False, default_factory=dict, repr=False)
    _current_domain_scenarios: dict[tuple[str, str], Any] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not self.seed or len(self.seed) > 256:
            raise ValueError("factory seed must be a non-empty bounded string")
        if not _NAMESPACE_RE.fullmatch(self.execution_namespace):
            raise ValueError("execution namespace must be a bounded identifier")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ValueError("factory clock must be timezone-aware")
        self.frozen_at = self.frozen_at.astimezone(UTC)

    def next_sequence(self, factory_name: str) -> int:
        _validate_factory_name(factory_name)
        value = self._sequences.get(factory_name, 0) + 1
        self._sequences[factory_name] = value
        return value

    def random_stream(self, factory_name: str) -> random.Random:
        _validate_factory_name(factory_name)
        stream = self._streams.get(factory_name)
        if stream is None:
            digest = hashlib.sha256(
                f"dtc-factory-random-v1\0{self.seed}\0{factory_name}".encode()
            ).digest()
            stream = random.Random(int.from_bytes(digest, "big"))
            self._streams[factory_name] = stream
        return stream

    def logical_uuid(self, factory_name: str, logical_key: str) -> uuid.UUID:
        _validate_factory_name(factory_name)
        return uuid.uuid5(
            _UUID_NAMESPACE,
            f"dtc-factory-logical-v1/{self.seed}/{factory_name}/{logical_key}",
        )

    def physical_uuid(self, factory_name: str, logical_key: str) -> uuid.UUID:
        logical = self.logical_uuid(factory_name, logical_key)
        return uuid.uuid5(
            _UUID_NAMESPACE,
            f"dtc-factory-physical-v1/{logical}/{self.execution_namespace}",
        )

    def physical_key(self, factory_name: str, logical_key: str, *, length: int = 20) -> str:
        if not 8 <= length <= 64:
            raise ValueError("physical key length must be between 8 and 64")
        digest = hashlib.sha256(self.physical_uuid(factory_name, logical_key).bytes).hexdigest()
        return digest[:length]

    def physical_int(self, factory_name: str, logical_key: str) -> int:
        """Return a positive SQLite-safe integer key derived from the execution namespace."""

        digest = hashlib.sha256(self.physical_uuid(factory_name, logical_key).bytes).digest()
        return int.from_bytes(digest[:7], "big") + 1

    def synthetic_email(self, factory_name: str, logical_key: str) -> str:
        identity = self.physical_key(factory_name, logical_key, length=16)
        return f"synthetic-{identity}@example.invalid"

    def canonical_payload(self, value: Any) -> bytes:
        return canonical_json_bytes(value)

    def checksum(self, value: Any) -> str:
        return canonical_sha256(value)

    @contextmanager
    def frozen_environment(self) -> Iterator[None]:
        """Scope global process state for legacy code and restore it on every exit."""

        random_state = random.getstate()
        old_timezone = os.environ.get("TZ")
        old_locale = locale.setlocale(locale.LC_ALL)
        try:
            digest = hashlib.sha256(f"dtc-global-random-v1\0{self.seed}".encode()).digest()
            random.seed(int.from_bytes(digest, "big"))
            os.environ["TZ"] = "UTC"
            if hasattr(time, "tzset"):
                time.tzset()
            locale.setlocale(locale.LC_ALL, "C")
            yield
        finally:
            random.setstate(random_state)
            if old_timezone is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_timezone
            if hasattr(time, "tzset"):
                time.tzset()
            locale.setlocale(locale.LC_ALL, old_locale)


def _validate_factory_name(value: str) -> None:
    if not _NAMESPACE_RE.fullmatch(value):
        raise ValueError("factory name must be a bounded identifier")
