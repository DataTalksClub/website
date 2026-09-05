"""Generic snapshot-transport logic shared by every push/pull content source.

A "snapshot" here is a bounded ``dict[str, bytes]``: a ``git archive`` tar --
fetched over HTTPS from GitHub's codeload for the push transport, or produced
locally by running ``git archive`` against a checkout for the pull transport
-- turned into repository-relative POSIX paths mapped to their bytes, with
per-file, file-count and total-byte admission ceilings applied while the tar
is walked.

Extracted from ``content_sync/course_repository_ingest.py`` (see
``.tmp/content-ingest-design.md`` section 5.1, row "Snapshot transport"),
which remains the only caller today and keeps its own ``course_repository_*``
exception type, codes and messages by wrapping the generic functions here.
Nothing in this module knows what a "course" is: admission limits are any
object with ``max_files``, ``max_total_bytes`` and ``max_file_bytes``
attributes (``content_sync.course_repository.CourseRepositoryLimits``
satisfies this today), and refusals are reported through the source-free
``SnapshotError`` with generic reason codes, so a future docs/faq adapter can
reuse these mechanics with its own exception type and vocabulary the same way
course-repository ingestion does -- see ``course_repository_ingest.py`` for
the wrapping pattern (catch ``SnapshotError``, re-raise
``f"course_repository_{error.code}"`` with the same ``retryable``/``detail``,
or with hand-written detail text for the admission ceilings, which the
``admit`` callback of :func:`read_snapshot_archive` exists to make possible).

What this module is *not*: it is not "the served artifact via an atomic
swap". Course repositories never build a served file-based projection to
swap into place -- ``ingest_course_repository_snapshot`` in
``course_repository_ingest.py`` parses a snapshot from here and writes
straight into the ``courses`` app's models inside one transaction
(``import_course_repository_curriculum``). An atomic rename-into-place step
does not exist for any source in this codebase yet. The design doc names
that separately as the *not-yet-built* ``content_sync/serving_snapshot.py``
("Atomic serving swap", section 5.1; ordering in section 9.3 steps 6-7), for
the future JSON-served sources (wiki/faq/docs) that would take the snapshot
dict this module returns, adapt it, and bake a runtime-served tree from it.
Section 9.4 of the same design doc says so explicitly for courses: "No JSON
snapshot. Do not add one."
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

DEFAULT_MAX_ARCHIVE_BYTES = 200_000_000
DEFAULT_GIT_ARCHIVE_TIMEOUT_SECONDS = 120


class SnapshotLimits(Protocol):
    """Duck-typed admission ceilings; ``CourseRepositoryLimits`` satisfies this."""

    max_files: int
    max_total_bytes: int
    max_file_bytes: int


class SnapshotError(RuntimeError):
    """A bounded, source-free snapshot-transport refusal.

    ``code`` is a stable, generic reason a caller can log directly or
    translate into its own vocabulary (course-repository ingestion prefixes
    it with ``course_repository_`` and, for the admission ceilings, rebuilds
    its own prose -- see ``course_repository_ingest.py``). ``retryable``
    marks a transient condition (a subprocess timeout, an unreadable
    checkout) as opposed to a structural refusal (an oversized file, an
    unsafe path). Neither ``code`` nor ``detail`` ever carries file content.
    """

    def __init__(self, code: str, *, retryable: bool = False, detail: str = "") -> None:
        self.code = code
        self.retryable = retryable
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def validate_commit_sha(commit_sha: object) -> str:
    """Validate a full, lower-case 40-character commit SHA."""

    if not isinstance(commit_sha, str) or COMMIT_SHA_PATTERN.fullmatch(commit_sha) is None:
        raise SnapshotError("commit_invalid")
    return commit_sha


def snapshot_relative_path(name: object, *, strip_root: bool) -> str | None:
    """Normalise one tar member name to a repository-relative POSIX path.

    ``strip_root`` drops the single wrapper directory GitHub's codeload
    archive adds; a locally produced ``git archive`` has no such prefix, so
    the pull transport passes ``False``. ``None`` means "not a file of this
    repository" -- the archive root itself, or (after stripping) an empty
    remainder.
    """

    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise SnapshotError("archive_path_invalid", detail=repr(name))
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise SnapshotError("archive_path_invalid", detail=name)
    parts = pure.parts
    if strip_root:
        if len(parts) < 2:
            return None
        parts = parts[1:]
    if not parts:
        return None
    relative = PurePosixPath(*parts).as_posix()
    return None if relative == "." else relative


def check_admission_ceiling(
    *, size: int, admitted_files: int, admitted_bytes: int, limits: SnapshotLimits
) -> str | None:
    """Return which ceiling one candidate file would breach, or ``None``.

    A pure comparison with no exception and no message text: both transports
    call this (directly, or through :func:`read_snapshot_archive`'s default
    admission) through the same admitted-so-far counters, so an oversized or
    over-budget snapshot fails the same way whichever route it arrived by.
    Returns ``"file"``, ``"count"`` or ``"total"`` for the first ceiling
    breached, else ``None``.
    """

    if size < 0 or size > limits.max_file_bytes:
        return "file"
    if admitted_files >= limits.max_files:
        return "count"
    if admitted_bytes + size > limits.max_total_bytes:
        return "total"
    return None


def read_snapshot_archive(
    archive_bytes: bytes,
    *,
    limits: SnapshotLimits,
    strip_root: bool,
    admit: Callable[[str, int, int, int], None] | None = None,
) -> dict[str, bytes]:
    """Project one ``git archive`` tar into a snapshot.

    A ``git archive`` tar carries a directory entry for every directory as
    well as a regular entry for every file. Directories are structure, not
    content, and are skipped; anything that is neither a directory nor a
    regular file (a symlink, a device node, a hard link) is a refusal,
    because a repository must not be able to reach outside itself.

    ``admit`` lets a caller apply its own admission-ceiling refusal (its own
    exception type, its own message) for each candidate file, called with
    ``(path, size, admitted_files_so_far, admitted_bytes_so_far)`` before the
    file is read; it should raise to refuse. When omitted,
    :func:`check_admission_ceiling` is applied and a breach raises
    ``SnapshotError("admission_ceiling_exceeded")`` with a generic detail.
    """

    snapshot: dict[str, bytes] = {}
    total_bytes = 0
    try:
        # ``r:*`` accepts codeload's gzip and a local uncompressed tar alike, so
        # the compression the transport happened to use is not a second rule.
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*")
    except (tarfile.TarError, OSError) as error:
        raise SnapshotError("archive_invalid") from error
    with archive:
        for member in archive:
            path = snapshot_relative_path(member.name, strip_root=strip_root)
            if path is None or member.isdir():
                continue
            if not member.isreg() or member.issym() or member.islnk():
                raise SnapshotError("archive_entry_invalid", detail=f"{path} is not a regular file")
            if admit is not None:
                admit(path, member.size, len(snapshot), total_bytes)
            else:
                breach = check_admission_ceiling(
                    size=member.size,
                    admitted_files=len(snapshot),
                    admitted_bytes=total_bytes,
                    limits=limits,
                )
                if breach is not None:
                    raise SnapshotError(
                        "admission_ceiling_exceeded",
                        detail=f"{path} breaches the {breach} ceiling",
                    )
            if path in snapshot:
                raise SnapshotError("duplicate_path", detail=path)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise SnapshotError(
                    "archive_entry_invalid", detail=f"{path} has no readable content"
                )
            content = extracted.read(member.size + 1)
            if len(content) != member.size:
                raise SnapshotError(
                    "archive_entry_invalid",
                    detail=f"{path} is {len(content)} bytes but declares {member.size}",
                )
            snapshot[path] = content
            total_bytes += len(content)
    return snapshot


def run_git_archive(
    root: Path,
    commit_sha: str,
    *,
    timeout_seconds: float = DEFAULT_GIT_ARCHIVE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> bytes:
    """Return ``git archive <commit>`` for a checkout, without touching a remote.

    ``git archive`` on a local tree-ish is an offline operation: no remote is
    named, and the environment below disables the operator's global and
    system git configuration and any terminal prompt so the tar depends on
    the repository and the commit rather than on the machine.
    """

    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise SnapshotError("checkout_unavailable", detail=str(root))
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar",
                commit_sha,
            ],
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
            },
        )
    except subprocess.TimeoutExpired as error:
        raise SnapshotError(
            "checkout_archive_failed",
            detail=f"git archive timed out after {timeout_seconds}s in {root}",
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise SnapshotError(
            "checkout_archive_failed", detail=f"git is unavailable for {root}"
        ) from error
    if completed.returncode != 0:
        reason = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SnapshotError(
            "checkout_archive_failed",
            detail=(
                f"git archive {commit_sha} failed in {root}: "
                f"{reason[0][:200] if reason else completed.returncode}"
            ),
        )
    if len(completed.stdout) > max_bytes:
        raise SnapshotError("archive_too_large", detail=f"the archive exceeds {max_bytes} bytes")
    return completed.stdout


__all__ = [
    "COMMIT_SHA_PATTERN",
    "DEFAULT_GIT_ARCHIVE_TIMEOUT_SECONDS",
    "DEFAULT_MAX_ARCHIVE_BYTES",
    "SnapshotError",
    "SnapshotLimits",
    "check_admission_ceiling",
    "read_snapshot_archive",
    "run_git_archive",
    "snapshot_relative_path",
    "validate_commit_sha",
]
