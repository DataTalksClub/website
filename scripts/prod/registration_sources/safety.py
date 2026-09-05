"""Fail-closed structural checks every provider export reader shares.

These are the guards that make reading an untrusted export file safe: bounded
sizes and entry counts, no symlink and no hidden entry, no archive member that
escapes its own directory, and a whole-source checksum the caller pins.  They
are provider-neutral so the two readers cannot drift into different ideas of
"safe", and they live beside the readers because reading a provider's file
format is ingestion work, not domain work.

Nothing here returns an attendee value: the mapping helpers carry only
canonical-event proposals, and every refusal is a bounded code.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from events.importers import CanonicalProposal, ProtectedSourceError

MAX_ARCHIVE_ENTRIES = 5_000
MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_EXPANSION_RATIO = 20
MAX_ROWS = 2_000_000


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def aggregate_checksum(
    *, provider: str, external_id: str, eligible: int, excluded: int, quarantined: int
) -> str:
    digest = hashlib.sha256(b"dtc-historical-aggregate-v1\0")
    for value in (provider, external_id, str(eligible), str(excluded), str(quarantined)):
        encoded = value.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _proposal(value: object) -> CanonicalProposal | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "repository",
        "revision",
        "source_key",
        "slug",
    }:
        raise ProtectedSourceError("invalid_mapping_bridge")
    fields = tuple(value[key] for key in ("repository", "revision", "source_key", "slug"))
    if any(not isinstance(item, str) or not item or len(item) > 512 for item in fields):
        raise ProtectedSourceError("invalid_mapping_bridge")
    return CanonicalProposal(*fields)


def _any_external_identifier(external_id: str) -> bool:
    return True


def mapping_evidence(
    *,
    mapping_bridge: Mapping[str, object],
    source_missing: Mapping[str, object],
    external_id_valid: Callable[[str], bool] = _any_external_identifier,
) -> tuple[dict[str, CanonicalProposal], dict[str, CanonicalProposal | None]]:
    """Validate the caller's explicit provider-event-to-canonical-event evidence.

    ``external_id_valid`` lets a reader whose provider identifiers have a known
    shape reject anything else; a reader whose identifiers are opaque accepts any
    bounded string.
    """

    bridge: dict[str, CanonicalProposal] = {}
    missing: dict[str, CanonicalProposal | None] = {}
    for external_id, value in mapping_bridge.items():
        if (
            not isinstance(external_id, str)
            or not external_id
            or len(external_id) > 2_048
            or not external_id_valid(external_id)
        ):
            raise ProtectedSourceError("invalid_mapping_bridge")
        proposal = _proposal(value)
        if proposal is None:
            raise ProtectedSourceError("invalid_mapping_bridge")
        bridge[external_id] = proposal
    for external_id, value in source_missing.items():
        if (
            not isinstance(external_id, str)
            or not external_id
            or len(external_id) > 512
            or not external_id_valid(external_id)
        ):
            raise ProtectedSourceError("invalid_mapping_bridge")
        missing[external_id] = _proposal(value)
    if set(bridge) & set(missing):
        raise ProtectedSourceError("invalid_mapping_bridge")
    return bridge, missing


def safe_path(path: Path, *, expected_kind: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise ProtectedSourceError("source_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ProtectedSourceError("source_symlink")
    if expected_kind == "file" and not resolved.is_file():
        raise ProtectedSourceError("source_not_file")
    if expected_kind == "directory" and not resolved.is_dir():
        raise ProtectedSourceError("source_not_directory")
    return resolved


def directory_checksum(root: Path, files: tuple[Path, ...]) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def checked_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            raise ProtectedSourceError("hidden_entry")
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ProtectedSourceError("source_unavailable") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ProtectedSourceError("source_symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise ProtectedSourceError("unsupported_entry")
        if path.suffix.casefold() not in {".csv", ".json"}:
            raise ProtectedSourceError("unsupported_entry")
        if metadata.st_size > MAX_ENTRY_BYTES:
            raise ProtectedSourceError("entry_too_large")
        files.append(path)
    if len(files) > MAX_ARCHIVE_ENTRIES:
        raise ProtectedSourceError("entry_count_exceeded")
    if len({path.name.casefold() for path in files}) != len(files):
        raise ProtectedSourceError("duplicate_entry")
    return tuple(files)


def validate_archive_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ProtectedSourceError("path_traversal")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ProtectedSourceError("path_traversal")
    if any(part.startswith(".") for part in path.parts):
        raise ProtectedSourceError("hidden_entry")
    if len(path.parts) != 1:
        raise ProtectedSourceError("unsafe_archive_structure")
    return path
