#!/usr/bin/env python3
"""Run the database-owned content checkout and pull workflows.

The course-repository workflows (``sources``, ``pull-plan``, ``checkouts``,
``pull``) read the registered course ``ContentSource`` rows.  The editorial
workflows (``editorial-plan``, ``editorial-checkouts``) read the seeded
``community_base.content_sync`` sources -- ``manage.py seed_content_sources``
writes them -- and refresh one disposable checkout per source for
``manage.py sync_content --from-disk``.  Every checkout is refreshed through
``scripts/checkout_refresh.py``.

``editorial-plan`` is the one workflow that reads the database in this process:
it prints one tab-separated line per declared editorial source, in the shape
``scripts/checkout_refresh.py`` reads::

    <slug>\t<owner/repository>\tHEAD\t<checkout-root>/<slug>

``HEAD`` is the remote's default branch, the commit the engine's own GitHub sync
resolves.  A declared source with no enabled seeded row is a refusal, not a
shorter plan: a rebuild that silently skipped a source would publish without it.
Its ``--database`` must name a SQLite file below the project-local ``.tmp/``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
UV: Final = ("uv", "run", "--frozen", "python")
#: The branch field of every editorial plan line: the remote's default branch.
EDITORIAL_DEFAULT_BRANCH: Final = "HEAD"


class EditorialPlanError(RuntimeError):
    """A bounded refusal naming the unmet condition."""


def _python(script: str, *arguments: str) -> tuple[str, ...]:
    return (*UV, script, *arguments)


def _run(
    command: tuple[str, ...],
    *,
    environment: dict[str, str] | None = None,
    input_text: str | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        input=input_text,
        capture_output=capture_output,
        text=True,
    )


def _database(value: str | None) -> str:
    return value or os.environ.get("CONTENT_DATABASE", ".tmp/local.sqlite3")


def _checkout_root(value: str | None) -> str:
    return value or os.environ.get("CONTENT_CHECKOUT_ROOT", ".tmp/course-checkouts")


def _editorial_checkout_root(value: str | None) -> str:
    return value or os.environ.get("CONTENT_EDITORIAL_CHECKOUT_ROOT", ".tmp/editorial-checkouts")


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


def declared_editorial_slugs() -> tuple[str, ...]:
    """The declared editorial source slugs (``COMMUNITY_BASE["CONTENT_SOURCES"]``)."""

    from community_base.kernel.conf import get

    declarations = get("CONTENT_SOURCES")
    if not isinstance(declarations, list):
        raise EditorialPlanError("content_sources_not_declared")
    return tuple(str(declaration["slug"]) for declaration in declarations)


def editorial_checkout_plan(root: Path) -> list[tuple[str, str, str, Path]]:
    """One ``(slug, repository, branch, checkout)`` per seeded editorial source."""

    from community_base.content_sync.models import ContentSource

    slugs = declared_editorial_slugs()
    seeded = {
        source.slug: source
        for source in ContentSource.objects.filter(slug__in=slugs, is_enabled=True)
    }
    plan: list[tuple[str, str, str, Path]] = []
    for slug in slugs:
        source = seeded.get(slug)
        if source is None:
            raise EditorialPlanError(f"editorial_source_not_seeded_{slug}")
        plan.append((slug, source.repo_name, EDITORIAL_DEFAULT_BRANCH, root / slug))
    return plan


def _start_django_for(database: str) -> None:
    """Point Django at a ``.tmp/`` SQLite file through the shared target rules."""

    from scripts.prod.target import require_scratch_database, resolve_target

    selection = argparse.Namespace(
        database=Path(database), deployment_target=None, allow_production_write=None
    )
    require_scratch_database(selection)
    resolve_target(selection).apply(os.environ)

    import django

    django.setup()


def show_editorial_plan(database: str, checkout_root: str) -> None:
    from scripts.prod.target import TargetRefused

    try:
        _start_django_for(database)
        plan = editorial_checkout_plan(Path(checkout_root))
    except (TargetRefused, EditorialPlanError) as error:
        print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
        raise subprocess.CalledProcessError(2, ("editorial-plan",)) from error
    for slug, repository, branch, checkout in plan:
        print(f"{slug}\t{repository}\t{branch}\t{checkout}")


def refresh_editorial_checkouts(database: str, checkout_root: str) -> None:
    Path(checkout_root).mkdir(parents=True, exist_ok=True)
    # The plan runs in its own process so this one never starts Django.
    plan = _run(
        _python(
            "scripts/content.py",
            "editorial-plan",
            "--database",
            database,
            "--checkout-root",
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

    editorial_plan = commands.add_parser("editorial-plan")
    editorial_plan.add_argument("--database")
    editorial_plan.add_argument("--checkout-root")

    editorial_checkouts = commands.add_parser("editorial-checkouts")
    editorial_checkouts.add_argument("--database")
    editorial_checkouts.add_argument("--checkout-root")

    pull = commands.add_parser("pull")
    pull.add_argument("--database")
    pull.add_argument("--checkout-root")
    pull.add_argument("extra", nargs=argparse.REMAINDER)
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
        elif args.command == "editorial-plan":
            show_editorial_plan(database, _editorial_checkout_root(args.checkout_root))
        elif args.command == "editorial-checkouts":
            refresh_editorial_checkouts(database, _editorial_checkout_root(args.checkout_root))
        elif args.command == "pull":
            pull_course_content(database, _checkout_root(args.checkout_root), args.extra)
    except (OSError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            return error.returncode or 1
        print(f"content command failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
