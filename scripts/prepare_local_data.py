#!/usr/bin/env python3
"""Run the bounded, repeatable local production-data rehearsal.

This command composes the existing local-only preparation seams.  It writes only to an
explicit SQLite database below ``.tmp/`` and never connects to a deployed database.

It is the single owner of every step of the bootstrap order in
``_docs/runbooks/data-ingest.md`` §11, in the order :data:`STEP_ORDER` declares:
``migrate``, course-source registration, the legacy Zoomcamp import (when
``--legacy-zoomcamp-source`` is given), the course-repository pull, the CMP content
reconcile (``COURSE_CATALOGUE_ORDER``), the reviewed one-time imports, the editorial
stage (``seed_content_sources`` plus one ``sync_content`` per declared editorial
source, when ``--editorial-checkout-root`` is given), and last the event stage --
``scripts/prod/import_events.py``'s own ``run()`` followed by the attendee exports.

It is offline by default: it reads the checkouts it is given.  ``--refresh-checkouts``
opts in to refreshing the disposable course and editorial checkouts through
``scripts/content.py checkouts`` / ``editorial-checkouts`` (and so through
``scripts/checkout_refresh.py``) once the rows they are planned from exist.
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
EVENTBRITE_RELATIVE_SOURCE = Path(".local/migration-data/events/eventbrite/aggregate-v1.zip")
LUMA_IDENTITIES_PATH = Path.home() / "prod/dtc-data/luma-event-identities.json"
EVENTBRITE_IDENTITIES_PATH = Path.home() / "prod/dtc-data/eventbrite-event-identities.json"
GALLERY_ENRICHMENT_PATH = (
    Path.home() / "prod/dtc-data/content-staging/project_gallery_repo_enrichment.jsonl"
)
GALLERY_STRUCTURED_PATH = (
    Path.home() / "prod/dtc-data/content-staging/project_gallery_structured.jsonl"
)
TOUR_TESTIMONIALS_PATH = Path.home() / "prod/dtc-data/content-staging/tour_testimonials.json"
#: Where the legacy importer looks for course-repository checkouts to enrich homework
#: write-ups from -- the standalone script's own default.
LEGACY_COURSE_REPOS_DIR = Path.home() / "git"
UV = ("uv", "run", "--frozen", "python")

#: The rehearsal's steps, in the order ``run()`` performs them.  The runbook's step
#: table (``_docs/runbooks/data-ingest.md`` §11) shows this sequence verbatim, and
#: ``scripts/tests/test_production_data.py`` holds both to it.
STEP_ORDER = (
    "migrate",
    "course_catalog",
    "course_repository_sources",
    "legacy_zoomcamp",
    "course_repository_checkouts",
    "course_modules",
    "cmp_content",
    "one_time_imports",
    "editorial_seed",
    "editorial_checkouts",
    "editorial_sync",
    "events",
)


class LocalPreparationError(RuntimeError):
    """A safe, bounded refusal to run the local rehearsal."""


@contextmanager
def _event_import_refusals() -> Iterator[None]:
    """Re-raise the event importer's condition codes as this command's own."""

    try:
        yield
    except EventImportError as error:
        raise LocalPreparationError(str(error)) from error


from scripts.prod.target import main_checkout_root  # noqa: E402


def _main_checkout_root() -> Path:
    return main_checkout_root(PROJECT_ROOT, error_cls=LocalPreparationError)


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


def _import_sponsor_and_testimonial_content() -> dict[str, Any]:
    """The narrowed step 4 of the documented bootstrap order.

    ``_docs/runbooks/data-ingest.md`` §11 step 4 used to name five reviewed one-time
    inputs under ``~/prod/dtc-data/content-staging/`` (outside this repository -- see
    ``_docs/architecture/database-only-content.md``): the editorial catalogue
    (``import_public_content.py``, ``import_faq.py``, ``import_docs.py``) plus sponsors
    and testimonials.  The three editorial-catalogue importers are gone -- articles,
    podcasts, books, people, wiki, docs and FAQ all read
    ``content.models.SyncedDocument`` rows now, written by the live
    ``community_base.content_sync`` engine (``manage.py sync_content`` against a real
    checkout, or its webhook).  The rehearsal runs that engine itself in its
    editorial stage (``_run_editorial_stage``) when it is given
    ``--editorial-checkout-root``, and reports the stage as skipped otherwise.

    Sponsors and testimonials are unaffected: they still write ``core.Sponsor`` and
    ``courses.Testimonial`` from their own reviewed one-time inputs, key each row on
    its natural key, and report ``replayed`` on a rerun -- these are the production
    importers themselves, not a second copy of what they do.  The tour page's two
    legs ride here too: its member stories are ``placement: "tour"`` testimonial
    rows from their own staging file, and its editorial copy is one
    ``ContentDocument`` imported by ``import_tour.py`` (§23).
    """

    from scripts.prod.import_sponsors import SponsorDirectoryImportFailure
    from scripts.prod.import_sponsors import run as import_sponsors
    from scripts.prod.import_testimonials import TestimonialImportFailure
    from scripts.prod.import_testimonials import run as import_testimonials
    from scripts.prod.import_tour import run as import_tour

    steps: tuple[tuple[str, Any, type[Exception]], ...] = (
        ("sponsors", import_sponsors, SponsorDirectoryImportFailure),
        ("testimonials", import_testimonials, TestimonialImportFailure),
        (
            "tour_testimonials",
            lambda: import_testimonials(path=TOUR_TESTIMONIALS_PATH),
            TestimonialImportFailure,
        ),
        ("tour_page", import_tour, ValueError),
    )
    reports: dict[str, Any] = {}
    for name, importer, failure in steps:
        try:
            reports[name] = importer()
        except failure as error:
            raise LocalPreparationError(f"{name}_{error}") from error
    return reports


def _refresh_checkouts(command: str, *, database: Path, checkout_root: Path) -> None:
    """Refresh disposable checkouts through ``scripts/content.py`` (opt-in, networked)."""

    try:
        subprocess.run(
            (
                *UV,
                "scripts/content.py",
                command,
                "--database",
                str(database),
                "--checkout-root",
                str(checkout_root),
            ),
            cwd=PROJECT_ROOT,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise LocalPreparationError(f"{command.replace('-', '_')}_failed") from error


def _import_legacy_zoomcamp(source: Path | None) -> dict[str, Any]:
    """``COURSE_CATALOGUE_ORDER``'s first leg: the frozen pre-2024 editions."""

    if source is None:
        return {"imported": False, "skipped": "source_not_supplied"}
    if not source.is_dir():
        raise LocalPreparationError("legacy_zoomcamp_source_unavailable")
    from scripts.prod.import_legacy_zoomcamp import LegacyZoomcampImportError
    from scripts.prod.import_legacy_zoomcamp import run as import_legacy_zoomcamp

    try:
        report = import_legacy_zoomcamp(
            source_repo=source, course_repos_dir=LEGACY_COURSE_REPOS_DIR
        )
    except LegacyZoomcampImportError as error:
        raise LocalPreparationError(f"legacy_zoomcamp_{error}") from error
    return {"imported": True, **report}


def _seed_editorial_sources() -> list[str]:
    """``manage.py seed_content_sources``: one engine row per declared source."""

    from django.core.management import call_command

    output = io.StringIO()
    try:
        call_command("seed_content_sources", stdout=output, stderr=output)
    except Exception as error:
        raise LocalPreparationError("seed_content_sources_failed") from error
    return [line.strip() for line in output.getvalue().splitlines() if line.strip()]


def _sync_editorial_source(slug: str, checkout: Path) -> dict[str, Any]:
    """``manage.py sync_content --source <slug> --from-disk <checkout>``.

    Anything short of a ``success`` sync log fails the rehearsal, naming the slug:
    a ``partial`` sync leaves some kinds stale, and a ``skipped`` one wrote nothing.
    """

    from community_base.content_sync.models import SyncLog, SyncStatus
    from django.core.management import call_command

    if not checkout.is_dir():
        raise LocalPreparationError(f"editorial_checkout_unavailable_{slug}")
    output = io.StringIO()
    try:
        call_command(
            "sync_content",
            source_slug=slug,
            from_disk=str(checkout),
            stdout=output,
            stderr=output,
        )
    except Exception as error:
        raise LocalPreparationError(f"editorial_sync_failed_{slug}") from error
    log = SyncLog.objects.filter(source__slug=slug).order_by("-started_at").first()
    if log is None or log.status != SyncStatus.SUCCESS:
        raise LocalPreparationError(f"editorial_sync_failed_{slug}")
    return {
        "status": log.status,
        "commit": log.commit_sha,
        "created": log.items_created,
        "updated": log.items_updated,
        "unchanged": log.items_unchanged,
        "deleted": log.items_deleted,
    }


def _run_editorial_stage(
    *, database: Path, checkout_root: Path | None, refresh_checkouts: bool
) -> dict[str, Any]:
    """Seed the declared editorial sources and sync each from its checkout.

    The live ``community_base.content_sync`` engine is the only way articles,
    podcasts, books, people, media, wiki, docs and FAQ arrive, so a rebuild runs it.
    Without a checkout root the stage is skipped explicitly -- and
    ``verify_local_dataset.py``'s editorial gate then fails, because such a dataset
    is incomplete.
    """

    if checkout_root is None:
        return {"skipped": "checkout_root_not_supplied"}
    from scripts.content import EditorialPlanError, declared_editorial_slugs

    seeded = _seed_editorial_sources()
    if refresh_checkouts:
        checkout_root.mkdir(parents=True, exist_ok=True)
        _refresh_checkouts("editorial-checkouts", database=database, checkout_root=checkout_root)
    try:
        slugs = declared_editorial_slugs()
    except EditorialPlanError as error:
        raise LocalPreparationError(str(error)) from error
    sources = {slug: _sync_editorial_source(slug, checkout_root / slug) for slug in slugs}
    return {"seeded": seeded, "sources": sources}


def run(
    *,
    database: Path,
    course_checkout_root: Path,
    identity_manifest: Path,
    luma_source: Path,
    eventbrite_source: Path,
    luma_identities: Path | None = LUMA_IDENTITIES_PATH,
    eventbrite_identities: Path = EVENTBRITE_IDENTITIES_PATH,
    gallery_enrichment_source: Path = GALLERY_ENRICHMENT_PATH,
    gallery_structured_source: Path = GALLERY_STRUCTURED_PATH,
    cmp_source_db: Path | None = None,
    legacy_zoomcamp_source: Path | None = None,
    editorial_checkout_root: Path | None = None,
    refresh_checkouts: bool = False,
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
    # COURSE_CATALOGUE_ORDER: the frozen pre-2024 editions first, then the
    # repositories, then CMP.  The legacy import bootstraps from an empty catalogue.
    legacy_zoomcamp = _import_legacy_zoomcamp(legacy_zoomcamp_source)
    if refresh_checkouts:
        # Planned from the source rows registered just above.
        _refresh_checkouts("checkouts", database=database, checkout_root=course_checkout_root)
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
        # The family/homework slug overrides come from that same production entry
        # point: without them this rehearsal reconciles CMP's raw "ai-dev-tools"
        # edition slug as its own family instead of folding it into
        # "ai-dev-tools-zoomcamp", splitting one course across two families.
        from courses.services.cmp_content_import import (
            CmpContentImportError,
            import_cmp_course_content,
        )
        from scripts.prod.import_cmp_content import (
            FAMILY_SLUG_OVERRIDES,
            HOMEWORK_SLUG_OVERRIDES,
        )

        try:
            cmp_content = import_cmp_course_content(
                cmp_source_db,
                family_slug_overrides=FAMILY_SLUG_OVERRIDES,
                homework_slug_overrides=HOMEWORK_SLUG_OVERRIDES,
            ).summary()
        except CmpContentImportError as error:
            raise LocalPreparationError(f"cmp_content_{error}") from error
    # Step 4, after the catalogue and before the event stage: sponsors and
    # testimonials, from the production importers rather than a seeder. The
    # editorial catalogue itself (articles/podcasts/books/people/wiki/docs/FAQ)
    # arrives through the editorial stage below.
    editorial_content = _import_sponsor_and_testimonial_content()
    # Step 4 alongside the other reviewed one-time staging inputs: the
    # project-gallery repository enrichment (issue #416), with its structured
    # course records attached.  Order-free -- it bootstraps its own table and
    # touches no row another step writes -- and the same failure shape.
    from scripts.prod.import_project_repo_enrichment import (  # noqa: E402
        ProjectRepoEnrichmentImportFailure,
    )
    from scripts.prod.import_project_repo_enrichment import (  # noqa: E402
        run as import_project_repo_enrichment,
    )

    try:
        project_repo_enrichment = import_project_repo_enrichment(
            enrichment_source=gallery_enrichment_source,
            structured_source=gallery_structured_source,
        )
    except ProjectRepoEnrichmentImportFailure as error:
        raise LocalPreparationError(f"project_repo_enrichment_{error}") from error
    # Step 4, last: the live editorial sync, from one checkout per declared source.
    editorial = _run_editorial_stage(
        database=database,
        checkout_root=editorial_checkout_root,
        refresh_checkouts=refresh_checkouts,
    )
    # Step 5: import current Event identities and content, then populate current
    # attendee rows from both provider exports.
    with _event_import_refusals():
        event_pipeline = run_event_pipeline(
            identity_manifest=identity_manifest,
            luma_source=luma_source,
        )
    try:
        from scripts.prod.registrant_import import RegistrantImportError, import_registrants
        from scripts.prod.registration_sources.eventbrite_registrants import (
            PROVIDER as EVENTBRITE_PROVIDER,
        )
        from scripts.prod.registration_sources.eventbrite_registrants import (
            eventbrite_registrant_sources,
        )
        from scripts.prod.registration_sources.luma_registrants import (
            PROVIDER as LUMA_PROVIDER,
        )
        from scripts.prod.registration_sources.luma_registrants import (
            luma_registrant_sources,
        )

        registrations = {
            "luma": import_registrants(
                provider=LUMA_PROVIDER,
                pending=luma_registrant_sources(
                    luma_source,
                    identities_path=luma_identities,
                ),
                refresh=True,
            ).as_dict(),
            "eventbrite": import_registrants(
                provider=EVENTBRITE_PROVIDER,
                pending=eventbrite_registrant_sources(
                    archive_path=eventbrite_source,
                    identities_path=eventbrite_identities,
                ),
                refresh=True,
            ).as_dict(),
        }
    except RegistrantImportError as error:
        raise LocalPreparationError(f"event_registrants_{error}") from error
    return _orchestrator_report(
        fresh=fresh,
        migrations=migrations,
        catalog=catalog,
        course_sources=course_sources,
        legacy_zoomcamp=legacy_zoomcamp,
        modules=modules,
        cmp_content=cmp_content,
        editorial_content=editorial_content,
        editorial=editorial,
        event_pipeline=event_pipeline,
        registrations=registrations,
        project_repo_enrichment=project_repo_enrichment,
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
    legacy_zoomcamp: dict[str, Any] | None = None,
    editorial: dict[str, Any] | None = None,
    registrations: dict[str, Any],
    project_repo_enrichment: dict[str, Any],
) -> dict[str, Any]:
    """The rehearsal report: course/editorial under ``steps``, the event
    stage's own structured result at top level.

    The event keys are carried over from ``import_events.run()`` and the
    provider registration imports and the gallery enrichment import are
    reported separately.
    """

    return {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "database": {"environment": "local", "sqlite": True, "fresh_requested": fresh},
        "steps": {
            "migrations": {"completed": True, "report": migrations},
            "course_catalog": catalog,
            "course_repository_sources": course_sources,
            "legacy_zoomcamp": legacy_zoomcamp
            or {"imported": False, "skipped": "source_not_supplied"},
            "course_modules": modules,
            "cmp_content": cmp_content,
            "editorial_content": editorial_content,
            "editorial": editorial or {"skipped": "checkout_root_not_supplied"},
        },
        "event_identities": event_pipeline["identities"],
        "new_event_identities": event_pipeline["new_event_identities"],
        "event_content": event_pipeline["event_content"],
        "new_event_content": event_pipeline["new_event_content"],
        "eventbrite_descriptions": event_pipeline["eventbrite_descriptions"],
        "event_registrations": registrations,
        "project_repo_enrichment": project_repo_enrichment,
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
            "named after the source stable id. `scripts/content.py checkouts` produces it."
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
        "--luma-identities",
        type=Path,
        default=LUMA_IDENTITIES_PATH,
    )
    parser.add_argument(
        "--eventbrite-identities",
        type=Path,
        default=EVENTBRITE_IDENTITIES_PATH,
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Refuse to run if the selected SQLite database or its WAL files already exist.",
    )
    parser.add_argument(
        "--legacy-zoomcamp-source",
        type=Path,
        default=None,
        help=(
            "Local clone of DataTalksClub/zoomcamp-scoring. When given, the pre-2024 "
            "editions are imported after course-source registration and before the "
            "course-repository pull (COURSE_CATALOGUE_ORDER)."
        ),
    )
    parser.add_argument(
        "--editorial-checkout-root",
        type=Path,
        default=None,
        help=(
            "Directory holding one checkout per declared editorial source, named after "
            "its slug (`scripts/content.py editorial-checkouts` produces it). When "
            "given, seed_content_sources and one sync_content per source run; without "
            "it the editorial stage is reported as skipped."
        ),
    )
    parser.add_argument(
        "--refresh-checkouts",
        action="store_true",
        help=(
            "Refresh the disposable course and editorial checkouts (network) once the "
            "rows they are planned from exist. Off by default: the rehearsal is offline."
        ),
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
            luma_identities=(
                Path(args.luma_identities).expanduser().resolve()
                if args.luma_identities is not None
                else None
            ),
            eventbrite_identities=Path(args.eventbrite_identities).expanduser().resolve(),
            cmp_source_db=(
                Path(args.cmp_source_db).resolve() if args.cmp_source_db is not None else None
            ),
            legacy_zoomcamp_source=(
                Path(args.legacy_zoomcamp_source).expanduser().resolve()
                if args.legacy_zoomcamp_source is not None
                else None
            ),
            editorial_checkout_root=(
                Path(args.editorial_checkout_root).resolve()
                if args.editorial_checkout_root is not None
                else None
            ),
            refresh_checkouts=args.refresh_checkouts,
            fresh=args.fresh,
        )
    except LocalPreparationError as error:
        print(f"local preparation refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
