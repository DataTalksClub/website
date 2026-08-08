"""Verify that a built image contains the static manifest required at runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import django
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage

EXPECTED_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"
REQUIRED_ASSET = "courses.css"


def _fail(message: str) -> int:
    print(f"Static manifest verification failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    try:
        django.setup()
        backend = settings.STORAGES["staticfiles"]["BACKEND"]
        if backend != EXPECTED_BACKEND:
            return _fail("staticfiles storage does not use the runtime manifest backend")

        static_root = Path(settings.STATIC_ROOT).resolve()
        manifest_path = static_root / "staticfiles.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return _fail("staticfiles.json is absent or malformed")

        paths = manifest.get("paths")
        if not isinstance(paths, dict) or not isinstance(paths.get(REQUIRED_ASSET), str):
            return _fail(f"staticfiles.json has no {REQUIRED_ASSET} entry")

        resolved_url = staticfiles_storage.url(REQUIRED_ASSET)
        resolved_path = urlsplit(resolved_url).path
        if not resolved_path.startswith(settings.STATIC_URL):
            return _fail(f"resolved {REQUIRED_ASSET} URL is outside STATIC_URL")

        relative_path = resolved_path.removeprefix(settings.STATIC_URL)
        if paths[REQUIRED_ASSET] != relative_path:
            return _fail(f"manifest and storage disagree about {REQUIRED_ASSET}")

        asset_path = (static_root / relative_path).resolve()
        if not asset_path.is_relative_to(static_root) or not asset_path.is_file():
            return _fail(f"resolved {REQUIRED_ASSET} file is absent")
    except Exception as error:
        return _fail(f"{type(error).__name__} while loading the runtime storage contract")

    print(f"Static manifest verified: {REQUIRED_ASSET} -> {relative_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
