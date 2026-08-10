#!/usr/bin/env python3
"""Verify locally served Course Platform vendor assets and their provenance."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_support.provenance import (  # noqa: E402
    FixtureProvenanceError,
    validate_public_fixture,
)

MANIFEST_PATH = ROOT / "_docs/provenance/course-platform-vendor-assets.json"
VENDOR_ROOT = ROOT / "core/static/core/vendor"
TAILWIND_COMMIT = "4f9f603e12b51cc53b8a09c7739b8f88c8eb87eb"
FONTAWESOME_COMMIT = "57005cea6da7d1c67f3466974aecd25485f60452"
TAILWIND_REPOSITORY = "https://github.com/tailwindlabs/tailwindcss.git"
FONTAWESOME_REPOSITORY = "https://github.com/FortAwesome/Font-Awesome.git"
ASSET_FIELDS = {
    "accepted_fixture_path",
    "artifact_url",
    "license",
    "source_commit",
    "source_path",
    "source_repository",
    "source_sha256",
    "target_path",
    "target_sha256",
    "transformation",
}
FONT_FAMILIES = ("fa-brands-400", "fa-regular-400", "fa-solid-900")
FONT_EXTENSIONS = ("eot", "svg", "ttf", "woff", "woff2")
FONTAWESOME_ROOT = "core/static/core/vendor/fontawesome-free-5.15.1"
TAILWIND_ROOT = "core/static/core/vendor/tailwindcss-3.4.17"
EXPECTED_TARGETS = {
    f"{TAILWIND_ROOT}/LICENSE",
    f"{TAILWIND_ROOT}/tailwindcss-3.4.17.js",
    f"{FONTAWESOME_ROOT}/LICENSE.txt",
    f"{FONTAWESOME_ROOT}/css/all.min.css",
    *(
        f"{FONTAWESOME_ROOT}/webfonts/{family}.{extension}"
        for family in FONT_FAMILIES
        for extension in FONT_EXTENSIONS
    ),
}
CSS_URL_RE = re.compile(r"url\(([^)]+)\)")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VendorAssetProvenanceError(ValueError):
    """The vendored asset inventory is incomplete, unsafe, or not reproducible."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise VendorAssetProvenanceError(f"{field} must be a non-empty POSIX path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise VendorAssetProvenanceError(f"{field} must be a safe relative path")
    return value


def _expected_source(asset: dict[str, Any]) -> tuple[str, str]:
    target = asset["target_path"]
    if target.startswith(f"{TAILWIND_ROOT}/"):
        return TAILWIND_REPOSITORY, TAILWIND_COMMIT
    if target.startswith(f"{FONTAWESOME_ROOT}/"):
        return FONTAWESOME_REPOSITORY, FONTAWESOME_COMMIT
    raise VendorAssetProvenanceError("target is outside the exact vendor roots")


def _validate_artifact_url(asset: dict[str, Any]) -> None:
    try:
        parsed = urlsplit(asset["artifact_url"])
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise VendorAssetProvenanceError("artifact URL is malformed") from error
    if (
        parsed.scheme != "https"
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise VendorAssetProvenanceError("artifact URL must be one immutable HTTPS URL")
    if parsed.hostname == "raw.githubusercontent.com":
        repository = asset["source_repository"]
        repository_path = repository.removeprefix("https://github.com/").removesuffix(".git")
        expected_path = f"/{repository_path}/{asset['source_commit']}/{asset['source_path']}"
        if parsed.path != expected_path:
            raise VendorAssetProvenanceError(
                "raw artifact URL does not match source repository, commit, and path"
            )
        return
    if parsed.hostname == "cdn.tailwindcss.com" and parsed.path == "/3.4.17":
        return
    if parsed.hostname == "cdnjs.cloudflare.com" and parsed.path.startswith(
        "/ajax/libs/font-awesome/5.15.1/webfonts/"
    ):
        return
    raise VendorAssetProvenanceError("artifact URL is outside the exact approved sources")


def _fixture_provenance_path(fixture: Path) -> Path:
    name = fixture.name
    if name == "tailwindcss-3.4.17.js":
        provenance_name = "tailwindcss-3.4.17.provenance.json"
    elif name == "fontawesome-5.15.1-all.min.css":
        provenance_name = "fontawesome-5.15.1-all.min.provenance.json"
    elif name.endswith(".woff2"):
        provenance_name = f"{name.removesuffix('.woff2')}.provenance.json"
    elif name.endswith(".woff"):
        provenance_name = f"{name.removesuffix('.woff')}-woff.provenance.json"
    else:
        raise VendorAssetProvenanceError("accepted fixture type is not allowlisted")
    return fixture.with_name(provenance_name)


def _validate_fixture(root: Path, asset: dict[str, Any]) -> None:
    fixture_value = asset["accepted_fixture_path"]
    if fixture_value is None:
        return
    fixture_relative = _safe_relative(fixture_value, field="accepted_fixture_path")
    if not fixture_relative.startswith("test_support/fixtures/public/"):
        raise VendorAssetProvenanceError("accepted fixture is outside the public fixture root")
    fixture = root / fixture_relative
    provenance_path = _fixture_provenance_path(fixture)
    try:
        provenance = validate_public_fixture(fixture, provenance_path)
    except FixtureProvenanceError as error:
        raise VendorAssetProvenanceError("accepted fixture provenance is invalid") from error
    if (
        provenance.repository != asset["source_repository"]
        or provenance.commit != asset["source_commit"]
        or provenance.source_path != asset["source_path"]
        or provenance.artifact_url != asset["artifact_url"]
        or provenance.byte_sha256 != asset["source_sha256"]
    ):
        raise VendorAssetProvenanceError("accepted fixture differs from vendor provenance")


def _load_assets(manifest_path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VendorAssetProvenanceError("vendor provenance manifest is malformed") from error
    if not isinstance(payload, dict) or set(payload) != {"assets", "schema_version"}:
        raise VendorAssetProvenanceError("vendor provenance manifest fields are not exact")
    if payload["schema_version"] != 1 or not isinstance(payload["assets"], list):
        raise VendorAssetProvenanceError("vendor provenance schema version differs")
    assets = payload["assets"]
    if any(not isinstance(asset, dict) or set(asset) != ASSET_FIELDS for asset in assets):
        raise VendorAssetProvenanceError("vendor asset fields are not exact")
    return assets


def verify(
    root: Path = ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[int, int, int]:
    assets = _load_assets(manifest_path)
    targets: set[str] = set()
    for asset in assets:
        target_relative = _safe_relative(asset["target_path"], field="target_path")
        source_relative = _safe_relative(asset["source_path"], field="source_path")
        del source_relative
        if target_relative in targets:
            raise VendorAssetProvenanceError("vendor target paths must be unique")
        targets.add(target_relative)
        repository, commit = _expected_source(asset)
        if asset["source_repository"] != repository or asset["source_commit"] != commit:
            raise VendorAssetProvenanceError("vendor source repository or commit differs")
        if (
            not SHA256_RE.fullmatch(str(asset["source_sha256"]))
            or asset["source_sha256"] != asset["target_sha256"]
            or asset["transformation"] != "none"
            or not isinstance(asset["license"], str)
            or not asset["license"]
        ):
            raise VendorAssetProvenanceError(
                "vendor digests, license, or transformation are invalid"
            )
        _validate_artifact_url(asset)
        target = root / target_relative
        if target.is_symlink() or not target.is_file() or digest(target) != asset["target_sha256"]:
            raise VendorAssetProvenanceError("vendor target bytes differ from provenance")
        _validate_fixture(root, asset)

    if targets != EXPECTED_TARGETS:
        raise VendorAssetProvenanceError("vendor target inventory differs")
    actual_targets = {
        path.relative_to(root).as_posix()
        for path in (root / "core/static/core/vendor").rglob("*")
        if path.is_file()
    }
    if actual_targets != EXPECTED_TARGETS:
        raise VendorAssetProvenanceError("vendor directory has missing or unrecorded files")

    css_relative = f"{FONTAWESOME_ROOT}/css/all.min.css"
    css = (root / css_relative).read_text(encoding="utf-8")
    references = tuple(match.group(1).strip("\"'") for match in CSS_URL_RE.finditer(css))
    resolved: set[str] = set()
    for reference in references:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            raise VendorAssetProvenanceError("vendor CSS contains an external asset reference")
        destination = posixpath.normpath(
            posixpath.join(posixpath.dirname(css_relative), parsed.path)
        )
        if destination not in targets:
            raise VendorAssetProvenanceError("vendor CSS reference is unresolved")
        resolved.add(destination)
    expected_fonts = {
        f"{FONTAWESOME_ROOT}/webfonts/{family}.{extension}"
        for family in FONT_FAMILIES
        for extension in FONT_EXTENSIONS
    }
    if resolved != expected_fonts:
        raise VendorAssetProvenanceError("vendor CSS does not reference every recorded font")
    return len(assets), len(references), len(resolved)


def main() -> None:
    asset_count, reference_count, font_count = verify()
    print(
        f"verified {asset_count} local vendor assets, "
        f"{reference_count} CSS URLs, and {font_count} font files"
    )


if __name__ == "__main__":
    main()
