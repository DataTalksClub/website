#!/usr/bin/env python3
"""Build a one-shot, synthetic issue #237 review database below ``.tmp``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = (PROJECT_ROOT / ".tmp").resolve()


def _scratch_path(raw: str) -> Path:
    path = (PROJECT_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if not path.is_relative_to(SCRATCH_ROOT):
        raise ValueError("review database must be below the project-local .tmp directory")
    return path


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=".tmp/design-review/issue-237.sqlite3",
        help="Fresh SQLite target below .tmp (default: %(default)s).",
    )
    parser.add_argument(
        "--manifest",
        default=".tmp/design-review/issue-237-manifest.json",
        help="Redacted route/persona manifest below .tmp (default: %(default)s).",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Origin encoded into scratch Playwright storage states (default: %(default)s).",
    )
    return parser.parse_args(argv)


def _write_browser_states(review_data, *, base_url: str, output_dir: Path) -> None:
    """Write session-bearing Playwright states without logging their secret values."""

    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.test import Client

    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("base URL must be an HTTP(S) origin without a path")
    output_dir.mkdir(parents=True, exist_ok=True)
    for persona in review_data.personas:
        client = Client()
        client.force_login(get_user_model().objects.get(username=persona.username))
        session_cookie = client.cookies[settings.SESSION_COOKIE_NAME]
        storage_state = {
            "cookies": [
                {
                    "domain": parsed.hostname,
                    "expires": -1,
                    "httpOnly": True,
                    "name": settings.SESSION_COOKIE_NAME,
                    "path": "/",
                    "sameSite": "Lax",
                    "secure": parsed.scheme == "https",
                    "value": session_cookie.value,
                }
            ],
            "origins": [],
        }
        (output_dir / f"{persona.key}.json").write_text(
            json.dumps(storage_state, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def run(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        database = _scratch_path(args.database)
        manifest = _scratch_path(args.manifest)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if database.exists():
        print(f"refusing to overwrite existing review database: {database}", file=sys.stderr)
        return 2

    database.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "DTC_ENVIRONMENT": "local",
            "DTC_SQLITE_PATH": str(database.relative_to(PROJECT_ROOT)),
            "DJANGO_SETTINGS_MODULE": "website.settings.design_review",
        }
    )
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    import django

    django.setup()
    from django.core.management import call_command

    from test_support.design_review_data import seed_design_review_data

    call_command("migrate", interactive=False, verbosity=0)
    review_data = seed_design_review_data()
    storage_dir = database.parent / "browser-state"
    try:
        _write_browser_states(
            review_data,
            base_url=args.base_url,
            output_dir=storage_dir,
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    manifest.write_text(
        json.dumps(review_data.manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "database": str(database.relative_to(PROJECT_ROOT)),
                "manifest": str(manifest.relative_to(PROJECT_ROOT)),
                "personas": len(review_data.personas),
                "storage_state_directory": str(storage_dir.relative_to(PROJECT_ROOT)),
                "surfaces": len(review_data.surfaces),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
