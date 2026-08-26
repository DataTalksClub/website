#!/usr/bin/env python
# ruff: noqa: E402
"""Import pre-2024 Zoomcamp scoring/certificate history into the local database.

The course management platform only carries data from 2024 onward. Everything
before that lives in the commit history of the separate
``DataTalksClub/zoomcamp-scoring`` repository. This script reads a local
checkout of that repository -- it is never vendored into this one -- and
writes cohorts, homeworks, projects, submissions, and certificates locally.

Every learner's real email is recovered (from the weekly raw exports,
graduate lists, and any leaderboard-email reveal in that repository) and used
to pick their account, so a historical cohort attaches to the same account a
returning learner already has or later creates. Every *displayed* identity
(leaderboard name, certificate name) is instead a freshly generated
placeholder -- the platform's own anonymous-leaderboard-name generator, the
same one every current enrollment already gets by default. See
``scripts/historical_import/identity.py`` for exactly what is and is not
persisted, and ``email_recovery.py`` for where the email comes from.

Only the graded ("processed") exports are read for scores -- not the free-text
GitHub links/feedback in ``raw/``. See ``scripts/historical_import/editions.py``
for the full source-file mapping.

When a local checkout of the matching course repo exists (e.g.
``~/git/data-engineering-zoomcamp``, cloned alongside this one), real module
titles and homework write-ups are pulled from its ``cohorts/<year>/`` content
instead of a generic placeholder -- see ``homework_content.py``. Pass
``--course-repos-dir`` to point elsewhere, or ``--no-homework-content`` to skip
it entirely.

Run from the repository root, after cloning zoomcamp-scoring somewhere
outside this repository:

    git clone https://github.com/DataTalksClub/zoomcamp-scoring /tmp/zoomcamp-scoring
    uv run python scripts/import_historical_zoomcamp_data.py --source-repo /tmp/zoomcamp-scoring --list
    uv run python scripts/import_historical_zoomcamp_data.py --source-repo /tmp/zoomcamp-scoring
    uv run python scripts/import_historical_zoomcamp_data.py --source-repo /tmp/zoomcamp-scoring --edition de-zoomcamp-2022
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

import django

django.setup()

from django.core.cache import cache

from courses.assignment_statistics import (
    calculate_homework_statistics,
    calculate_project_statistics,
)
from courses.leaderboard import update_leaderboard
from scripts.historical_import.certificate_import import import_edition_certificates
from scripts.historical_import.editions import build_editions
from scripts.historical_import.scoring_import import import_edition_scoring


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--source-repo",
        required=True,
        type=Path,
        help="Path to a local clone of DataTalksClub/zoomcamp-scoring.",
    )
    parser.add_argument(
        "--edition",
        action="append",
        help="Limit to this cohort slug (e.g. de-zoomcamp-2022). Repeatable.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List discovered editions and what was found for each, then exit.",
    )
    parser.add_argument(
        "--skip-certificates",
        action="store_true",
        help="Import homework/project scores only.",
    )
    parser.add_argument(
        "--skip-scoring",
        action="store_true",
        help="Import certificates only (the cohort must already exist).",
    )
    parser.add_argument(
        "--course-repos-dir",
        type=Path,
        default=Path.home() / "git",
        help="Directory containing local checkouts of the course repos "
        "(e.g. data-engineering-zoomcamp), used for real homework titles/content. "
        "Default: ~/git",
    )
    parser.add_argument(
        "--no-homework-content",
        action="store_true",
        help="Skip real homework title/content enrichment even if --course-repos-dir exists.",
    )
    return parser.parse_args()


def list_editions(editions):
    for edition in editions:
        certs = "yes" if edition.certificate_csvs else "no"
        print(
            f"{edition.cohort_slug}: "
            f"{len(edition.homeworks)} homeworks, "
            f"{len(edition.projects)} projects, "
            f"certificates={certs}"
        )


def select_editions(editions, wanted_slugs):
    if not wanted_slugs:
        return editions
    by_slug = {edition.cohort_slug: edition for edition in editions}
    missing = [slug for slug in wanted_slugs if slug not in by_slug]
    if missing:
        raise SystemExit(f"Unknown edition(s): {', '.join(sorted(missing))}")
    return [by_slug[slug] for slug in wanted_slugs]


def recalculate_cohort_scores(cohort, homeworks, projects):
    for homework in homeworks:
        calculate_homework_statistics(homework, force=True)
    for project in projects:
        calculate_project_statistics(project, force=True)
    update_leaderboard(cohort)
    cache.delete(f"leaderboard:{cohort.id}")
    cache.delete(f"leaderboard_data:{cohort.id}")
    cache.delete(f"leaderboard_yaml:{cohort.id}")


def import_edition(edition, *, skip_scoring, skip_certificates, course_repos_dir):
    cohort = None
    if not skip_scoring:
        result = import_edition_scoring(edition, course_repos_dir=course_repos_dir)
        cohort = result.cohort
        recalculate_cohort_scores(cohort, result.homeworks, result.projects)
        print(
            f"{edition.cohort_slug}: "
            f"{len(result.homeworks)} homeworks ({result.homework_submissions} submissions), "
            f"{len(result.projects)} projects ({result.project_submissions} submissions)"
        )

    if not skip_certificates:
        if cohort is None:
            from courses.models import Cohort

            cohort = Cohort.objects.get(slug=edition.cohort_slug)
        if edition.certificate_csvs:
            cert_result = import_edition_certificates(cohort, edition)
            print(
                f"{edition.cohort_slug}: "
                f"{cert_result.graduates_seen} certificates "
                f"({cert_result.certificate_urls_matched} with a matched PDF URL)"
            )
        else:
            print(f"{edition.cohort_slug}: no certificate source, skipped")


def main():
    args = parse_args()
    if not args.source_repo.is_dir():
        raise SystemExit(f"--source-repo does not exist or is not a directory: {args.source_repo}")

    editions = build_editions(args.source_repo)

    if args.list:
        list_editions(editions)
        return

    course_repos_dir = None if args.no_homework_content else args.course_repos_dir

    selected = select_editions(editions, args.edition)
    for edition in selected:
        import_edition(
            edition,
            skip_scoring=args.skip_scoring,
            skip_certificates=args.skip_certificates,
            course_repos_dir=course_repos_dir,
        )


if __name__ == "__main__":
    main()
