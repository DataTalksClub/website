#!/usr/bin/env python3
"""Import the reviewed project-gallery repository enrichment into a database.

One-time import (issue #416).  The reviewed exports live outside this
repository, in ``~/prod/dtc-data/content-staging/``:

* ``project_gallery_repo_enrichment.jsonl`` -- one row per distinct GitHub
  repository submitted to any project: what it does, why it is worth
  reading, observed gaps, topics, availability, and whether it is a
  coursework collection.  Extracted from the repositories themselves
  (README snapshots and live availability probes); nothing upstream is
  going to move, so this is read once at migration.
* ``project_gallery_structured.jsonl`` -- the ``course-structured-v1``
  records extracted from the same README snapshots (technologies, problem
  domain, dataset, metrics, deployment, ...).  Each record is stored whole
  as JSON on the matching enrichment row, keyed on the lowercased
  ``owner/name`` slug the two files share.  The pair is one reviewed
  release: a structured record whose repository has no enrichment row is
  refused rather than silently dropped.

The gallery reads the database only -- public content is database-owned
(``_docs/architecture/database-only-content.md``) -- and a submission whose
repository has no enrichment row renders plainly.  Replay is safe: rows are
keyed on the slug, so a second run reports ``unchanged`` and writes nothing.

    uv run --frozen python scripts/prod/import_project_repo_enrichment.py \\
        --database .tmp/local.sqlite3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True

CONTENT_STAGING = Path.home() / "prod" / "dtc-data" / "content-staging"
REVIEWED_ENRICHMENT_PATH = CONTENT_STAGING / "project_gallery_repo_enrichment.jsonl"
REVIEWED_STRUCTURED_PATH = CONTENT_STAGING / "project_gallery_structured.jsonl"

STRUCTURED_SCHEMA = "course-structured-v1"

#: The homework/coursework relabel heuristic shared with the enrichment
#: extraction; a repository named or described as a homework dump is labelled
#: coursework rather than capstone.
_HW_NAME = re.compile(r"hw[-_]?(submissions?|dumps?)")
_HW_TEXT_1 = re.compile(r"homework (submissions?|exercises|assignments|solutions|notebooks?)")
_HW_TEXT_2 = re.compile(
    r"(collection|series|repo(sitory)?|folder) of (homework|coursework|exercise)"
)

#: The enrichment fields this import writes, and the JSONL keys they read.
#: Score, cohort, course, and author facts stay upstream -- the model carries
#: only what could not be derived from the submissions alone.
FIELD_MAP = (
    ("effective_url", "effective_url"),
    ("availability", "availability"),
    ("card_summary", "card_summary"),
    ("what_it_is", "what_it_is"),
    ("interesting", "interesting"),
    ("why_check", "why_check"),
    ("improvements", "improvements"),
    ("topics", "topics"),
    ("confidence", "confidence"),
)


class ProjectRepoEnrichmentImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _is_coursework(row: dict[str, Any]) -> bool:
    name = row["repo"].split("/")[-1].lower()
    if "homework" in name or _HW_NAME.search(name):
        return True
    text = " ".join(
        row.get(field) or "" for field in ("card_summary", "what_it_is", "interesting")
    ).lower()
    return bool(_HW_TEXT_1.search(text) or _HW_TEXT_2.search(text))


def _read_jsonl(path: Path, what: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProjectRepoEnrichmentImportFailure(f"{what}_source_not_found")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not row.get("repo"):
            raise ProjectRepoEnrichmentImportFailure(f"{what}_row_without_repo")
        rows.append(row)
    return rows


def _load_enrichment(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path, "enrichment")
    keyed: dict[str, dict[str, Any]] = {}
    for row in rows:
        repo_lower = row["repo"].lower()
        if repo_lower in keyed:
            raise ProjectRepoEnrichmentImportFailure("duplicate_enrichment_repo")
        keyed[repo_lower] = row
    return keyed


def _load_structured(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path, "structured")
    keyed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("schema") != STRUCTURED_SCHEMA:
            raise ProjectRepoEnrichmentImportFailure("unexpected_structured_schema")
        repo_lower = row["repo"].lower()
        if repo_lower in keyed:
            raise ProjectRepoEnrichmentImportFailure("duplicate_structured_repo")
        keyed[repo_lower] = row
    return keyed


def _row_values(row: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field, key in FIELD_MAP:
        values[field] = row.get(key) or ""
    values["availability"] = values["availability"] or "live"
    values["is_unavailable"] = values["availability"] != "live"
    values["is_coursework"] = _is_coursework(row)
    return values


def run(
    *,
    enrichment_source: Path | None = None,
    structured_source: Path | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Upsert the reviewed exports, reporting what each pass changed.

    Both files are parsed and cross-checked before anything is written, and
    the writes sit in one transaction, so a refusal or a failure leaves the
    database exactly as it was.
    """
    from django.db import transaction

    from courses.models import ProjectRepoEnrichment

    enrichment_rows = _load_enrichment(enrichment_source or REVIEWED_ENRICHMENT_PATH)
    structured_rows = _load_structured(structured_source or REVIEWED_STRUCTURED_PATH)

    unmatched = sorted(set(structured_rows) - set(enrichment_rows))
    if unmatched:
        raise ProjectRepoEnrichmentImportFailure("structured_without_enrichment_row")

    existing = {row.repo_lower: row for row in ProjectRepoEnrichment.objects.all()}
    creates: list[ProjectRepoEnrichment] = []
    updates: list[tuple[ProjectRepoEnrichment, dict[str, Any]]] = []
    unchanged = 0
    for repo, row in enrichment_rows.items():
        repo_lower = repo
        values = _row_values(row)
        current = existing.get(repo_lower)
        if current is None:
            creates.append(ProjectRepoEnrichment(repo=row["repo"], repo_lower=repo_lower, **values))
            continue
        changed = {
            field: value for field, value in values.items() if getattr(current, field) != value
        }
        if changed:
            updates.append((current, changed))
        else:
            unchanged += 1

    structured_unchanged = 0
    structured_updates: list[tuple[ProjectRepoEnrichment, dict[str, Any]]] = []
    structured_creates = 0
    for repo_lower, record in structured_rows.items():
        target = existing.get(repo_lower)
        if target is not None and target.structured == record:
            structured_unchanged += 1
            continue
        if target is None:
            # A new enrichment row above carries this record at creation.
            record_holder = next((row for row in creates if row.repo_lower == repo_lower), None)
            if record_holder is None:  # pragma: no cover - unmatched refused above
                raise ProjectRepoEnrichmentImportFailure("structured_without_enrichment_row")
            record_holder.structured = record
            structured_creates += 1
            continue
        structured_updates.append((target, record))

    report = {
        "applied": apply,
        "enrichment": {
            "total": len(enrichment_rows),
            "created": len(creates),
            "updated": len(updates),
            "unchanged": unchanged,
        },
        "structured": {
            "total": len(structured_rows),
            "created": structured_creates,
            "updated": len(structured_updates),
            "unchanged": structured_unchanged,
        },
    }
    if not apply:
        return report

    with transaction.atomic():
        ProjectRepoEnrichment.objects.bulk_create(creates, batch_size=500)
        for current, changed in updates:
            for field, value in changed.items():
                setattr(current, field, value)
            current.save(update_fields=[*changed, "updated_at"])
        for target, record in structured_updates:
            target.structured = record
            target.save(update_fields=["structured", "updated_at"])
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--enrichment-source", type=Path, default=REVIEWED_ENRICHMENT_PATH)
    parser.add_argument("--structured-source", type=Path, default=REVIEWED_STRUCTURED_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate both files against the target database and write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(
            enrichment_source=args.enrichment_source.resolve(),
            structured_source=args.structured_source.resolve(),
            apply=not args.dry_run,
        )
    except ProjectRepoEnrichmentImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
