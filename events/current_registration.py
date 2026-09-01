"""Strict input contract for explicitly identified current-event counts.

This module contains no provider-row parsing and no database writes.  The input names
the exact provider identity accepted by the corresponding protected adapter and one
canonical Event source identity.  Nothing in the contract permits title/date matching
or attendee-level fields.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CURRENT_REGISTRATION_INPUT_SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
MAX_MAPPINGS = 100
_EVENTBRITE_ID = re.compile(r"^[0-9]{1,20}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


class CurrentRegistrationInputError(ValueError):
    """A bounded current-event mapping input failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ExplicitCurrentEventMapping:
    provider: str
    provider_event_identity: str
    canonical_repository: str
    canonical_revision: str
    canonical_source_key: str

    @property
    def canonical_identity(self) -> tuple[str, str, str]:
        return (
            self.canonical_repository,
            self.canonical_revision,
            self.canonical_source_key,
        )


@dataclass(frozen=True, slots=True)
class CurrentRegistrationInput:
    mapping_set_revision: int
    mappings: tuple[ExplicitCurrentEventMapping, ...]


def _text(value: Any, *, maximum: int, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise CurrentRegistrationInputError(code)
    return value


def _read_payload(path: Path) -> Any:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise CurrentRegistrationInputError("input_symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise CurrentRegistrationInputError("input_not_file")
        if metadata.st_size > MAX_INPUT_BYTES:
            raise CurrentRegistrationInputError("input_too_large")
        payload = path.read_text(encoding="utf-8")
    except CurrentRegistrationInputError:
        raise
    except (OSError, UnicodeError) as error:
        raise CurrentRegistrationInputError("input_unavailable") from error
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise CurrentRegistrationInputError("input_invalid_json") from error


def load_current_registration_input(path: Path) -> CurrentRegistrationInput:
    """Load and validate an explicit current-event source-to-canonical mapping file."""

    payload = _read_payload(path)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "mapping_set_revision",
        "mappings",
    }:
        raise CurrentRegistrationInputError("input_shape_invalid")
    if payload["schema_version"] != CURRENT_REGISTRATION_INPUT_SCHEMA_VERSION:
        raise CurrentRegistrationInputError("input_schema_version_invalid")
    mapping_set_revision = payload["mapping_set_revision"]
    if (
        isinstance(mapping_set_revision, bool)
        or not isinstance(mapping_set_revision, int)
        or mapping_set_revision < 1
    ):
        raise CurrentRegistrationInputError("mapping_set_revision_invalid")

    raw_mappings = payload["mappings"]
    if not isinstance(raw_mappings, list) or not 1 <= len(raw_mappings) <= MAX_MAPPINGS:
        raise CurrentRegistrationInputError("mappings_invalid")
    mappings: list[ExplicitCurrentEventMapping] = []
    provider_identities: set[tuple[str, str]] = set()
    canonical_identities: set[tuple[str, str, str]] = set()
    for raw in raw_mappings:
        if not isinstance(raw, dict) or set(raw) != {
            "provider",
            "provider_event_identity",
            "canonical_source",
        }:
            raise CurrentRegistrationInputError("mapping_shape_invalid")
        provider = raw["provider"]
        if provider not in {"luma", "eventbrite"}:
            raise CurrentRegistrationInputError("provider_invalid")
        provider_identity = _text(
            raw["provider_event_identity"],
            maximum=2_048,
            code="provider_event_identity_invalid",
        )
        if provider == "eventbrite" and _EVENTBRITE_ID.fullmatch(provider_identity) is None:
            raise CurrentRegistrationInputError("provider_event_identity_invalid")
        canonical = raw["canonical_source"]
        if not isinstance(canonical, dict) or set(canonical) != {
            "repository",
            "revision",
            "source_key",
        }:
            raise CurrentRegistrationInputError("canonical_source_invalid")
        repository = _text(canonical["repository"], maximum=255, code="canonical_source_invalid")
        revision = _text(canonical["revision"], maximum=64, code="canonical_source_invalid")
        source_key = _text(canonical["source_key"], maximum=512, code="canonical_source_invalid")
        if _REPOSITORY.fullmatch(repository) is None or _REVISION.fullmatch(revision) is None:
            raise CurrentRegistrationInputError("canonical_source_invalid")
        provider_key = (provider, provider_identity)
        canonical_key = (repository, revision, source_key)
        if provider_key in provider_identities:
            raise CurrentRegistrationInputError("mapping_duplicate")
        if canonical_key in canonical_identities:
            raise CurrentRegistrationInputError("canonical_source_duplicate")
        provider_identities.add(provider_key)
        canonical_identities.add(canonical_key)
        mappings.append(
            ExplicitCurrentEventMapping(
                provider=provider,
                provider_event_identity=provider_identity,
                canonical_repository=repository,
                canonical_revision=revision,
                canonical_source_key=source_key,
            )
        )
    mappings.sort(key=lambda item: (item.provider, item.provider_event_identity))
    return CurrentRegistrationInput(mapping_set_revision, tuple(mappings))
