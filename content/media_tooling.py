"""Operator tooling shared by the public media hydrate/publish/verify commands.

None of this module is reachable from a public, Studio, or admin API request path: it is
imported only by the three ``manage.py`` commands and their focused tests.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .media_store import (
    LocalMediaStore,
    MediaObjectMissing,
    MediaStore,
    MediaStoreError,
    MediaStoreUnavailable,
    media_records,
    read_media_object,
    record_checksum,
    record_key,
    record_relative_path,
)

RAW_CONTENT_ORIGIN = "https://raw.githubusercontent.com"
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30.0


class MediaToolingError(RuntimeError):
    """An operator command cannot continue safely."""


@dataclass(slots=True)
class HydrateReport:
    total: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "written": self.written,
            "skipped": self.skipped,
            "failed": self.failed,
            "failures": sorted(self.failures)[:20],
        }


@dataclass(slots=True)
class PublishReport:
    total: int = 0
    added: int = 0
    changed: int = 0
    skipped: int = 0
    orphan: int = 0
    failed: int = 0
    orphans: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "added": self.added,
            "changed": self.changed,
            "skipped": self.skipped,
            "orphan": self.orphan,
            "failed": self.failed,
            "orphans": sorted(self.orphans)[:20],
            "failures": sorted(self.failures)[:20],
        }


@dataclass(slots=True)
class VerifyReport:
    total: int = 0
    matched: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.missing or self.mismatched or self.extra or self.unreadable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "matched": self.matched,
            "missing": sorted(self.missing)[:20],
            "missing_count": len(self.missing),
            "mismatched": sorted(self.mismatched)[:20],
            "mismatched_count": len(self.mismatched),
            "extra": sorted(self.extra)[:20],
            "extra_count": len(self.extra),
            "unreadable": sorted(self.unreadable)[:20],
            "unreadable_count": len(self.unreadable),
        }


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_checkout_arguments(values: Iterable[str]) -> dict[str, Path]:
    """Parse ``--checkout owner/repository=/path`` pairs into a mapping."""

    checkouts: dict[str, Path] = {}
    for value in values:
        repository, separator, path = value.partition("=")
        if not separator or not repository.strip() or not path.strip():
            raise MediaToolingError("a --checkout value must be repository=path")
        checkouts[repository.strip()] = Path(path.strip())
    return checkouts


def _record_provenance(record: Mapping[str, Any]) -> tuple[str, str, str]:
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        raise MediaToolingError("media record has no provenance")
    repository = str(provenance.get("repository", ""))
    revision = str(provenance.get("revision", ""))
    source_path = str(provenance.get("source_path", ""))
    if not repository or not revision or not source_path:
        raise MediaToolingError("media record provenance is incomplete")
    return repository, revision, source_path


def _read_from_checkout(record: Mapping[str, Any], checkouts: Mapping[str, Path]) -> bytes:
    repository, _revision, source_path = _record_provenance(record)
    root = checkouts.get(repository)
    if root is None:
        raise MediaToolingError(f"no --checkout supplied for {repository}")
    candidate = root / source_path
    resolved_root = root.resolve()
    if not candidate.resolve().is_relative_to(resolved_root):
        raise MediaToolingError("source path escapes the supplied checkout")
    try:
        return candidate.read_bytes()
    except OSError as error:
        raise MediaToolingError("source object cannot be read from the checkout") from error


def _read_from_github(record: Mapping[str, Any], *, timeout: float, maximum: int) -> bytes:
    repository, revision, source_path = _record_provenance(record)
    quoted = urllib.parse.quote(source_path)
    url = f"{RAW_CONTENT_ORIGIN}/{repository}/{revision}/{quoted}"
    request = urllib.request.Request(url, headers={"User-Agent": "dtc-website-hydrate"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read(maximum + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise MediaToolingError("pinned upstream object could not be downloaded") from error
    if len(payload) > maximum:
        raise MediaToolingError("pinned upstream object exceeds the configured bound")
    return payload


def _read_from_store(record: Mapping[str, Any], store: MediaStore) -> bytes:
    try:
        return read_media_object(store, record)
    except MediaStoreError as error:
        raise MediaToolingError("store object could not be read and verified") from error


def _atomic_write(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(destination.parent), prefix=".hydrate-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def hydrate_media(
    *,
    destination_root: Path,
    source: str,
    checkouts: Mapping[str, Path] | None = None,
    store: MediaStore | None = None,
    maximum_object_bytes: int,
    timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    force: bool = False,
    records: Iterable[Mapping[str, Any]] | None = None,
) -> HydrateReport:
    """Materialise every recorded object into ``destination_root``.

    The work is idempotent and resumable: an object already present with the recorded
    checksum is skipped, and an object whose retrieved digest does not match the record
    is never written.
    """

    report = HydrateReport()
    for record in records if records is not None else media_records():
        report.total += 1
        key = record_key(record)
        expected = record_checksum(record)
        target = destination_root / record_relative_path(record)
        if not force and target.is_file() and not target.is_symlink():
            try:
                if _sha256_hex(target.read_bytes()) == expected:
                    report.skipped += 1
                    continue
            except OSError:
                pass
        try:
            if source == "checkout":
                payload = _read_from_checkout(record, checkouts or {})
            elif source == "store":
                if store is None:
                    raise MediaToolingError("no media store supplied")
                payload = _read_from_store(record, store)
            elif source == "github":
                payload = _read_from_github(
                    record, timeout=timeout_seconds, maximum=maximum_object_bytes
                )
            else:
                raise MediaToolingError(f"unsupported hydrate source: {source}")
            if len(payload) > maximum_object_bytes:
                raise MediaToolingError("source object exceeds the configured bound")
            if _sha256_hex(payload) != expected:
                raise MediaToolingError("source object digest does not match the record")
            _atomic_write(target, payload)
        except (MediaToolingError, OSError):
            report.failed += 1
            report.failures.append(key)
            continue
        report.written += 1
    return report


def publish_media(
    *,
    source_root: Path,
    store: Any,
    maximum_object_bytes: int,
    dry_run: bool = False,
    records: Iterable[Mapping[str, Any]] | None = None,
) -> PublishReport:
    """Upload exactly the recorded objects, refusing anything with no record."""

    report = PublishReport()
    resolved = tuple(records if records is not None else media_records())
    known_relative = {record_relative_path(record) for record in resolved}
    local = LocalMediaStore(root=source_root, maximum_object_bytes=maximum_object_bytes)

    for record in resolved:
        report.total += 1
        key = record_key(record)
        expected = record_checksum(record)
        try:
            payload = local.fetch(record).payload
        except MediaStoreError:
            report.failed += 1
            report.failures.append(key)
            continue
        if _sha256_hex(payload) != expected:
            report.failed += 1
            report.failures.append(key)
            continue
        try:
            existing = store.stat(record)
        except MediaObjectMissing:
            existing = None
        except (MediaStoreUnavailable, MediaStoreError):
            report.failed += 1
            report.failures.append(key)
            continue
        if existing is not None and existing.checksum == expected:
            report.skipped += 1
            continue
        if not dry_run:
            try:
                store.put(record, payload)
            except MediaStoreError:
                report.failed += 1
                report.failures.append(key)
                continue
        if existing is None:
            report.added += 1
        else:
            report.changed += 1

    if source_root.is_dir():
        for path in sorted(source_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(source_root).as_posix()
            if relative not in known_relative:
                report.orphan += 1
                report.orphans.append(f"images/{relative}")
    return report


def verify_media(
    *,
    store: MediaStore,
    records: Iterable[Mapping[str, Any]] | None = None,
) -> VerifyReport:
    """Compare the configured store against ``media.json``."""

    report = VerifyReport()
    resolved = tuple(records if records is not None else media_records())
    expected_keys = set()
    for record in resolved:
        report.total += 1
        key = record_key(record)
        expected_keys.add(key)
        # ``expected_checksum`` is the record's ``provenance.checksum`` for every store
        # that holds real bytes.  Only the deterministic offline fixture store overrides
        # it, so ``verify`` stays meaningful for ``local`` and ``s3`` while remaining
        # runnable in an offline CI job.
        expected = store.expected_checksum(record)
        try:
            stat = store.stat(record)
        except MediaObjectMissing:
            report.missing.append(key)
            continue
        except MediaStoreError:
            report.unreadable.append(key)
            continue
        if stat.checksum != expected:
            report.mismatched.append(key)
            continue
        report.matched += 1

    try:
        present = set(store.existing_keys())
    except MediaStoreError:
        present = set()
    prefix = getattr(store, "prefix", "")
    normalized = {
        candidate[len(prefix) + 1 :] if prefix and candidate.startswith(f"{prefix}/") else candidate
        for candidate in present
    }
    report.extra.extend(sorted(normalized - expected_keys))
    return report
