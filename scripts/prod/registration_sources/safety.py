"""Fail-closed structural checks every provider export reader shares.

These are the guards that make reading an untrusted export file safe: bounded
sizes and entry counts, no symlink and no hidden entry, and no archive member
that escapes its own directory. They
are provider-neutral so the two readers cannot drift into different ideas of
"safe", and they live beside the readers because reading a provider's file
format is ingestion work, not domain work.

Nothing here returns an attendee value, and every refusal is a bounded code.
"""

from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from scripts.prod.registrant_import import RegistrantImportError

MAX_ARCHIVE_ENTRIES = 5_000
MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_EXPANSION_RATIO = 20
MAX_ROWS = 2_000_000


def safe_path(path: Path, *, expected_kind: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise RegistrantImportError("source_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise RegistrantImportError("source_symlink")
    if expected_kind == "file" and not resolved.is_file():
        raise RegistrantImportError("source_not_file")
    if expected_kind == "directory" and not resolved.is_dir():
        raise RegistrantImportError("source_not_directory")
    return resolved


def checked_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            raise RegistrantImportError("hidden_entry")
        try:
            metadata = path.lstat()
        except OSError as error:
            raise RegistrantImportError("source_unavailable") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise RegistrantImportError("source_symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise RegistrantImportError("unsupported_entry")
        if path.suffix.casefold() not in {".csv", ".json"}:
            raise RegistrantImportError("unsupported_entry")
        if metadata.st_size > MAX_ENTRY_BYTES:
            raise RegistrantImportError("entry_too_large")
        files.append(path)
    if len(files) > MAX_ARCHIVE_ENTRIES:
        raise RegistrantImportError("entry_count_exceeded")
    if len({path.name.casefold() for path in files}) != len(files):
        raise RegistrantImportError("duplicate_entry")
    return tuple(files)


def validate_archive_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise RegistrantImportError("path_traversal")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RegistrantImportError("path_traversal")
    if any(part.startswith(".") for part in path.parts):
        raise RegistrantImportError("hidden_entry")
    if len(path.parts) != 1:
        raise RegistrantImportError("unsafe_archive_structure")
    return path
