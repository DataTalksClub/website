#!/usr/bin/env python
# ruff: noqa: E402
"""Import pre-2024 Zoomcamp scoring/certificate history into a database.

One-time import.  ``zoomcamp-scoring`` is frozen history: it is read once at
migration, not re-synchronized.  See ``scripts/prod/__init__.py`` for what the
two sync models mean and which script is which.

The course management platform only carries data from 2024 onward.  Everything
before that lives in the commit history of the separate
``DataTalksClub/zoomcamp-scoring`` repository.  This script reads a local
checkout of that repository -- it is never vendored into this one -- and writes
cohorts, homeworks, projects, submissions, enrollments and certificates.

Unlike every other importer here, this one works against an empty database:
``Cohort.save()`` resolves the reviewed course family from the slug, so no
catalogue has to exist first.

Every learner's real email is recovered (from the weekly raw exports, graduate
lists, and any leaderboard-email reveal in that repository) and used to pick
their account, so a historical cohort attaches to the same account a returning
learner already has or later creates.  Every *displayed* identity (leaderboard
name, certificate name) is instead a freshly generated placeholder -- the
platform's own anonymous-leaderboard-name generator, the same one every current
enrollment already gets by default.  See ``legacy_zoomcamp/identity.py`` for
exactly what is and is not persisted, and ``email_recovery.py`` for where the
email comes from.

Only the graded ("processed") exports are read for scores -- not the free-text
GitHub links/feedback in ``raw/``.  See ``legacy_zoomcamp/editions.py`` for the
full source-file mapping.

When a local checkout of the matching course repo exists (e.g.
``~/git/data-engineering-zoomcamp``, cloned alongside this one), real module
titles and homework write-ups are pulled from its ``cohorts/<year>/`` content
instead of a generic placeholder -- see ``legacy_zoomcamp/homework_content.py``.
Pass ``--course-repos-dir`` to point elsewhere, or ``--no-homework-content`` to
skip it entirely.

Re-running is safe: every write is keyed on a natural key, so a replay reports
the same counts and creates no duplicate row.

    git clone https://github.com/DataTalksClub/zoomcamp-scoring ~/git/zoomcamp-scoring

    uv run --frozen python scripts/prod/import_legacy_zoomcamp.py \\
        --database .tmp/local.sqlite3 \\
        --source-repo ~/git/zoomcamp-scoring --list

    uv run --frozen python scripts/prod/import_legacy_zoomcamp.py \\
        --database .tmp/local.sqlite3 \\
        --source-repo ~/git/zoomcamp-scoring --edition de-zoomcamp-2022
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYNC_MODEL = "one-time"
# This is the only importer here that bootstraps an empty database: a cohort's
# course family is resolved from its slug by ``Cohort.save()``, so nothing has
# to exist first.  Every other importer reconciles against rows that are
# already there.  ``scripts/prod/__init__.py`` states and checks that ordering.
BOOTSTRAPS_EMPTY_DATABASE = True


class LegacyZoomcampImportError(RuntimeError):
    """A safe refusal that names a condition, never a source value."""


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def discover(source_repo: Path) -> list:
    """Return the editions the checkout carries, newest source layout first."""

    from scripts.prod.legacy_zoomcamp.editions import build_editions

    if not source_repo.is_dir():
        raise LegacyZoomcampImportError("source_repo_unavailable")
    return build_editions(source_repo)


def edition_inventory(editions) -> list[dict[str, Any]]:
    return [
        {
            "cohort": edition.cohort_slug,
            "year": edition.year,
            "homeworks": len(edition.homeworks),
            "projects": len(edition.projects),
            "certificates": bool(edition.certificate_csvs),
        }
        for edition in editions
    ]


def select(editions, wanted: list[str] | None):
    if not wanted:
        return list(editions)
    by_slug = {edition.cohort_slug: edition for edition in editions}
    missing = sorted(slug for slug in wanted if slug not in by_slug)
    if missing:
        raise LegacyZoomcampImportError(f"unknown_edition:{','.join(missing)}")
    return [by_slug[slug] for slug in wanted]


def _recalculate(cohort, homeworks, projects) -> None:
    from django.core.cache import cache

    from courses.assignment_statistics import (
        calculate_homework_statistics,
        calculate_project_statistics,
    )
    from courses.leaderboard import update_leaderboard

    for homework in homeworks:
        calculate_homework_statistics(homework, force=True)
    for project in projects:
        calculate_project_statistics(project, force=True)
    update_leaderboard(cohort)
    for key in ("leaderboard", "leaderboard_data", "leaderboard_yaml"):
        cache.delete(f"{key}:{cohort.id}")


def import_edition(
    edition,
    *,
    skip_scoring: bool,
    skip_certificates: bool,
    course_repos_dir: Path | None,
) -> dict[str, Any]:
    """Import one edition and report its counts."""

    from scripts.prod.legacy_zoomcamp.certificate_import import import_edition_certificates
    from scripts.prod.legacy_zoomcamp.scoring_import import import_edition_scoring

    report: dict[str, Any] = {"cohort": edition.cohort_slug}
    cohort = None
    if skip_scoring:
        report["scoring"] = {"skipped": True}
    else:
        result = import_edition_scoring(edition, course_repos_dir=course_repos_dir)
        cohort = result.cohort
        _recalculate(cohort, result.homeworks, result.projects)
        report["scoring"] = {
            "homeworks": len(result.homeworks),
            "projects": len(result.projects),
            "homework_submissions": result.homework_submissions,
            "project_submissions": result.project_submissions,
        }

    if skip_certificates:
        report["certificates"] = {"skipped": True}
        return report
    if cohort is None:
        from courses.models import Cohort

        try:
            cohort = Cohort.objects.get(slug=edition.cohort_slug)
        except Cohort.DoesNotExist:
            raise LegacyZoomcampImportError("cohort_absent_for_certificates") from None
    if not edition.certificate_csvs:
        report["certificates"] = {"source": False}
        return report
    certificates = import_edition_certificates(cohort, edition)
    report["certificates"] = {
        "source": True,
        "graduates": certificates.graduates_seen,
        "urls_matched": certificates.certificate_urls_matched,
    }
    return report


def run(
    *,
    source_repo: Path,
    editions: list[str] | None = None,
    skip_scoring: bool = False,
    skip_certificates: bool = False,
    course_repos_dir: Path | None = None,
) -> dict[str, Any]:
    discovered = discover(source_repo)
    selected = select(discovered, editions)
    per_edition = [
        import_edition(
            edition,
            skip_scoring=skip_scoring,
            skip_certificates=skip_certificates,
            course_repos_dir=course_repos_dir,
        )
        for edition in selected
    ]
    return {
        "source": "zoomcamp-scoring",
        "editions_available": len(discovered),
        "editions_imported": len(per_edition),
        "homework_submissions": sum(
            item["scoring"].get("homework_submissions", 0) for item in per_edition
        ),
        "project_submissions": sum(
            item["scoring"].get("project_submissions", 0) for item in per_edition
        ),
        "graduates": sum(item["certificates"].get("graduates", 0) for item in per_edition),
        "per_edition": per_edition,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--source-repo",
        required=True,
        type=Path,
        help="Local clone of DataTalksClub/zoomcamp-scoring.",
    )
    parser.add_argument(
        "--edition",
        action="append",
        default=None,
        help="Limit to one cohort slug, e.g. de-zoomcamp-2022. Repeatable.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Report the discovered editions and exit without writing.",
    )
    parser.add_argument("--skip-certificates", action="store_true")
    parser.add_argument("--skip-scoring", action="store_true")
    parser.add_argument(
        "--course-repos-dir",
        type=Path,
        default=Path.home() / "git",
        help=(
            "Directory holding course-repository checkouts (e.g. "
            "data-engineering-zoomcamp), read for real homework titles and "
            "write-ups. Default: ~/git"
        ),
    )
    parser.add_argument(
        "--no-homework-content",
        action="store_true",
        help="Skip homework title/content enrichment even when the checkouts exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.resolve())

    try:
        if args.list:
            report: dict[str, Any] = {
                "source": "zoomcamp-scoring",
                "editions": edition_inventory(discover(args.source_repo)),
            }
        else:
            report = run(
                source_repo=args.source_repo,
                editions=args.edition,
                skip_scoring=args.skip_scoring,
                skip_certificates=args.skip_certificates,
                course_repos_dir=None if args.no_homework_content else args.course_repos_dir,
            )
    except LegacyZoomcampImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
