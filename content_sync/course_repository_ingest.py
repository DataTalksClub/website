"""One course-repository ingestion, two transports.

Content reaches this site two ways.  CI/CD *pushes*: a course repository's
GitHub Action posts a signed push event, ``api.views.course_repository_webhooks``
fences the delivery and enqueues a durable job, and the job downloads that exact
commit's archive.  A developer *pulls*: ``manage.py pull_course_repositories``
reads a checkout that is already on disk, with no network at all.

The two differ only in *where the tar comes from*.  Both read a ``git archive``
tar: codeload serves one over HTTPS for the push, and the pull runs ``git
archive`` in the checkout.  Everything after that -- the admission ceilings, the
path rules, parsing, validation and the transactional projection -- is this
module's single implementation, so a snapshot a developer can load locally is
exactly a snapshot the webhook would accept, and a refusal a developer sees
locally is the refusal production would produce.

Reading the tar on both sides is not a stylistic choice.  A repository's
``.gitattributes`` can carry ``export-ignore`` and ``export-subst``, which
``git archive`` honours and a working-tree or ``git ls-files`` walk does not.
A pull that walked the checkout therefore imported files the push route would
never see, and left ``export-subst`` placeholders unexpanded -- a divergence no
course repository happens to trigger today, which is precisely what made it
survivable.  Running the same reader over the same kind of tar collapses that
class of difference instead of describing it.

Where the transports must differ, they differ in one place each:

* ``fetch_course_repository_snapshot`` downloads GitHub's immutable commit archive.
* ``read_course_repository_checkout`` runs ``git archive`` against a checkout.

Both hand their tar to ``read_course_repository_archive``, which hands a
``dict[str, bytes]`` to ``ingest_course_repository_snapshot`` and admits files
through ``_admit_file``, so the file-count, total-size and per-file ceilings are
one set of numbers rather than two.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import requests

from content.models import ContentSource
from content_sync.course_repository import (
    DEFAULT_LIMITS,
    CourseRepositoryLimits,
    CourseRepositoryValidationError,
    parse_course_repository,
)
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    CurriculumImportError,
    import_course_repository_curriculum,
)

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_ARCHIVE_BYTES = 200_000_000
REQUEST_TIMEOUT_SECONDS = 30
GIT_ARCHIVE_TIMEOUT_SECONDS = 120

ARCHIVE_TRANSPORT = "archive"
CHECKOUT_TRANSPORT = "checkout"


class CourseRepositoryIngestError(RuntimeError):
    """A bounded ingestion refusal with retry classification.

    ``code`` is the stable, log-safe identifier a durable job records.
    ``detail`` names the offending repository path and the numbers involved so
    the same failure is legible to a developer reading a terminal.  Neither ever
    carries file content.
    """

    def __init__(self, code: str, *, retryable: bool = False, detail: str = "") -> None:
        self.code = code
        self.retryable = retryable
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class CourseRepositoryFetchError(CourseRepositoryIngestError):
    """A snapshot could not be obtained from its transport."""


@dataclass(frozen=True, slots=True)
class CourseRepositoryIngestResult:
    """What one ingestion did, in terms safe to print or log."""

    source_stable_id: str
    repository: str
    branch: str
    commit_sha: str
    transport: str
    file_count: int
    total_bytes: int
    counts: Mapping[str, int]
    replayed: bool

    def summary(self) -> dict[str, object]:
        return {
            "source_stable_id": self.source_stable_id,
            "repository": self.repository,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "transport": self.transport,
            "files": self.file_count,
            "bytes": self.total_bytes,
            "counts": dict(self.counts),
            "replayed": self.replayed,
        }


def course_repository_limits(source: ContentSource) -> CourseRepositoryLimits:
    """Return the admission limits for one registered source.

    A source may narrow the parser's ceilings; it may never widen them.  Both
    transports and the parser then agree on exactly one set of numbers, so a
    file the webhook would reject is a file the local pull rejects too.
    """

    return replace(
        DEFAULT_LIMITS,
        max_files=min(int(source.max_files), DEFAULT_LIMITS.max_files),
        max_total_bytes=min(int(source.max_bytes), DEFAULT_LIMITS.max_total_bytes),
    )


def validate_commit_sha(commit_sha: object) -> str:
    if not isinstance(commit_sha, str) or COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise CourseRepositoryIngestError("course_repository_commit_invalid")
    return commit_sha


def _snapshot_relative_path(name: object, *, strip_root: bool) -> str | None:
    """Normalise one transport entry name to a repository-relative POSIX path.

    ``strip_root`` drops the single wrapper directory GitHub's archive adds.
    ``None`` means "not a file of this repository" -- the archive root itself,
    or a directory entry.
    """

    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise CourseRepositoryFetchError(
            "course_repository_archive_path_invalid", detail=repr(name)
        )
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CourseRepositoryFetchError("course_repository_archive_path_invalid", detail=name)
    parts = pure.parts
    if strip_root:
        if len(parts) < 2:
            return None
        parts = parts[1:]
    if not parts:
        return None
    relative = PurePosixPath(*parts).as_posix()
    if relative == ".":
        return None
    return relative


def _admit_file(
    *,
    path: str,
    size: int,
    admitted_files: int,
    admitted_bytes: int,
    limits: CourseRepositoryLimits,
) -> None:
    """Apply the shared admission ceilings to one candidate file.

    Both transports call this, so an oversized or over-budget snapshot fails the
    same way whichever route it arrived by -- and it fails *by name*, because a
    bare ``course_repository_file_too_large`` with no path is a genuinely
    expensive thing to diagnose.
    """

    if size < 0 or size > limits.max_file_bytes:
        raise CourseRepositoryFetchError(
            "course_repository_file_too_large",
            detail=(
                f"{path} is {size} bytes, over the {limits.max_file_bytes}-byte "
                f"per-file limit. Course repositories carry text, not binaries: "
                f"move the asset out of the repository or host it externally."
            ),
        )
    if admitted_files >= limits.max_files:
        raise CourseRepositoryFetchError(
            "course_repository_source_limit_exceeded",
            detail=(
                f"more than {limits.max_files} files (reached at {path}); "
                f"raise the source's max_files or narrow the repository."
            ),
        )
    if admitted_bytes + size > limits.max_total_bytes:
        raise CourseRepositoryFetchError(
            "course_repository_source_limit_exceeded",
            detail=(
                f"more than {limits.max_total_bytes} bytes in total "
                f"(reached at {path}, itself {size} bytes); "
                f"raise the source's max_bytes or narrow the repository."
            ),
        )


def _response_bytes(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_ARCHIVE_BYTES:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_too_large",
                    detail=f"the archive declares {content_length} bytes",
                )
        except ValueError as error:
            raise CourseRepositoryFetchError("course_repository_response_invalid") from error
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_too_large",
                    detail=f"the archive exceeds {MAX_ARCHIVE_BYTES} bytes",
                )
            chunks.append(chunk)
    except CourseRepositoryFetchError:
        raise
    except requests.RequestException as error:
        raise CourseRepositoryFetchError(
            "course_repository_fetch_failed", retryable=True
        ) from error
    return b"".join(chunks)


def read_course_repository_archive(
    archive_bytes: bytes,
    *,
    limits: CourseRepositoryLimits,
    strip_root: bool,
) -> dict[str, bytes]:
    """Project one ``git archive`` tar into a snapshot.  Both transports use this.

    A ``git archive`` tar carries a directory entry for every directory as well
    as a regular entry for every file.  Directories are structure, not content,
    and are skipped; anything that is neither a directory nor a regular file
    (a symlink, a device node, a hard link) is a refusal, because a repository
    must not be able to reach outside itself.

    ``strip_root`` drops the single wrapper directory codeload's archive adds.
    A locally produced ``git archive`` has no prefix, so the pull passes
    ``False``; that is the only knob, and it concerns the tar's shape rather
    than what may be in it.
    """

    snapshot: dict[str, bytes] = {}
    total_bytes = 0
    try:
        # ``r:*`` accepts codeload's gzip and a local uncompressed tar alike, so
        # the compression the transport happened to use is not a second rule.
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*")
    except (tarfile.TarError, OSError) as error:
        raise CourseRepositoryFetchError("course_repository_archive_invalid") from error
    with archive:
        for member in archive:
            path = _snapshot_relative_path(member.name, strip_root=strip_root)
            if path is None or member.isdir():
                continue
            if not member.isreg() or member.issym() or member.islnk():
                raise CourseRepositoryFetchError(
                    "course_repository_archive_entry_invalid",
                    detail=f"{path} is not a regular file",
                )
            _admit_file(
                path=path,
                size=member.size,
                admitted_files=len(snapshot),
                admitted_bytes=total_bytes,
                limits=limits,
            )
            if path in snapshot:
                raise CourseRepositoryFetchError("course_repository_duplicate_path", detail=path)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_entry_invalid",
                    detail=f"{path} has no readable content",
                )
            content = extracted.read(member.size + 1)
            if len(content) != member.size:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_entry_invalid",
                    detail=f"{path} is {len(content)} bytes but declares {member.size}",
                )
            snapshot[path] = content
            total_bytes += len(content)
    return snapshot


def fetch_course_repository_snapshot(
    *,
    owner: str,
    repository: str,
    commit_sha: str,
    limits: CourseRepositoryLimits,
) -> dict[str, bytes]:
    """Push transport: download GitHub's immutable commit archive."""

    commit_sha = validate_commit_sha(commit_sha)
    url = f"https://codeload.github.com/{owner}/{repository}/tar.gz/{commit_sha}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True)
    except requests.RequestException as error:
        raise CourseRepositoryFetchError(
            "course_repository_fetch_failed", retryable=True
        ) from error
    try:
        if response.status_code == 404:
            raise CourseRepositoryFetchError(
                "course_repository_commit_not_found",
                detail=f"{owner}/{repository}@{commit_sha}",
            )
        if response.status_code >= 500:
            raise CourseRepositoryFetchError(
                "course_repository_provider_unavailable", retryable=True
            )
        if response.status_code != 200:
            raise CourseRepositoryFetchError(
                "course_repository_fetch_rejected",
                detail=f"codeload answered {response.status_code}",
            )
        archive_bytes = _response_bytes(response)
    finally:
        response.close()

    return read_course_repository_archive(archive_bytes, limits=limits, strip_root=True)


def _git_archive_bytes(root: Path, commit_sha: str) -> bytes:
    """Return ``git archive <commit>`` for a checkout, without touching a remote.

    ``git archive`` on a local tree-ish is an offline operation: no remote is
    named, and the environment below disables the operator's global and system
    git configuration and any terminal prompt so the tar depends on the
    repository and the commit rather than on the machine.
    """

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
            timeout=GIT_ARCHIVE_TIMEOUT_SECONDS,
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
        raise CourseRepositoryFetchError(
            "course_repository_checkout_archive_failed",
            detail=f"git archive timed out after {GIT_ARCHIVE_TIMEOUT_SECONDS}s in {root}",
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise CourseRepositoryFetchError(
            "course_repository_checkout_archive_failed",
            detail=f"git is unavailable for {root}",
        ) from error
    if completed.returncode != 0:
        reason = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CourseRepositoryFetchError(
            "course_repository_checkout_archive_failed",
            detail=(
                f"git archive {commit_sha} failed in {root}: "
                f"{reason[0][:200] if reason else completed.returncode}"
            ),
        )
    if len(completed.stdout) > MAX_ARCHIVE_BYTES:
        raise CourseRepositoryFetchError(
            "course_repository_archive_too_large",
            detail=f"the archive exceeds {MAX_ARCHIVE_BYTES} bytes",
        )
    return completed.stdout


def read_course_repository_checkout(
    root: Path,
    *,
    commit_sha: str,
    limits: CourseRepositoryLimits,
) -> dict[str, bytes]:
    """Pull transport: ``git archive`` one commit out of a checkout on disk.

    No network call is made and no remote is consulted.  The snapshot is the
    commit's exported tree, not the working tree, so a repository's
    ``.gitattributes`` decides what is in it exactly as it does for codeload.
    That is what makes the two transports agree by construction: uncommitted
    edits, untracked files and ``export-ignore``d paths cannot enter here
    because they cannot enter there.
    """

    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise CourseRepositoryFetchError("course_repository_checkout_unavailable", detail=str(root))
    commit_sha = validate_commit_sha(commit_sha)
    archive_bytes = _git_archive_bytes(root, commit_sha)
    return read_course_repository_archive(archive_bytes, limits=limits, strip_root=False)


def _assert_ingestible(source: ContentSource) -> None:
    if not source.enabled or source.adapter_type != COURSE_REPOSITORY_ADAPTER_TYPE:
        raise CourseRepositoryIngestError(
            "course_repository_source_disabled",
            detail=f"{source.stable_id} is not an enabled course-repository source",
        )


def ingest_course_repository_snapshot(
    *,
    source: ContentSource,
    commit_sha: str,
    snapshot: Mapping[str, bytes],
    transport: str,
) -> CourseRepositoryIngestResult:
    """Parse and project one snapshot.  Both entry points end up here.

    This function has no idea where the snapshot came from, which is the whole
    point: the validation the webhook enforces and the validation a developer
    gets are not two implementations that could drift, they are this one.
    """

    _assert_ingestible(source)
    commit_sha = validate_commit_sha(commit_sha)
    limits = course_repository_limits(source)

    try:
        parsed = parse_course_repository(snapshot, commit_sha=commit_sha, limits=limits)
    except CourseRepositoryValidationError as error:
        diagnostic = error.diagnostics[0]
        raise CourseRepositoryIngestError(
            f"course_repository_{diagnostic.code}",
            detail=f"{diagnostic.source_path or '<snapshot>'}{diagnostic.pointer}",
        ) from error

    try:
        result = import_course_repository_curriculum(
            CurriculumImportCommand(
                source=parsed,
                source_uuid=source.id,
                source_stable_id=source.stable_id,
                repository_owner=source.repository_owner,
                repository_name=source.repository_name,
                repository_branch=source.branch,
                commit_sha=commit_sha,
            )
        )
    except CurriculumImportError as error:
        raise CourseRepositoryIngestError(
            f"course_repository_{error.code}",
            detail=f"{source.stable_id}@{commit_sha}",
        ) from error

    return CourseRepositoryIngestResult(
        source_stable_id=source.stable_id,
        repository=f"{source.repository_owner}/{source.repository_name}",
        branch=source.branch,
        commit_sha=commit_sha,
        transport=transport,
        file_count=len(snapshot),
        total_bytes=sum(len(value) for value in snapshot.values()),
        counts=MappingProxyType(dict(result.counts)),
        replayed=result.replayed,
    )


def ingest_course_repository(
    *,
    source: ContentSource,
    commit_sha: str,
    checkout_root: Path | None = None,
) -> CourseRepositoryIngestResult:
    """Ingest one commit of one registered source.

    ``checkout_root is None`` selects the push transport and downloads the
    commit archive.  Any other value selects the pull transport and produces
    the same archive locally, offline.  This ``if`` is the only difference
    between the two routes; everything after it is shared.
    """

    _assert_ingestible(source)
    commit_sha = validate_commit_sha(commit_sha)
    limits = course_repository_limits(source)

    if checkout_root is None:
        transport = ARCHIVE_TRANSPORT
        snapshot = fetch_course_repository_snapshot(
            owner=source.repository_owner,
            repository=source.repository_name,
            commit_sha=commit_sha,
            limits=limits,
        )
    else:
        transport = CHECKOUT_TRANSPORT
        snapshot = read_course_repository_checkout(
            checkout_root, commit_sha=commit_sha, limits=limits
        )

    return ingest_course_repository_snapshot(
        source=source,
        commit_sha=commit_sha,
        snapshot=snapshot,
        transport=transport,
    )


__all__ = (
    "ARCHIVE_TRANSPORT",
    "CHECKOUT_TRANSPORT",
    "CourseRepositoryFetchError",
    "CourseRepositoryIngestError",
    "CourseRepositoryIngestResult",
    "course_repository_limits",
    "fetch_course_repository_snapshot",
    "ingest_course_repository",
    "ingest_course_repository_snapshot",
    "read_course_repository_archive",
    "read_course_repository_checkout",
    "validate_commit_sha",
)
