#!/usr/bin/env python3
"""Recompute only the derived digest/scope fields of a checked projection manifest.

The checked public projection is not currently reproducible from upstream (issue #253),
so moving the complete-tree digest to its media-free scope must not go through a full
rebuild: that would rewrite every record and silently adopt unrelated drift.  This
utility recomputes ``tree_sha256`` from the already-checked artifacts using the builder's
own walk, writes the machine-readable ``tree_digest_scope`` and ``media_storage``
declarations, and touches nothing else.

    uv run python scripts/repin_projection_digests.py --check
    uv run python scripts/repin_projection_digests.py --write

``--check`` exits non-zero when the manifest does not already carry the recomputed
values, which is what CI and the focused tests use.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.build_public_projection import (  # noqa: E402
    TREE_DIGEST_SCOPE,
    _tree_sha256,
)

DEFAULT_PROJECTION_ROOT = REPOSITORY_ROOT / "temporary" / "content" / "public_projection"
#: The only manifest keys this utility is permitted to introduce or change.
DERIVED_FIELDS = ("tree_sha256", "tree_digest_scope", "media_storage")


class RepinError(RuntimeError):
    """The manifest cannot be re-pinned safely."""


def derived_fields(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or not isinstance(counts.get("media"), int):
        raise RepinError("manifest has no media count to declare")
    return {
        "tree_sha256": _tree_sha256(root),
        "tree_digest_scope": TREE_DIGEST_SCOPE,
        "media_storage": {
            "location": "object-store",
            "records": "media.json",
            "count": counts["media"],
            "integrity": "per-record provenance.checksum",
        },
    }


def repin(root: Path, *, write: bool) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    try:
        original = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(original)
    except (OSError, json.JSONDecodeError) as error:
        raise RepinError("manifest cannot be read") from error
    if not isinstance(manifest, dict):
        raise RepinError("manifest is not an object")

    expected = derived_fields(root, manifest)
    changed = [name for name in DERIVED_FIELDS if manifest.get(name) != expected[name]]
    updated = {**manifest, **expected}
    encoded = json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    if write and (changed or encoded != original):
        manifest_path.write_text(encoded, encoding="utf-8")
    return {
        "changed_fields": changed,
        "tree_sha256": expected["tree_sha256"],
        "written": bool(write and (changed or encoded != original)),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--projection-root", type=Path, default=DEFAULT_PROJECTION_ROOT)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify without writing")
    mode.add_argument("--write", action="store_true", help="rewrite the derived fields")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        report = repin(arguments.projection_root, write=arguments.write)
    except RepinError as error:
        print(f"projection digest re-pin failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    if arguments.check and report["changed_fields"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
