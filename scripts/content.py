#!/usr/bin/env python3
"""Run the database-owned content checkout and pull workflows."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
UV: Final = ("uv", "run", "--frozen", "python")


def _python(script: str, *arguments: str) -> tuple[str, ...]:
    return (*UV, script, *arguments)


def _run(
    command: tuple[str, ...],
    *,
    input_text: str | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        input=input_text,
        capture_output=capture_output,
        text=True,
    )


def _database(value: str | None) -> str:
    return value or os.environ.get("CONTENT_DATABASE", ".tmp/local.sqlite3")


def _checkout_root(value: str | None) -> str:
    return value or os.environ.get("CONTENT_CHECKOUT_ROOT", ".tmp/course-checkouts")


def _git_host() -> str:
    return os.environ.get("CONTENT_GIT_HOST", "https://github.com")


def register_sources(database: str) -> None:
    _run(
        _python(
            "scripts/prod/sync_course_repository_sources.py",
            "--database",
            database,
        )
    )


def show_pull_plan(database: str, checkout_root: str) -> None:
    _run(
        _python(
            "scripts/prod/sync_course_repositories.py",
            "--database",
            database,
            "--checkout-plan",
            "--from-disk",
            checkout_root,
        )
    )


def refresh_course_checkouts(database: str, checkout_root: str) -> None:
    Path(checkout_root).mkdir(parents=True, exist_ok=True)
    plan = _run(
        _python(
            "scripts/prod/sync_course_repositories.py",
            "--database",
            database,
            "--checkout-plan",
            "--from-disk",
            checkout_root,
        ),
        capture_output=True,
    )
    _run(
        _python("scripts/checkout_refresh.py", "--host", _git_host()),
        input_text=plan.stdout,
    )


def pull_course_content(database: str, checkout_root: str, extra: list[str]) -> None:
    _run(
        _python(
            "scripts/prod/sync_course_repositories.py",
            "--database",
            database,
            "--from-disk",
            checkout_root,
            *extra,
        )
    )


def refresh_editorial_checkout(database: str, checkout: str) -> None:
    checkout_path = Path(checkout).resolve()
    checkout_path.parent.mkdir(parents=True, exist_ok=True)
    plan = _run(
        _python(
            "scripts/prod/sync_content_verify.py",
            "--database",
            database,
            "--checkout",
            str(checkout_path),
            "--checkout-plan",
        ),
        capture_output=True,
    )
    _run(
        _python("scripts/checkout_refresh.py", "--host", _git_host()),
        input_text=plan.stdout,
    )


def check_editorial_drift(database: str, checkout: str, revision: str | None) -> int:
    checkout_path = Path(checkout).resolve()
    command = list(
        _python(
            "scripts/prod/sync_content_verify.py",
            "--database",
            database,
            "--checkout",
            str(checkout_path),
        )
    )
    if revision:
        command.extend(("--revision", revision))
    return subprocess.run(tuple(command), cwd=PROJECT_ROOT, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sources = commands.add_parser("sources")
    sources.add_argument("--database")

    plan = commands.add_parser("pull-plan")
    plan.add_argument("--database")
    plan.add_argument("--checkout-root")

    checkouts = commands.add_parser("checkouts")
    checkouts.add_argument("--database")
    checkouts.add_argument("--checkout-root")

    pull = commands.add_parser("pull")
    pull.add_argument("--database")
    pull.add_argument("--checkout-root")
    pull.add_argument("extra", nargs=argparse.REMAINDER)

    editorial_checkout = commands.add_parser("checkout")
    editorial_checkout.add_argument("--database")
    editorial_checkout.add_argument("--checkout")

    drift = commands.add_parser("drift")
    drift.add_argument("--database")
    drift.add_argument("--checkout")
    drift.add_argument("--revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = _database(args.database)
    try:
        if args.command == "sources":
            register_sources(database)
        elif args.command == "pull-plan":
            show_pull_plan(database, _checkout_root(args.checkout_root))
        elif args.command == "checkouts":
            refresh_course_checkouts(database, _checkout_root(args.checkout_root))
        elif args.command == "pull":
            pull_course_content(database, _checkout_root(args.checkout_root), args.extra)
        elif args.command == "checkout":
            refresh_editorial_checkout(database, args.checkout or ".tmp/content-checkout")
        elif args.command == "drift":
            return check_editorial_drift(
                database,
                args.checkout or ".tmp/content-checkout",
                args.revision,
            )
    except (OSError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            return error.returncode or 1
        print(f"content command failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
