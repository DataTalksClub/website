#!/usr/bin/env python3
"""Load the reviewed ``/courses`` catalogue facts onto the course families.

Site feedback 3.1 gave every catalogue card the same fields -- topic, level,
length, weekly time, key tools, what you will build -- and the filters above the
grid read the same facts.  They are editorial facts about a course family, so
they live on ``Course`` (``catalog_*`` fields) and an editor curates them in the
admin afterwards; this script is only how the reviewed set gets there, from
``~/prod/dtc-data/content-staging/course_catalog_facets.json`` (outside this
repository -- see ``_docs/architecture/database-only-content.md``).

The file is a JSON object keyed by course family slug::

    {"de-zoomcamp": {"topic": "data-engineering", "level": "beginner",
                     "duration_weeks": 10, "hours_per_week": "",
                     "tools": ["Docker", "dbt"], "project": "...",
                     "fits": ["new-to-data"]}}

Reconciling, not bootstrapping: it updates families that already exist and
reports any slug it cannot find rather than inventing a family.  Every value is
validated with the model's own validators before anything is written, and the
whole file applies in one transaction or not at all.  Replaying is safe.

    uv run --frozen python scripts/prod/import_course_catalog_facets.py \\
        --database .tmp/local.sqlite3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import configure_target, reviewed_file_parser  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

REVIEWED_PATH = Path.home() / "prod" / "dtc-data" / "content-staging" / "course_catalog_facets.json"

#: reviewed-file key -> Course field
FIELD_MAP = {
    "topic": "catalog_topic",
    "level": "catalog_level",
    "duration_weeks": "catalog_duration_weeks",
    "hours_per_week": "catalog_hours_per_week",
    "tools": "catalog_tools",
    "project": "catalog_project",
    "fits": "catalog_fits",
}


class CatalogFacetImportFailure(RuntimeError):
    """A safe refusal that names the slug and field, never a whole source row."""


def load_reviewed(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CatalogFacetImportFailure(
            f"cannot read reviewed file: {type(error).__name__}"
        ) from None
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise CatalogFacetImportFailure("reviewed file must map family slugs to objects")
    for slug, entry in data.items():
        unknown = set(entry) - set(FIELD_MAP)
        if unknown:
            raise CatalogFacetImportFailure(f"{slug}: unknown keys {sorted(unknown)}")
    return data


def run(*, path: Path | None = None, apply: bool = True) -> dict[str, Any]:
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from courses.models import Course

    data = load_reviewed(path or REVIEWED_PATH)
    families = {course.slug: course for course in Course.objects.filter(slug__in=list(data))}
    missing = sorted(set(data) - set(families))

    changed_rows = []
    for slug, entry in data.items():
        course = families.get(slug)
        if course is None:
            continue
        changed = []
        for key, field_name in FIELD_MAP.items():
            if key not in entry:
                continue
            value = entry[key]
            if value is None and field_name != "catalog_duration_weeks":
                value = [] if field_name in {"catalog_tools", "catalog_fits"} else ""
            if getattr(course, field_name) != value:
                setattr(course, field_name, value)
                changed.append(field_name)
        try:
            course.full_clean(
                exclude=[f.name for f in Course._meta.fields if f.name not in FIELD_MAP.values()]
            )
        except ValidationError as error:
            fields = sorted(error.message_dict) if hasattr(error, "message_dict") else ["?"]
            raise CatalogFacetImportFailure(f"{slug}: invalid {fields}") from error
        if changed:
            changed_rows.append((course, changed))

    if apply and changed_rows:
        with transaction.atomic():
            for course, changed in changed_rows:
                course.save(update_fields=changed)

    return {
        "total": len(data),
        "updated": len(changed_rows) if apply else 0,
        "would_update": len(changed_rows),
        "missing_slugs": missing,
        "applied": apply,
    }


def _parser() -> argparse.ArgumentParser:
    return reviewed_file_parser(__doc__, REVIEWED_PATH)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(path=args.reviewed_file.resolve(), apply=not args.dry_run)
    except CatalogFacetImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
