from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .adapter import CandidateBundle, DtcContentValidationError, adapt_dtc_content_checkout
from .contract import (
    ACCEPTED_CONTENT_COMMIT,
    DTC_CONTENT_CONTRACT,
    EDITORIAL_OVERLAY_PATH,
    REPAIR_MANIFEST_PATH,
    DtcContentAdapterContract,
)
from .parity import ProjectionParityEvidence, verify_initial_projection_parity

_ALLOWED_ORIGINS = frozenset(
    {
        "git@github.com:DataTalksClub/content.git",
        "https://github.com/DataTalksClub/content",
        "https://github.com/DataTalksClub/content.git",
    }
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MATERIALIZATION_ROOT = _PROJECT_ROOT / ".tmp" / "content-verification"
_GIT_EXECUTABLE = shutil.which("git", path=os.defpath) or "git"
_GIT_CONFIGURATION = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "diff.external=",
    "-c",
    f"core.attributesFile={os.devnull}",
    "-c",
    "protocol.file.allow=never",
)
_GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_EXTERNAL_DIFF": "",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_PROTOCOL_FROM_USER": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": os.defpath,
}
_TREE_ROOTS = (
    "migration.yaml",
    "articles",
    "podcasts",
    "books",
    "images/posts",
    "images/podcast",
    "images/books",
)


class DtcContentCheckoutError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VerifiedCheckout:
    commit_sha: str
    tree_sha: str
    origin: str
    bundle: CandidateBundle
    projection_parity: ProjectionParityEvidence | None


@dataclass(frozen=True, slots=True)
class _TreeBlob:
    object_sha: str
    size: int


def _git_command(root: Path, arguments: tuple[str, ...]) -> tuple[str, ...]:
    # The fixed command prefix and non-inherited environment apply to every Git
    # query in this verifier. None of the selected plumbing commands invokes
    # checkout filters or lazy promisor fetches; hooks, fsmonitor, attributes,
    # external diffs, pagers, prompts, and ambient global/system configuration
    # are disabled explicitly.
    return (
        _GIT_EXECUTABLE,
        *_GIT_CONFIGURATION,
        "-C",
        str(root),
        *arguments,
    )


def _git(root: Path, *arguments: str, failure_code: str = "git_verification_failed") -> str:
    try:
        result = subprocess.run(
            _git_command(root, arguments),
            check=False,
            capture_output=True,
            env=_GIT_ENVIRONMENT,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DtcContentCheckoutError(failure_code) from error
    if result.returncode != 0:
        raise DtcContentCheckoutError(failure_code)
    return result.stdout.strip()


def _git_bytes(
    root: Path,
    *arguments: str,
    failure_code: str = "git_verification_failed",
    timeout: int = 30,
) -> bytes:
    try:
        result = subprocess.run(
            _git_command(root, arguments),
            check=False,
            capture_output=True,
            env=_GIT_ENVIRONMENT,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DtcContentCheckoutError(failure_code) from error
    if result.returncode != 0:
        raise DtcContentCheckoutError(failure_code)
    return result.stdout


def _safe_tree_path(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DtcContentCheckoutError("git_tree_path_invalid") from error
    else:
        value = raw
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise DtcContentCheckoutError("git_tree_path_invalid")
    return value


def _tree_inventory(
    root: Path,
    *,
    commit_sha: str,
    contract: DtcContentAdapterContract,
) -> dict[str, _TreeBlob]:
    selected_roots: tuple[str, ...] = _TREE_ROOTS
    if commit_sha == ACCEPTED_CONTENT_COMMIT:
        selected_roots = (*selected_roots, REPAIR_MANIFEST_PATH, EDITORIAL_OVERLAY_PATH)
    payload = _git_bytes(
        root,
        "ls-tree",
        "-rlz",
        "--full-tree",
        commit_sha,
        "--",
        *selected_roots,
        failure_code="git_tree_inventory_unreadable",
    )
    inventory: dict[str, _TreeBlob] = {}
    total_size = 0
    for raw_record in payload.split(b"\0"):
        if not raw_record:
            continue
        try:
            raw_metadata, raw_path = raw_record.split(b"\t", 1)
            mode, object_type, raw_object_sha, raw_size = raw_metadata.split()
        except ValueError as error:
            raise DtcContentCheckoutError("git_tree_inventory_invalid") from error
        path = _safe_tree_path(raw_path)
        # Legacy content includes a few executable bits. Only blob bytes are
        # materialized, always as newly-created regular non-executable files.
        # Symlinks and gitlinks remain fail-closed.
        if mode not in {b"100644", b"100755"} or object_type != b"blob":
            raise DtcContentCheckoutError("git_tree_entry_mode_invalid")
        if len(raw_object_sha) != 40 or any(
            character not in b"0123456789abcdef" for character in raw_object_sha
        ):
            raise DtcContentCheckoutError("git_tree_inventory_invalid")
        try:
            size = int(raw_size)
        except ValueError as error:
            raise DtcContentCheckoutError("git_tree_inventory_invalid") from error
        if path in inventory or size < 0 or size > contract.max_file_bytes:
            raise DtcContentCheckoutError("git_tree_inventory_invalid")
        inventory[path] = _TreeBlob(object_sha=raw_object_sha.decode("ascii"), size=size)
        total_size += size
        if len(inventory) > contract.max_files + 3:
            raise DtcContentCheckoutError("git_tree_file_count_exceeded")
        if total_size > contract.max_source_bytes + (3 * contract.max_file_bytes):
            raise DtcContentCheckoutError("git_tree_byte_limit_exceeded")
    required = {"migration.yaml"}
    if commit_sha == ACCEPTED_CONTENT_COMMIT:
        required.update({REPAIR_MANIFEST_PATH, EDITORIAL_OVERLAY_PATH})
    if not required.issubset(inventory):
        raise DtcContentCheckoutError("git_tree_inventory_invalid")
    return inventory


def _materialize_commit_tree(
    repository_root: Path,
    destination: Path,
    *,
    inventory: dict[str, _TreeBlob],
) -> None:
    requests = b"".join(entry.object_sha.encode("ascii") + b"\n" for entry in inventory.values())
    try:
        result = subprocess.run(
            _git_command(repository_root, ("cat-file", "--batch")),
            check=False,
            capture_output=True,
            env=_GIT_ENVIRONMENT,
            input=requests,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DtcContentCheckoutError("git_tree_blob_batch_unreadable") from error
    if result.returncode != 0:
        raise DtcContentCheckoutError("git_tree_blob_batch_unreadable")
    batch = result.stdout
    maximum_batch_bytes = sum(entry.size + 96 for entry in inventory.values())
    if len(batch) > maximum_batch_bytes:
        raise DtcContentCheckoutError("git_tree_blob_batch_invalid")
    destination.mkdir()
    for directory in (
        "articles",
        "podcasts/transcripts",
        "books",
        "images/posts",
        "images/podcast",
        "images/books",
    ):
        (destination / directory).mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    position = 0
    try:
        for path, entry in inventory.items():
            header_end = batch.find(b"\n", position, position + 96)
            if header_end < 0:
                raise DtcContentCheckoutError("git_tree_blob_batch_invalid")
            header = batch[position:header_end].split()
            expected_sha = entry.object_sha.encode("ascii")
            expected_size = str(entry.size).encode("ascii")
            if header != [expected_sha, b"blob", expected_size]:
                raise DtcContentCheckoutError("git_tree_blob_batch_invalid")
            content_start = header_end + 1
            content_end = content_start + entry.size
            if content_end >= len(batch) or batch[content_end : content_end + 1] != b"\n":
                raise DtcContentCheckoutError("git_tree_blob_batch_invalid")
            data = batch[content_start:content_end]
            object_header = f"blob {entry.size}\0".encode("ascii")
            digest = hashlib.sha1(object_header, usedforsecurity=False)
            digest.update(data)
            actual_sha = digest.hexdigest()
            if actual_sha != entry.object_sha:
                raise DtcContentCheckoutError("git_tree_blob_batch_invalid")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            seen.add(path)
            position = content_end + 1
    except OSError as error:
        raise DtcContentCheckoutError("git_tree_blob_batch_invalid") from error
    if seen != set(inventory) or position != len(batch):
        raise DtcContentCheckoutError("git_tree_blob_batch_invalid")


def verify_dtc_content_checkout(
    checkout_root: Path,
    *,
    expected_commit: str,
    contract: DtcContentAdapterContract = DTC_CONTENT_CONTRACT,
) -> VerifiedCheckout:
    """Fail closed on origin, identity, dirtiness, and adapter drift without networking."""

    try:
        contract.validate_commit(expected_commit)
    except ValueError as error:
        raise DtcContentCheckoutError("expected_commit_invalid") from error
    root = Path(checkout_root)
    if not root.is_absolute():
        raise DtcContentCheckoutError("checkout_path_not_absolute")
    try:
        if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
            raise DtcContentCheckoutError("checkout_directory_invalid")
    except OSError as error:
        raise DtcContentCheckoutError("checkout_directory_invalid") from error
    top_level = Path(_git(root, "rev-parse", "--show-toplevel"))
    try:
        if top_level.resolve(strict=True) != root.resolve(strict=True):
            raise DtcContentCheckoutError("checkout_root_mismatch")
    except OSError as error:
        raise DtcContentCheckoutError("checkout_root_mismatch") from error
    commit_sha = _git(root, "rev-parse", "HEAD")
    if commit_sha != expected_commit:
        raise DtcContentCheckoutError("checkout_commit_mismatch")
    tree_sha = _git(root, "rev-parse", f"{expected_commit}^{{tree}}")
    origin = _git(
        root,
        "remote",
        "get-url",
        "origin",
        failure_code="checkout_origin_mismatch",
    )
    if origin not in _ALLOWED_ORIGINS:
        raise DtcContentCheckoutError("checkout_origin_mismatch")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise DtcContentCheckoutError("checkout_dirty")
    inventory = _tree_inventory(
        root,
        commit_sha=expected_commit,
        contract=contract,
    )
    _MATERIALIZATION_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="git-tree-", dir=_MATERIALIZATION_ROOT) as temporary:
        immutable_root = Path(temporary) / "content"
        _materialize_commit_tree(
            root,
            immutable_root,
            inventory=inventory,
        )
        try:
            bundle = adapt_dtc_content_checkout(
                immutable_root,
                commit_sha=commit_sha,
                source_tree_sha=tree_sha,
                contract=contract,
            )
        except DtcContentValidationError:
            raise
    projection_parity = (
        verify_initial_projection_parity(bundle) if commit_sha == ACCEPTED_CONTENT_COMMIT else None
    )
    return VerifiedCheckout(
        commit_sha=commit_sha,
        tree_sha=tree_sha,
        origin=origin,
        bundle=bundle,
        projection_parity=projection_parity,
    )
