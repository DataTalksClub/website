#!/usr/bin/env python3
"""Run the bounded, repeatable local production-data rehearsal.

This command composes the existing local-only preparation seams.  It writes only to an
explicit SQLite database below ``.tmp/`` and never connects to a deployed database.

The public event pipeline is `scripts/prod/import_events.py`'s own `run()` -- all
five legs (reviewed identities, reviewed content, new-event discovery, staged
descriptions for discovered events, registration-aggregate derivation and
staging plus the automatic mapping resolution) in their fixed order under its
one transaction. The course catalog is imported first and the reviewed editorial
inputs after it, which is the bootstrap order in
`_docs/runbooks/data-ingest.md` §11; the event stage is §11 step 5 and runs
last, through the same function the production entry point calls. The protected
Eventbrite and Luma exports are parsed and reconciled against their recorded safe
facts. Legacy candidates remain review-required; an optional explicit
current-event mapping input can stage and activate only those exact provider
identities so a fresh database can render their aggregate count without a
title/date guess.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The event pipeline lives in scripts/prod/import_events.py, which is also its
# own entry point. This orchestrator composes that module's run() -- all five
# legs, in their fixed order, inside its one transaction -- rather than keeping
# a second, shorter copy of the same parsing, staging and activation code.
from scripts.prod.import_events import (  # noqa: E402
    EVENTBRITE_RELATIVE_SOURCE,
    IDENTITY_MANIFEST_PATH,
    LUMA_RELATIVE_SOURCE,
    EventImportError,
)
from scripts.prod.import_events import (  # noqa: E402
    run as run_event_pipeline,
)
from scripts.prod.sync_course_repositories import (  # noqa: E402
    SyncCourseRepositoriesError,
)
from scripts.prod.sync_course_repositories import (  # noqa: E402
    pull as pull_course_repositories,
)
from scripts.prod.sync_course_repositories import (  # noqa: E402
    select_sources as select_course_repository_sources,
)
from scripts.prod.sync_course_repository_sources import (  # noqa: E402
    SyncCourseRepositorySourcesError,
)
from scripts.prod.sync_course_repository_sources import (  # noqa: E402
    sync as sync_course_repository_sources,
)

ORCHESTRATOR_SCHEMA_VERSION = 2


class LocalPreparationError(RuntimeError):
    """A safe, bounded refusal to run the local rehearsal."""


@contextmanager
def _event_import_refusals() -> Iterator[None]:
    """Re-raise the event importer's condition codes as this command's own."""

    try:
        yield
    except EventImportError as error:
        raise LocalPreparationError(str(error)) from error


def _main_checkout_root() -> Path:
    try:
        common_dir = subprocess.run(
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
            check=True,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise LocalPreparationError("git_common_directory_unavailable") from error
    return Path(common_dir).resolve().parent


def _local_database_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve(strict=False)
    try:
        path.relative_to((PROJECT_ROOT / ".tmp").resolve())
    except ValueError as error:
        raise LocalPreparationError("database_must_be_under_tmp") from error
    if path.suffix != ".sqlite3":
        raise LocalPreparationError("database_must_be_sqlite")
    return path


def _configure_local_environment(database: Path) -> None:
    configured_environment = os.getenv("DTC_ENVIRONMENT")
    if configured_environment not in (None, "local"):
        raise LocalPreparationError("local_environment_required")
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ["DJANGO_SETTINGS_MODULE"] = "website.settings.local"


def _json_management_command(name: str, **options: Any) -> dict[str, Any]:
    from django.core.management import call_command

    output = io.StringIO()
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        call_command(name, stdout=output, stderr=output, verbosity=0, **options)
    except Exception as error:
        # Management-command diagnostics may contain source paths.  Keep the command's
        # public CLI bounded and let the caller rerun the individual command for debugging.
        raise LocalPreparationError(f"{name}_failed") from error
    finally:
        logging.disable(previous_logging_disable)
    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        report = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise LocalPreparationError(f"{name}_report_invalid") from error
    if not isinstance(report, dict):
        raise LocalPreparationError(f"{name}_report_invalid")
    return report


def _import_editorial_content() -> dict[str, Any]:
    """Step 4 of the documented bootstrap order, as one block.

    ``_docs/runbooks/data-ingest.md`` §11 step 4 names five reviewed one-time inputs
    under ``temporary/content/``.  All five declare ``BOOTSTRAPS_EMPTY_DATABASE`` and
    none depends on another, so the order within the block carries no meaning -- what
    matters is that the block runs, and that it runs after the course catalogue rather
    than instead of it.  Only testimonials used to run here, so a rehearsal database
    had no articles, podcasts, books, people, wiki, docs, FAQ or sponsors and nothing
    said so.

    Every one is replay-safe: the three catalogue importers write and activate a fresh
    release, and the sponsor and testimonial importers key each row on its natural key
    and report ``replayed``.  These are the production importers themselves, not a
    second copy of what they do.
    """

    from scripts.prod.import_docs import DocsImportFailure
    from scripts.prod.import_docs import run as import_docs
    from scripts.prod.import_faq import FaqImportFailure
    from scripts.prod.import_faq import run as import_faq
    from scripts.prod.import_public_content import PublicContentImportFailure
    from scripts.prod.import_public_content import run as import_public_content
    from scripts.prod.import_sponsors import SponsorDirectoryImportFailure
    from scripts.prod.import_sponsors import run as import_sponsors
    from scripts.prod.import_testimonials import TestimonialImportFailure
    from scripts.prod.import_testimonials import run as import_testimonials

    steps: tuple[tuple[str, Any, type[RuntimeError]], ...] = (
        ("public_content", import_public_content, PublicContentImportFailure),
        ("faq", import_faq, FaqImportFailure),
        ("docs", import_docs, DocsImportFailure),
        ("sponsors", import_sponsors, SponsorDirectoryImportFailure),
        ("testimonials", import_testimonials, TestimonialImportFailure),
    )
    reports: dict[str, Any] = {}
    for name, importer, failure in steps:
        try:
            reports[name] = importer()
        except failure as error:
            raise LocalPreparationError(f"{name}_{error}") from error
    return reports


def run(
    *,
    database: Path,
    course_checkout_root: Path,
    identity_manifest: Path,
    luma_source: Path,
    eventbrite_source: Path,
    current_registration_input: Path | None = None,
    cmp_source_db: Path | None = None,
    fresh: bool,
) -> dict[str, Any]:
    if fresh and any(
        path.exists()
        for path in (
            database,
            database.with_name(f"{database.name}-shm"),
            database.with_name(f"{database.name}-wal"),
        )
    ):
        raise LocalPreparationError("fresh_database_already_exists")
    if not identity_manifest.is_file():
        raise LocalPreparationError("preparation_manifest_unavailable")
    if not course_checkout_root.is_dir():
        raise LocalPreparationError("course_checkout_root_unavailable")
    if not luma_source.is_dir() or not eventbrite_source.is_file():
        raise LocalPreparationError("registration_source_unavailable")

    database.parent.mkdir(parents=True, exist_ok=True)
    _configure_local_environment(database)

    import django

    django.setup()

    migrations = _json_management_command("migrate", interactive=False)
    catalog = _json_management_command("seed_local_courses")
    # Which repositories exist is registered data, so the rehearsal registers the
    # pinned sources and then runs the one ingestion the signed push webhook runs.
    # Same functions scripts/prod/sync_course_repository_sources.py and
    # scripts/prod/sync_course_repositories.py call: one implementation, not a
    # management command wrapping a second copy of it.
    try:
        course_sources = sync_course_repository_sources()
    except SyncCourseRepositorySourcesError as error:
        raise LocalPreparationError(f"sync_course_repository_sources_{error}") from error
    # Production order: the course repositories are pulled *first*, then CMP is
    # reconciled against what they wrote.  The reverse order happened to work only
    # while CMP had no cohort that a repository also owns; the first time both
    # describe one, the CMP-first rebuild refuses on a homework slug collision it
    # would not hit this way round.  The repository is the upstream, so it goes first.
    try:
        repository_sources = select_course_repository_sources(
            (), explicit={}, root=course_checkout_root
        )
        modules = pull_course_repositories(
            sources=repository_sources,
            root=course_checkout_root,
            require_public_commit=True,
        )
    except SyncCourseRepositoriesError as error:
        raise LocalPreparationError(f"sync_course_repositories_{error}") from error
    cmp_content: dict[str, Any]
    if cmp_source_db is None:
        cmp_content = {"imported": False, "skipped": "source_not_supplied"}
    else:
        # The *reconciling* CMP importer, the same one scripts/prod/import_cmp_content.py
        # runs.  The bulk-copy path it replaced could only ever write into an empty
        # catalogue, so it had to run before the repository pull and refused outright
        # afterwards; a reconciler is what "repositories first, then CMP" needs.
        from courses.services.cmp_content_import import (
            CmpContentImportError,
            import_cmp_course_content,
        )

        try:
            cmp_content = import_cmp_course_content(cmp_source_db).summary()
        except CmpContentImportError as error:
            raise LocalPreparationError(f"cmp_content_{error}") from error
    # Step 4, after the catalogue and before the event stage: real reviewed
    # content from the production importers rather than a seeder, so the rehearsal
    # database holds the same editorial catalogue a production database does.
    editorial_content = _import_editorial_content()
    # Step 5, and the rehearsal's one event stage: `import_events.run()` itself,
    # all five legs in their fixed order under its one transaction. The
    # rehearsal used to run only the identity and content legs before the
    # catalogue and the registration legs after this block, outside any
    # transaction -- so a new event the discovery leg would have created, or an
    # aggregate the automatic resolution would have mapped, silently never
    # appeared, and a refused registration leg left the earlier event writes
    # committed with their queued wakeups pointing at them.
    with _event_import_refusals():
        event_pipeline = run_event_pipeline(
            identity_manifest=identity_manifest,
            luma_source=luma_source,
            eventbrite_source=eventbrite_source,
            current_registration_input=current_registration_input,
            correlation_id="local-production-prep",
        )
    return _orchestrator_report(
        fresh=fresh,
        migrations=migrations,
        catalog=catalog,
        course_sources=course_sources,
        modules=modules,
        cmp_content=cmp_content,
        editorial_content=editorial_content,
        event_pipeline=event_pipeline,
    )


def _orchestrator_report(
    *,
    fresh: bool,
    migrations: dict[str, Any],
    catalog: dict[str, Any],
    course_sources: dict[str, Any],
    modules: dict[str, Any],
    cmp_content: dict[str, Any],
    editorial_content: dict[str, Any],
    event_pipeline: dict[str, Any],
) -> dict[str, Any]:
    """The rehearsal report: course/editorial under ``steps``, the event
    stage's own structured result at top level.

    The event keys are carried over key for key from ``import_events.run()``:
    the reviewed-manifest identities, the automatic Luma discovery, the
    reviewed content records, the staged descriptions for discovered events,
    the registration derivation/staging, the narrower automatic mapping
    resolution and the explicit-input activation coverage each stay under
    their own name. Merging any two would hide which of them moved.
    """

    return {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "database": {"environment": "local", "sqlite": True, "fresh_requested": fresh},
        "steps": {
            "migrations": {"completed": True, "report": migrations},
            "course_catalog": catalog,
            "course_repository_sources": course_sources,
            "course_modules": modules,
            "cmp_content": cmp_content,
            "editorial_content": editorial_content,
        },
        "event_identities": event_pipeline["identities"],
        "new_event_identities": event_pipeline["new_event_identities"],
        "event_content": event_pipeline["event_content"],
        "new_event_content": event_pipeline["new_event_content"],
        "registration_sources": event_pipeline["registration_sources"],
        "registration_import": event_pipeline["registration_import"],
        "aggregate_auto_resolution": event_pipeline["aggregate_auto_resolution"],
        "activation_coverage": event_pipeline["activation_coverage"],
    }


def _parser() -> argparse.ArgumentParser:
    main_root = _main_checkout_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="SQLite path below this checkout's .tmp/")
    parser.add_argument(
        "--course-checkout-root",
        required=True,
        type=Path,
        help=(
            "Directory holding one checkout per registered course-repository source, "
            "named after the source stable id. `make content-checkouts` produces it."
        ),
    )
    parser.add_argument(
        "--identity-manifest",
        type=Path,
        # The one place that owns where the reviewed manifest sits is the
        # importer this orchestrator composes. Re-deriving the path here is how
        # it came to point at a file that had moved away two commits earlier.
        default=IDENTITY_MANIFEST_PATH,
    )
    parser.add_argument(
        "--luma-source",
        type=Path,
        default=main_root / LUMA_RELATIVE_SOURCE,
    )
    parser.add_argument(
        "--eventbrite-source",
        type=Path,
        default=main_root / EVENTBRITE_RELATIVE_SOURCE,
    )
    parser.add_argument(
        "--current-registration-input",
        type=Path,
        default=None,
        help=(
            "JSON file containing exact current provider identities and canonical Event "
            "source identities; legacy candidates remain review-required."
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Refuse to run if the selected SQLite database or its WAL files already exist.",
    )
    parser.add_argument(
        "--cmp-source-db",
        type=Path,
        default=None,
        help=(
            "Protected CMP SQLite snapshot to import as sanitized course content. "
            "The file is copied and read only; learner tables are not imported."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        database = _local_database_path(args.database)
        report = run(
            database=database,
            course_checkout_root=Path(args.course_checkout_root).resolve(),
            identity_manifest=Path(args.identity_manifest).resolve(),
            luma_source=Path(args.luma_source).resolve(),
            eventbrite_source=Path(args.eventbrite_source).resolve(),
            current_registration_input=(
                Path(args.current_registration_input).resolve()
                if args.current_registration_input is not None
                else None
            ),
            cmp_source_db=(
                Path(args.cmp_source_db).resolve() if args.cmp_source_db is not None else None
            ),
            fresh=args.fresh,
        )
    except LocalPreparationError as error:
        print(f"local preparation refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
