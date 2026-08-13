"""Scan bounded test/browser/observability artifacts for redaction canaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


class ArtifactScanError(RuntimeError):
    """An artifact contained a secret/PII canary or crossed a file boundary."""


def _assert_no_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ArtifactScanError("artifact input must not contain symlink components")


def _artifact_files(inputs: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for raw_path in inputs:
        # Check the caller-provided path before resolving it.  Resolving first
        # would turn a symlink input into its target and silently widen the
        # evidence boundary.
        _assert_no_symlink_components(raw_path)
        path = raw_path.resolve(strict=True)
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                raise ArtifactScanError("artifact tree contains a symlink")
            if candidate.is_file():
                files.append(candidate)
    return files


def scan_artifacts(inputs: tuple[Path, ...], *, canaries: tuple[str, ...] = ()) -> dict[str, Any]:
    if not inputs:
        raise ArtifactScanError("at least one artifact input is required")
    if not canaries:
        raise ArtifactScanError("at least one artifact canary is required")
    if any(not isinstance(canary, str) or not canary for canary in canaries):
        raise ArtifactScanError("artifact canaries must be non-empty strings")
    files = _artifact_files(inputs)
    if not files:
        raise ArtifactScanError("artifact input contains no regular files")
    hits: list[dict[str, object]] = []
    bytes_scanned = 0
    for path in files:
        size = path.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise ArtifactScanError("artifact exceeds the configured scan limit")
        content = path.read_bytes()
        bytes_scanned += len(content)
        for index, canary in enumerate(canaries):
            if canary.encode("utf-8") in content:
                # Record only an ordinal and path.  Never put the secret value
                # into a failure artifact or CI log.
                hits.append({"canary_index": index, "path": str(path)})
    if hits:
        raise ArtifactScanError(f"artifact redaction scan found {len(hits)} canary hit(s)")
    return {
        "artifact_count": len(files),
        "bytes_scanned": bytes_scanned,
        "canary_count": len(canaries),
        "schema_version": 1,
        "status": "pass",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, dest="inputs", type=Path)
    parser.add_argument("--canary", action="append", default=[], dest="canaries")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = scan_artifacts(tuple(args.inputs), canaries=tuple(args.canaries))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
