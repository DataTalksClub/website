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

import bz2
import errno
import gzip
import io
import lzma
import os
import re
import select
import signal
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

DEFAULT_MAX_ARCHIVE_BYTES = 200_000_000
DEFAULT_GIT_ARCHIVE_TIMEOUT_SECONDS = 120
_GIT_ARCHIVE_CHUNK_BYTES = 64 * 1024
_GIT_ARCHIVE_STDERR_BYTES = 8_192
_RECORD_BYTES = 10_240


class SnapshotLimits(Protocol):
    """Duck-typed admission ceilings; ``CourseRepositoryLimits`` satisfies this.

    Read-only members on purpose: nothing here ever writes a ceiling back, and
    a settable protocol member would exclude exactly the implementations this
    module wants -- ``CourseRepositoryLimits`` is a frozen dataclass, and a
    future adapter's ceilings have every reason to be frozen too.
    """

    @property
    def max_files(self) -> int: ...

    @property
    def max_total_bytes(self) -> int: ...

    @property
    def max_file_bytes(self) -> int: ...


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

    The checks run on the raw name *before* POSIX normalisation could quietly
    reinterpret it: repeated separators and ``.`` segments never reach
    :class:`PurePosixPath`'s collapsing, raw control characters (an invisible
    name is an unreviewable name) are refused outright, and a lone trailing
    slash is honoured as the tar convention for directory members but a
    doubled one is not.
    """

    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise SnapshotError("archive_path_invalid", detail=repr(name))
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
        raise SnapshotError("archive_path_invalid", detail=repr(name))
    trimmed = name[:-1] if name.endswith("/") else name
    parts = trimmed.split("/")
    if not trimmed or any(part in {"", ".", ".."} for part in parts):
        raise SnapshotError("archive_path_invalid", detail=name)
    if strip_root:
        if len(parts) < 2:
            return None
        parts = parts[1:]
    if not parts:
        return None
    relative = "/".join(parts)
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


def _member_budget(max_files: int) -> int:
    """Structural entries a tar may carry for ``max_files`` admitted files.

    Directories are structure, not content, so they must not consume the
    regular-file count -- but they are still unbounded work if left
    uncounted, so they get their own generous multiple of the file ceiling
    plus flat slack for the archive's own bookkeeping entries.
    """

    return 8 * max_files + 64


def _decompressed_budget(max_files: int, limits: SnapshotLimits) -> int:
    """Decompressed bytes one archive may pull through the reader.

    Headers, padding and tar metadata for every structural entry, plus the
    admitted content ceiling plus two records of end-of-archive slack. A
    legitimate archive of an admitted repository fits comfortably; a
    compressed bomb that decompresses into megabytes of metadata does not.
    """

    return limits.max_total_bytes + _member_budget(max_files) * 2048 + 2 * _RECORD_BYTES


def _decompressed_stream(archive_bytes: bytes) -> io.BufferedIOBase:
    """Detect the compression wrapper and hand back a plain byte stream.

    The transport decides compression, not policy: codeload's ``tar.gz``, the
    checkout transport's plain ``tar``, and bz2/xz variants (accepted for
    parity with the transparent ``r:*`` this replaces) all arrive as one
    readable, seekable stream.
    """

    head = archive_bytes[:6]
    raw = io.BytesIO(archive_bytes)
    if head.startswith(b"\x1f\x8b"):
        return gzip.GzipFile(fileobj=raw, mode="rb")
    if head.startswith(b"BZh"):
        return bz2.BZ2File(raw, "rb")
    if head.startswith(b"\xfd7zXZ\x00"):
        return lzma.LZMAFile(raw, "rb")
    return raw


class _BudgetedStream(io.BufferedIOBase):
    """The decompression boundary the tar layer may not outspend.

    ``tarfile`` moves through an archive with seeks as much as with reads --
    member data it does not extract is skipped, not read -- so counting only
    reads would let a bomb's bulk slip through the gaps. Consumption is the
    stream's high-water position: every forward move (read, fill, forward
    seek) is charged, and a request that would overshoot the budget raises
    *before* serving, so no caller ever sees a silent short read that could
    stand in for real content. Backward seeks replay already-charged bytes
    for free.
    """

    def __init__(self, stream: io.BufferedIOBase, budget: int) -> None:
        self._stream = stream
        self._remaining = budget
        self._position = 0

    def _refuse(self) -> SnapshotError:
        return SnapshotError(
            "archive_expansion_exceeded",
            detail="the archive expands past the decompression budget",
        )

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            if self._remaining <= 0:
                return b""
            data = self._stream.read(self._remaining)
        else:
            if size > self._remaining:
                raise self._refuse()
            data = self._stream.read(size)
        self._charge(len(data))
        return data

    def readinto(self, buffer: WriteableBuffer) -> int:
        size = memoryview(buffer).cast("B").nbytes
        if size > self._remaining:
            raise self._refuse()
        amount = self._stream.readinto(buffer)
        self._charge(amount)
        return amount

    def seek(self, target: int, whence: int = 0) -> int:
        if whence == 1:
            target += self._position
        elif whence != 0:
            raise OSError(errno.EINVAL, "seek relative to start or current only")
        if target > self._position:
            self._charge(target - self._position)
        self._stream.seek(target)
        self._position = target
        return target

    def tell(self) -> int:
        return self._position

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            super().close()

    def _charge(self, amount: int) -> None:
        if amount > self._remaining:
            raise self._refuse()
        self._remaining -= amount
        self._position += amount


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

    Structural work is bounded independently of content: total tar entries
    (directories included, at their own generous multiple of ``max_files``)
    and total bytes pulled through decompression each have a budget, so a
    tar made of metadata refuses before its metadata is materialised.

    ``admit`` lets a caller apply its own admission-ceiling refusal (its own
    exception type, its own message) for each candidate file, called with
    ``(path, size, admitted_files_so_far, admitted_bytes_so_far)`` before the
    file is read; it should raise to refuse. When omitted,
    :func:`check_admission_ceiling` is applied and a breach raises
    ``SnapshotError("admission_ceiling_exceeded")`` with a generic detail.
    """

    snapshot: dict[str, bytes] = {}
    total_bytes = 0
    member_budget = _member_budget(limits.max_files)
    member_count = 0
    stream = _BudgetedStream(
        _decompressed_stream(archive_bytes), _decompressed_budget(limits.max_files, limits)
    )
    try:
        # Compression is detected and applied *below* the budget on purpose:
        # a transparent ``r:*`` would hide everything the decompressor
        # produces from the accounting, and a compressed bomb's decompressed
        # volume is exactly what must be bound.
        archive = tarfile.open(fileobj=stream, mode="r")  # type: ignore[arg-type]
    except (tarfile.TarError, OSError) as error:
        raise SnapshotError("archive_invalid") from error
    try:
        with archive:
            for member in archive:
                member_count += 1
                if member_count > member_budget:
                    raise SnapshotError(
                        "archive_members_exceeded",
                        detail=(
                            f"more than {member_budget} archive entries "
                            f"(reached at {member.name!r})"
                        ),
                    )
                path = snapshot_relative_path(member.name, strip_root=strip_root)
                if path is None or member.isdir():
                    continue
                if not member.isreg() or member.issym() or member.islnk():
                    raise SnapshotError(
                        "archive_entry_invalid", detail=f"{path} is not a regular file"
                    )
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
    finally:
        stream.close()
    return snapshot


def _terminate_archive_process(process: subprocess.Popen[bytes]) -> None:
    """Kill the archive child and anything it spawned, then reap it.

    A plain ``kill()`` reaches only the direct child: a shell wrapping git,
    or a helper it spawned, survives holding the pipe ends open -- which
    keeps our stdout reads, the stderr drainer thread and even the pipe
    ``close()`` calls wedged until the orphan exits on its own. The child
    starts in its own session precisely so the group signal reaches the
    whole tree.
    """

    if process.poll() is None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
    process.wait()


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

    The archive is collected incrementally, not captured wholesale: stdout is
    streamed in chunks and refused the moment it passes ``max_bytes``, stderr
    is drained by a bounded side channel, and one wall-clock deadline bounds
    the whole child, so an archive that is too big or a git that will not
    finish is killed and reaped instead of buffered to completion.
    """

    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise SnapshotError("checkout_unavailable", detail=str(root))

    def timed_out() -> SnapshotError:
        return SnapshotError(
            "checkout_archive_failed",
            detail=f"git archive timed out after {timeout_seconds}s in {root}",
        )

    try:
        process = subprocess.Popen(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar",
                commit_sha,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
            },
        )
    except OSError as error:
        raise SnapshotError(
            "checkout_archive_failed", detail=f"git is unavailable for {root}"
        ) from error

    stdout = process.stdout
    stderr = process.stderr
    assert isinstance(stdout, io.BufferedReader)
    assert isinstance(stderr, io.BufferedReader)
    deadline = time.monotonic() + timeout_seconds
    collected = bytearray()
    stderr_buffer = bytearray()
    failure: SnapshotError | None = None
    returncode = 0

    def drain_stderr() -> None:
        """Collect the first bytes of stderr; discard the rest, keep draining.

        Draining at all is what prevents a full stderr pipe from deadlocking
        the child against our stdout read; capping is what keeps a chatty
        failure from becoming unbounded memory.
        """

        try:
            while True:
                chunk = stderr.read(_GIT_ARCHIVE_CHUNK_BYTES)
                if not chunk:
                    return
                room = max(0, _GIT_ARCHIVE_STDERR_BYTES - len(stderr_buffer))
                stderr_buffer.extend(chunk[:room])
        except OSError:
            return

    drainer = threading.Thread(target=drain_stderr, daemon=True)
    drainer.start()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = timed_out()
                break
            ready, _, _ = select.select([stdout], [], [], remaining)
            if not ready:
                failure = timed_out()
                break
            try:
                chunk = stdout.read1(_GIT_ARCHIVE_CHUNK_BYTES)
            except OSError:
                failure = SnapshotError(
                    "checkout_archive_failed",
                    detail=f"git archive {commit_sha} failed streaming in {root}",
                )
                break
            if not chunk:
                break
            collected.extend(chunk)
            if len(collected) > max_bytes:
                failure = SnapshotError(
                    "archive_too_large", detail=f"the archive exceeds {max_bytes} bytes"
                )
                break
        if failure is None:
            remaining = max(deadline - time.monotonic(), 0.001)
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                failure = timed_out()
        if failure is not None:
            _terminate_archive_process(process)
            drainer.join(timeout=5)
            raise failure
        if returncode != 0:
            drainer.join(timeout=5)
            reason = stderr_buffer.decode("utf-8", "replace").strip().splitlines()
            raise SnapshotError(
                "checkout_archive_failed",
                detail=(
                    f"git archive {commit_sha} failed in {root}: "
                    f"{reason[0][:200] if reason else returncode}"
                ),
            )
        return bytes(collected)
    finally:
        _terminate_archive_process(process)
        drainer.join(timeout=5)
        stdout.close()
        stderr.close()


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
