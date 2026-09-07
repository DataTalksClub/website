#!/usr/bin/env python3
"""Report drift between the editorial content this database serves and
``DataTalksClub/content``.

Git-synchronized -- see ``scripts/prod/__init__.py`` for what the two sync models
mean.  This one never writes: it answers *"is what we serve still what the content
repository says?"* and stops there.  Repairing what it finds belongs to the ingest
path, not here.

    uv run --frozen python scripts/prod/sync_content_verify.py \\
        --database .tmp/local.sqlite3 --checkout /absolute/path/to/content

Read-only in both directions
----------------------------

No ``INSERT``, ``UPDATE`` or ``DELETE``, no service call, no release transition, and
none of ``last_reconciled_at`` / ``pending_follow_up`` / ``last_successful_commit``.
It also makes no call of its own over a network: every git invocation reads the
checkout's own object database, and the revision it compares against is resolved
locally.  Refreshing that checkout is ``make content-checkout``, deliberately a
separate step, so an answer is never quietly one round trip old.  Because the answer
is only as fresh as that checkout, the report states the remote-tracking head it
resolved, so an operator can tell how stale it is.

What is compared, and by what identity
--------------------------------------

The five families specification 03 assigns to ``DataTalksClub/content``.  Selection is
data-driven: a served record is in scope when its ``provenance.repository`` (or, for a
transcript, its ``transcript_provenance.repository``) is ``DataTalksClub/content``.
Records owned by the legacy site, podwiki or the course platform are ignored, not
reported missing.

============================  ==============================  =========================
Family                        Served identity                 Upstream identity
============================  ==============================  =========================
``articles``                  ``provenance.source_key``       stem after the date
``podcasts``                  ``provenance.source_key``       the ``slug`` key
``podcast_transcripts``       ``transcript_provenance``       the ``podcast`` key
``books``                     ``provenance.source_key``       the ``slug`` key
``media``                     ``provenance.source_path``      repository-relative path
============================  ==============================  =========================

The four document families key on the stable identity and never on the source path.
Over fifty files differ only by path between the revision the served catalogue was
built from and upstream, so a path key would report ~100% false drift; a file that
moves with its bytes and its identity intact is not drift and is reported clean.

Digests need no parser.  ``provenance.checksum`` is the raw SHA-256 of the upstream
file bytes, so a mismatch is a byte-for-byte comparison against the blob at the named
revision.

Media is asymmetric on purpose.  The served media set is the *referenced* subset by
construction -- an upstream image no article, podcast or book points at was never
published -- so upstream-only images are reported in their own
``upstream_unreferenced`` bucket, counted and listed but not part of the exit code.
``extra`` (served, gone upstream) and ``mismatched`` are drift and do set it.  Media's
``missing`` bucket therefore stays empty by construction; it is kept so that every
family reads the same way.

Exit codes
----------

::

    0  clean      every family matched and the served revision is upstream's tip
    1  drift      the report is on stdout and something is not clean
    2  refusal    the run could not produce an answer; {"error": ...} on stderr

One is reserved for a *report*, so "we are behind" is distinguishable from "I could
not look".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "git-synchronized"
# It reads and reports. A database it ran against holds exactly the rows it held
# before, so it can populate nothing.
BOOTSTRAPS_EMPTY_DATABASE = False

#: The one repository this check compares against, spelled as ``provenance.repository``
#: spells it.
EDITORIAL_REPOSITORY = "DataTalksClub/content"
#: Reported in this order, so two runs over the same data read the same way.
FAMILIES = ("articles", "podcasts", "podcast_transcripts", "books", "media")
#: The document kind each served family is stored under, and the family it feeds.
SERVED_KINDS = {"article": "articles", "podcast": "podcasts", "book": "books", "media": "media"}
#: Upstream media lives under exactly these three trees.
MEDIA_PREFIXES = ("images/posts/", "images/podcast/", "images/books/")
#: The media suffixes the adapter admits, so a stray file under ``images/`` is not
#: mistaken for a published object.
MEDIA_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg"})
#: An article's identity is its stem with the publication date stripped, exactly as
#: ``scripts/build_public_projection.py`` mints it.
ARTICLE_DATE_PREFIX = re.compile(r"^(?:\d{2}|\d{4})-\d{2}-\d{2}-(.+)$")
#: Every bucket is truncated to this many entries beside a full count, the shape
#: ``content/media_tooling.py``'s ``VerifyReport`` reports in.
BUCKET_LIMIT = 20
GIT_TIMEOUT_SECONDS = 120
#: Blob bytes are read in batches bounded by this much content at a time, so a
#: repository holding 150 MB of images never has 150 MB in memory at once.
BATCH_BYTES = 32 * 1024 * 1024


class ContentDriftRefused(RuntimeError):
    """The run cannot produce an answer, and says which condition stopped it."""

    def __init__(self, condition: str, message: str) -> None:
        super().__init__(message)
        self.condition = condition


# --------------------------------------------------------------------------
# Offline git reads
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UpstreamFile:
    """One blob in the tree of the revision under comparison."""

    path: str
    object_name: str
    size: int


def _git_bytes(root: Path, arguments: Sequence[str], *, stdin: bytes | None = None) -> bytes:
    """Run one git command against ``root``'s own object database."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            input=stdin,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ContentDriftRefused("git-unavailable", f"git is unavailable for {root}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ContentDriftRefused(
            "git-command-failed",
            f"git {' '.join(arguments)} failed: {detail or completed.returncode}",
        )
    return completed.stdout


def _git_text(root: Path, *arguments: str) -> str:
    return _git_bytes(root, arguments).decode("utf-8", "replace")


def _git_optional(root: Path, *arguments: str) -> str | None:
    """Trimmed output, or ``None`` when git declines to answer."""

    try:
        return _git_text(root, *arguments).strip()
    except ContentDriftRefused:
        return None


def resolve_commit(root: Path, revision: str) -> str:
    """The 40-hex commit a revision names in this checkout, or a refusal."""

    resolved = _git_optional(root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if not resolved or re.fullmatch(r"[0-9a-f]{40}", resolved) is None:
        raise ContentDriftRefused(
            "revision-unresolvable",
            f"{revision!r} names no commit in the checkout; refresh it with "
            "`make content-checkout`, or name a --revision it holds",
        )
    return resolved


def origin_url(root: Path) -> str:
    """The checkout's ``origin`` URL, read before anything in it is parsed."""

    value = _git_optional(root, "config", "--get", "remote.origin.url")
    if not value:
        raise ContentDriftRefused(
            "checkout-origin-mismatch", "the checkout declares no origin remote"
        )
    return value


def list_tree(root: Path, commit: str) -> tuple[UpstreamFile, ...]:
    """Every blob in the commit's tree, with its object name and its size."""

    raw = _git_bytes(root, ("ls-tree", "-r", "-l", "-z", "--full-tree", commit))
    files: list[UpstreamFile] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, _, path = entry.partition(b"\t")
        fields = header.split()
        # A submodule or a symlink carries no comparable bytes.
        if len(fields) != 4 or fields[1] != b"blob" or fields[0] == b"120000":
            continue
        try:
            size = int(fields[3])
        except ValueError:
            continue
        files.append(
            UpstreamFile(
                path=path.decode("utf-8", "surrogateescape"),
                object_name=fields[2].decode("ascii"),
                size=size,
            )
        )
    return tuple(files)


def _batches(files: Sequence[UpstreamFile]) -> Iterator[list[UpstreamFile]]:
    batch: list[UpstreamFile] = []
    held = 0
    for item in files:
        if batch and held + item.size > BATCH_BYTES:
            yield batch
            batch, held = [], 0
        batch.append(item)
        held += item.size
    if batch:
        yield batch


def iter_blobs(
    root: Path, files: Sequence[UpstreamFile]
) -> Iterator[tuple[UpstreamFile, bytes]]:
    """Yield every named blob's bytes, one bounded batch at a time.

    One ``git cat-file --batch`` per batch rather than one process per file -- a real
    checkout holds well over a thousand in-scope blobs -- and a batch at a time rather
    than all at once, so 150 MB of images is never all in memory.
    """

    for batch in _batches(files):
        by_object: dict[str, list[UpstreamFile]] = {}
        for item in batch:
            by_object.setdefault(item.object_name, []).append(item)
        wanted = sorted(by_object)
        stream = _git_bytes(
            root,
            ("cat-file", "--batch"),
            stdin=("\n".join(wanted) + "\n").encode("ascii"),
        )
        offset = 0
        for _ in wanted:
            end = stream.find(b"\n", offset)
            if end < 0:
                raise ContentDriftRefused("git-object-unreadable", "cat-file output is truncated")
            header = stream[offset:end].decode("ascii", "replace").split()
            if len(header) != 3:
                raise ContentDriftRefused("git-object-unreadable", "cat-file refused an object")
            payload_start = end + 1
            payload_end = payload_start + int(header[2])
            payload = stream[payload_start:payload_end]
            for item in by_object[header[0]]:
                yield item, payload
            offset = payload_end + 1


# --------------------------------------------------------------------------
# The checkout under comparison
# --------------------------------------------------------------------------


def verify_checkout(checkout: Path, *, owner: str, name: str) -> str:
    """Refuse anything that is not this repository, before reading a byte of it.

    Deliberately *not* ``verify_dtc_content_checkout``'s refusals: this reads a named
    revision out of the object database, so an uncommitted edit or a HEAD parked
    somewhere else changes nothing about the answer and is none of its business.
    """

    from content_sync.course_repository_checkout import public_repository_urls

    if not checkout.is_absolute():
        raise ContentDriftRefused("checkout-not-absolute", "--checkout must be an absolute path")
    if checkout.is_symlink():
        raise ContentDriftRefused("checkout-symlink", "--checkout must not be a symlink")
    if not checkout.is_dir():
        raise ContentDriftRefused("checkout-missing", f"--checkout {checkout} is not a directory")
    url = origin_url(checkout)
    if url.strip().rstrip("/").casefold() not in public_repository_urls(owner, name):
        raise ContentDriftRefused(
            "checkout-origin-mismatch",
            f"the checkout's origin is not {owner}/{name}; nothing in it was read",
        )
    return url


# --------------------------------------------------------------------------
# The served side
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ServedCatalogue:
    """What the active release publishes, keyed the way upstream is keyed."""

    source_stable_id: str
    release_id: str
    families: dict[str, dict[str, str]] = field(default_factory=dict)
    revisions: set[str] = field(default_factory=set)


def select_editorial_source(*, repository: str = EDITORIAL_REPOSITORY) -> Any:
    """The registered enabled source for the editorial repository.

    Which repository this is stays a database question. A hardcoded stable id would
    make a differently registered database report every record as missing, and would
    make this script a second place that decides which repositories exist.
    """

    from content.models import ContentSource

    owner, _, name = repository.partition("/")
    registered = list(
        ContentSource.objects.filter(
            enabled=True,
            repository_owner__iexact=owner,
            repository_name__iexact=name,
        ).order_by("stable_id")
    )
    if not registered:
        raise ContentDriftRefused(
            "editorial-source-missing",
            f"no enabled ContentSource is registered for {repository}; import the "
            "public content with `scripts/prod/import_public_content.py` first",
        )
    if len(registered) > 1:
        named = ", ".join(source.stable_id for source in registered)
        raise ContentDriftRefused(
            "editorial-source-ambiguous",
            f"more than one enabled ContentSource claims {repository}: {named}",
        )
    return registered[0]


def _provenance_entry(
    record: Mapping[str, Any], key: str, *, identity_field: str
) -> tuple[str, str, str] | None:
    """``(identity, checksum, revision)`` for one record, or ``None`` when out of scope."""

    provenance = record.get(key)
    if not isinstance(provenance, Mapping):
        return None
    if provenance.get("repository") != EDITORIAL_REPOSITORY:
        return None
    identity = provenance.get(identity_field)
    checksum = provenance.get("checksum")
    revision = provenance.get("revision")
    if not isinstance(identity, str) or not identity:
        raise ContentDriftRefused(
            "served-provenance-incomplete",
            f"a served record carries no {key}.{identity_field}",
        )
    if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
        raise ContentDriftRefused(
            "served-provenance-incomplete",
            f"served record {identity!r} carries no SHA-256 in {key}.checksum",
        )
    return identity, checksum, str(revision or "")


def read_served_catalogue(source: Any) -> ServedCatalogue:
    """Every content-owned record the active release publishes."""

    from content.models import ContentDocument, ContentRelease

    if not source.active_release_id:
        raise ContentDriftRefused(
            "no-active-release",
            f"{source.stable_id} has no active release, so there is nothing served to "
            "compare against; an un-ingested database is not drift",
        )
    catalogue = ServedCatalogue(
        source_stable_id=source.stable_id,
        release_id=str(source.active_release_id),
        families={name: {} for name in FAMILIES},
    )
    rows = ContentDocument.objects.filter(
        release_id=source.active_release_id,
        release__status=ContentRelease.Status.ACTIVE,
        is_published=True,
        content_kind__in=sorted(SERVED_KINDS),
    ).values_list("content_kind", "adapter_metadata")
    for content_kind, metadata in rows.iterator():
        record = (metadata or {}).get("record")
        if not isinstance(record, Mapping):
            continue
        family = SERVED_KINDS[content_kind]
        identity_field = "source_path" if family == "media" else "source_key"
        entry = _provenance_entry(record, "provenance", identity_field=identity_field)
        if entry is not None:
            identity, checksum, revision = entry
            catalogue.families[family][identity] = checksum
            if revision:
                catalogue.revisions.add(revision)
        if family != "podcasts":
            continue
        transcript = _provenance_entry(record, "transcript_provenance", identity_field="source_key")
        if transcript is not None:
            identity, checksum, revision = transcript
            catalogue.families["podcast_transcripts"][identity] = checksum
            if revision:
                catalogue.revisions.add(revision)
    return catalogue


# --------------------------------------------------------------------------
# The upstream side
# --------------------------------------------------------------------------


def _is_draft(path: PurePosixPath) -> bool:
    """A leading underscore marks an unpublished draft, upstream's own convention.

    The same rule the adapter and the projection builder apply, so a draft is absent
    from both sides rather than reported as an upstream record we failed to serve.
    """

    return path.name.startswith("_")


def _identity_key(text: str, *, path: str, key: str) -> str:
    """One key out of a bounded YAML mapping. No adapter, no bundle, no parser fork."""

    from content_sync.dtc_content.adapter import DtcContentValidationError, _load_yaml_mapping
    from content_sync.dtc_content.contract import DTC_CONTENT_CONTRACT

    try:
        mapping = _load_yaml_mapping(text, path=path, contract=DTC_CONTENT_CONTRACT)
    except DtcContentValidationError as error:
        raise ContentDriftRefused(
            "upstream-file-unreadable", f"{path} could not be read: {error}"
        ) from error
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ContentDriftRefused(
            "upstream-file-unreadable", f"{path} declares no {key!r} identity"
        )
    return value


def classify_upstream(files: Iterable[UpstreamFile]) -> dict[str, list[tuple[str, UpstreamFile]]]:
    """Group the in-scope blobs by family, beside the identity a path already carries.

    Layout-tolerant on purpose: an article's identity is its stem after the date and an
    episode's is the key inside it, so ``articles/x.md`` and ``articles/2020/x.md`` are
    the same record, and so are ``podcasts/a.yaml`` and ``podcasts/s01/a.yaml``. That is
    what makes a reorganisation upstream report as the zero drift it is.
    """

    grouped: dict[str, list[tuple[str, UpstreamFile]]] = {name: [] for name in FAMILIES}
    for item in files:
        pure = PurePosixPath(item.path)
        head = pure.parts[0] if pure.parts else ""
        if item.path.startswith(MEDIA_PREFIXES):
            if pure.suffix.lower() in MEDIA_SUFFIXES:
                grouped["media"].append((item.path, item))
            continue
        if _is_draft(pure):
            continue
        if head == "articles" and pure.suffix == ".md":
            match = ARTICLE_DATE_PREFIX.fullmatch(pure.stem)
            if match is not None:
                grouped["articles"].append((match.group(1), item))
            continue
        if head == "books" and pure.suffix == ".yaml":
            grouped["books"].append(("", item))
            continue
        if head == "podcasts" and pure.suffix == ".yaml":
            transcript = "transcripts" in pure.parts[1:-1] or pure.stem.endswith("-transcript")
            grouped["podcast_transcripts" if transcript else "podcasts"].append(("", item))
    return grouped


#: The YAML key each family declares its own identity under. Articles and media take
#: theirs from the path, so they are absent here.
IDENTITY_KEYS = {"podcasts": "slug", "podcast_transcripts": "podcast", "books": "slug"}


def read_upstream_catalogue(
    checkout: Path, commit: str, *, contract: Any
) -> dict[str, dict[str, str]]:
    """Identity to SHA-256 of the file bytes, per family, at one revision."""

    grouped = classify_upstream(list_tree(checkout, commit))
    in_scope = [item for entries in grouped.values() for _identity, item in entries]
    if len(in_scope) > contract.max_files:
        raise ContentDriftRefused(
            "upstream-ceiling-exceeded",
            f"{commit[:12]} holds {len(in_scope)} in-scope files, over the "
            f"{contract.max_files} the contract admits",
        )
    oversized = next((item for item in in_scope if item.size > contract.max_file_bytes), None)
    if oversized is not None:
        raise ContentDriftRefused(
            "upstream-ceiling-exceeded",
            f"{oversized.path} is {oversized.size} bytes, over the contract ceiling",
        )
    total = sum(item.size for item in in_scope)
    if total > contract.max_source_bytes:
        raise ContentDriftRefused(
            "upstream-ceiling-exceeded",
            f"{commit[:12]} holds {total} in-scope bytes, over the contract ceiling",
        )
    placed = {
        item.path: (family, identity)
        for family, entries in grouped.items()
        for identity, item in entries
    }
    catalogue: dict[str, dict[str, str]] = {name: {} for name in FAMILIES}
    for item, payload in iter_blobs(checkout, in_scope):
        family, identity = placed[item.path]
        key = IDENTITY_KEYS.get(family)
        if key is not None:
            identity = _identity_key(payload.decode("utf-8", "replace"), path=item.path, key=key)
        catalogue[family][identity] = hashlib.sha256(payload).hexdigest()
    return catalogue


# --------------------------------------------------------------------------
# The diff
# --------------------------------------------------------------------------


def _bucket(values: Iterable[str]) -> tuple[list[str], int]:
    held = sorted(values)
    return held[:BUCKET_LIMIT], len(held)


def compare_family(
    served: Mapping[str, str], upstream: Mapping[str, str], *, asymmetric: bool
) -> dict[str, Any]:
    """One family's counts, identities and digests, from one algorithm.

    ``asymmetric`` is media: an upstream image nothing references was never published,
    so it is reported as ``upstream_unreferenced`` rather than as a record that never
    arrived. Media's ``missing`` bucket is therefore always empty, and kept only so
    that every family reads the same way.
    """

    absent = set(upstream) - set(served)
    extra = set(served) - set(upstream)
    shared = set(served) & set(upstream)
    mismatched = {identity for identity in shared if served[identity] != upstream[identity]}
    missing = set() if asymmetric else absent

    missing_list, missing_count = _bucket(missing)
    extra_list, extra_count = _bucket(extra)
    mismatched_list, mismatched_count = _bucket(mismatched)
    family: dict[str, Any] = {
        "total": len(served),
        "upstream_total": len(upstream),
        "matched": len(shared) - len(mismatched),
        "missing": missing_list,
        "missing_count": missing_count,
        "extra": extra_list,
        "extra_count": extra_count,
        "mismatched": mismatched_list,
        "mismatched_count": mismatched_count,
        "clean": not (missing or extra or mismatched),
    }
    if asymmetric:
        unreferenced_list, unreferenced_count = _bucket(absent)
        family["upstream_unreferenced"] = unreferenced_list
        family["upstream_unreferenced_count"] = unreferenced_count
    return family


def revision_status(
    checkout: Path,
    *,
    served_revisions: Iterable[str],
    remote_head: str | None,
    owner: str,
    name: str,
) -> dict[str, Any]:
    """Whether each revision the served records name is still upstream's history.

    Reachability is read from the checkout's own remote-tracking branches, exactly as
    ``commit_is_public`` reads it, so this stays offline. A revision that exists only in
    somebody's local working copy is ``unreachable`` -- drift class (d) in
    ``_docs/runbooks/data-ingest.md`` §10.2 -- and no amount of matching bytes makes the
    catalogue reproducible from upstream.
    """

    from content_sync.course_repository_checkout import commit_is_public

    entries: list[dict[str, Any]] = []
    for revision in sorted(set(served_revisions)):
        reachable = commit_is_public(checkout, owner=owner, name=name, commit_sha=revision)
        behind: int | None = None
        if reachable and remote_head is not None:
            counted = _git_optional(checkout, "rev-list", "--count", f"{revision}..{remote_head}")
            behind = int(counted) if counted and counted.isdigit() else None
        entries.append({"revision": revision, "reachable": reachable, "commits_behind": behind})
    if any(not entry["reachable"] for entry in entries):
        state = "unreachable"
    elif any(entry["commits_behind"] for entry in entries):
        state = "behind"
    else:
        state = "clean"
    return {
        "state": state,
        "commits_behind": max((entry["commits_behind"] or 0 for entry in entries), default=0),
        "revisions": entries,
    }


def build_report(*, source: Any, checkout: Path, revision: str | None) -> dict[str, Any]:
    """The whole answer, as one JSON-ready mapping. Writes nothing.

    The checkout path itself is deliberately absent from the report: an operator pastes
    this into an issue, and a home directory names a person.
    """

    from content_sync.dtc_content.contract import DTC_CONTENT_CONTRACT

    owner, name = source.repository_owner, source.repository_name
    origin = verify_checkout(checkout, owner=owner, name=name)
    tracking = f"refs/remotes/origin/{source.branch}"
    commit = resolve_commit(checkout, revision or tracking)
    remote_head = _git_optional(
        checkout, "rev-parse", "--verify", "--quiet", f"{tracking}^{{commit}}"
    )

    served = read_served_catalogue(source)
    upstream = read_upstream_catalogue(checkout, commit, contract=DTC_CONTENT_CONTRACT)
    families = {
        family: compare_family(
            served.families[family], upstream[family], asymmetric=family == "media"
        )
        for family in FAMILIES
    }
    status = revision_status(
        checkout,
        served_revisions=served.revisions,
        remote_head=remote_head,
        owner=owner,
        name=name,
    )
    return {
        "checkout": {
            "origin": origin,
            "revision": commit,
            "remote_head": remote_head,
            "branch": source.branch,
        },
        "served": {
            "source": served.source_stable_id,
            "release_id": served.release_id,
            "revisions": sorted(served.revisions),
        },
        "revision_status": status,
        "families": families,
        "clean": (
            status["state"] == "clean" and all(family["clean"] for family in families.values())
        ),
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report drift between served editorial content and DataTalksClub/content."
    )
    add_target_arguments(parser)
    parser.add_argument(
        "--checkout",
        required=True,
        type=Path,
        metavar="PATH",
        help=(
            "Absolute path to a checkout of the editorial repository. Only its object "
            "database is read; the working copy and HEAD are not consulted."
        ),
    )
    parser.add_argument(
        "--revision",
        default=None,
        metavar="REV",
        help=(
            "The revision to compare against. Defaults to the checkout's own "
            "remote-tracking branch for the registered branch, resolved locally."
        ),
    )
    parser.add_argument(
        "--checkout-plan",
        action="store_true",
        help=(
            "Print the registered editorial source, its repository, its branch and the "
            "checkout it would be read from, then exit without reading or writing anything."
        ),
    )
    return parser


def run_from_args(args: argparse.Namespace) -> int:
    """Everything after the target is configured, so a test can drive it directly."""

    try:
        source = select_editorial_source()
        checkout = args.checkout.expanduser()
        if args.checkout_plan:
            print(
                f"{source.stable_id}\t{source.repository_owner}/{source.repository_name}"
                f"\t{source.branch}\t{checkout}"
            )
            return 0
        report = build_report(source=source, checkout=checkout, revision=args.revision)
    except ContentDriftRefused as refusal:
        print(
            json.dumps({"condition": refusal.condition, "error": str(refusal)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["clean"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)
    return run_from_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
