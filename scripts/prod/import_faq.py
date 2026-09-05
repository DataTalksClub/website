#!/usr/bin/env python3
"""Import the reviewed course FAQ into a database.

One-time import.  Six courses, seventy sections and 1,401 questions taken from
a pinned revision of ``DataTalksClub/faq``.  They used to be served straight out
of ``content/faq_projection.json``, which meant the running site read public
content from a file in its own source tree; the reviewed file is now ingest
input and lives with the other one-time inputs under ``temporary/content/``.
See ``scripts/prod/__init__.py`` for what the two sync models mean.

Each course becomes one published document, because each course is one public
page.  Its sections and questions are that page's own structure and travel with
it rather than being spread over a table per nesting level -- nothing queries a
FAQ question except the page it appears on.

Everything the file claims is checked before anything is written: the schema
version, the pinned revision, the course order, every question's identifier,
relationship, answer and edit URL, and the declared counts against the records
actually present.  A file that fails any of those is refused whole.

    uv run --frozen python scripts/prod/import_faq.py \\
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

REVIEWED_PATH = PROJECT_ROOT / "temporary" / "content" / "faq_projection.json"

_QUESTION_ID = re.compile(r"^[A-Za-z0-9]{10}$", re.ASCII)


class FaqImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _fail(code: str) -> None:
    raise FaqImportFailure(code)


def load_reviewed_faq(path: Path) -> dict[str, Any]:
    """Parse and fully validate the reviewed file without touching the database."""

    from content.faq_data import (
        FAQ_COURSE_ORDER,
        FAQ_SOURCE_REPOSITORY,
        FAQ_SOURCE_REVISION,
    )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FaqImportFailure("reviewed_faq_unreadable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        _fail("reviewed_faq_schema_invalid")
    if payload.get("source") != {
        "repository": FAQ_SOURCE_REPOSITORY,
        "revision": FAQ_SOURCE_REVISION,
        "branch": "main",
        "path": "_questions",
    }:
        _fail("reviewed_faq_source_mismatch")
    courses = payload.get("courses")
    if not isinstance(courses, list) or tuple(course.get("slug") for course in courses) != tuple(
        FAQ_COURSE_ORDER
    ):
        _fail("reviewed_faq_course_order_invalid")
    declared = payload.get("counts")
    if not isinstance(declared, dict):
        _fail("reviewed_faq_counts_missing")

    seen_ids: set[str] = set()
    sections_total = questions_total = 0
    asset_paths: set[str] = set()
    for course in courses:
        slug = course.get("slug")
        if not isinstance(slug, str) or course.get("public_path") != f"/faq/{slug}.html":
            _fail("reviewed_faq_course_public_path_invalid")
        sections = course.get("sections")
        if not isinstance(sections, list):
            _fail("reviewed_faq_sections_missing")
        section_ids: set[str] = set()
        for section in sections:
            section_id = section.get("id")
            if not isinstance(section_id, str) or section_id in section_ids:
                _fail("reviewed_faq_section_id_not_unique")
            section_ids.add(section_id)
            sections_total += 1
            questions = section.get("questions")
            if not isinstance(questions, list):
                _fail("reviewed_faq_questions_missing")
            for question in questions:
                question_id = question.get("id")
                if not isinstance(question_id, str) or _QUESTION_ID.fullmatch(question_id) is None:
                    _fail("reviewed_faq_question_id_invalid")
                if question_id in seen_ids:
                    _fail("reviewed_faq_question_id_not_unique")
                seen_ids.add(question_id)
                if question.get("course") != slug or question.get("section_id") != section_id:
                    _fail("reviewed_faq_question_relationship_inconsistent")
                if not isinstance(question.get("question"), str) or not isinstance(
                    question.get("answer"), str
                ):
                    _fail("reviewed_faq_question_body_invalid")
                source_path = question.get("source_path")
                edit_url = question.get("edit_url")
                if not isinstance(source_path, str) or not source_path.startswith(
                    f"_questions/{slug}/"
                ):
                    _fail("reviewed_faq_source_path_invalid")
                if not isinstance(edit_url, str) or not edit_url.endswith(str(source_path)):
                    _fail("reviewed_faq_edit_url_invalid")
                questions_total += 1
                image_ids: set[str] = set()
                for image in question.get("images", []):
                    image_id = image.get("id")
                    image_path = image.get("public_path")
                    if not isinstance(image_id, str) or image_id in image_ids:
                        _fail("reviewed_faq_image_id_not_unique")
                    image_ids.add(image_id)
                    if not isinstance(image_path, str) or not image_path.startswith(
                        f"/faq/images/{slug}/"
                    ):
                        _fail("reviewed_faq_image_public_path_invalid")
                    asset_paths.add(image_path)
    if declared != {
        "courses": len(courses),
        "sections": sections_total,
        "questions": questions_total,
        "assets": len(asset_paths),
    }:
        _fail("reviewed_faq_counts_mismatch")
    return payload


def run(*, path: Path | None = None, apply: bool = True) -> dict[str, Any]:
    source_file = path or REVIEWED_PATH
    payload = load_reviewed_faq(source_file)
    courses = list(payload["courses"])
    if not apply:
        return {"courses": len(courses), "applied": False}

    import uuid

    from django.db import transaction
    from django.utils import timezone

    from content.faq_data import (
        FAQ_CONTENT_KIND,
        FAQ_SOURCE_REPOSITORY,
        FAQ_SOURCE_STABLE_ID,
    )
    from content.models import ContentDocument, ContentRelease, ContentSource
    from content.services import (
        ActivateContentRelease,
        MarkReleaseReady,
        TransitionContentRelease,
        activate_content_release,
        asset_manifest_checksum_for,
        begin_release_validation,
        mark_release_ready,
    )
    from core.services import ServiceContext

    owner, name = FAQ_SOURCE_REPOSITORY.split("/")
    with transaction.atomic():
        source, _ = ContentSource.objects.get_or_create(
            stable_id=FAQ_SOURCE_STABLE_ID,
            defaults={
                "display_name": "DataTalks.Club course FAQ",
                "repository_owner": owner,
                "repository_name": name,
                "branch": "main",
                "path_allowlist": ["/faq/"],
                "adapter_type": "reviewed-faq-v1",
                "mount_path": "/faq/",
                "enabled": True,
            },
        )
        sequence = (
            ContentRelease.objects.filter(source=source)
            .order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
            or 0
        ) + 1
        release = ContentRelease.objects.create(
            source=source,
            sequence=sequence,
            based_on_release_id=source.active_release_id,
            commit_sha=f"{sequence:040x}",
            parser_version="reviewed-faq-v1",
            rendering_version="reviewed-faq-v1",
            status=ContentRelease.Status.FETCHING,
            requested_at=timezone.now(),
            request_provenance={"kind": "import", "source_file": source_file.name},
        )
        ContentDocument.objects.bulk_create(
            [
                ContentDocument(
                    release=release,
                    content_kind=FAQ_CONTENT_KIND,
                    stable_key=str(course["slug"]),
                    source_path=str(course.get("json_path") or f"_questions/{course['slug']}"),
                    checksum=_course_digest(course),
                    exact_public_path=str(course["public_path"]),
                    slug=str(course["slug"]),
                    title=str(course["name"]),
                    # The sections and their questions are this page's own
                    # structure; nothing queries a question except the page it
                    # appears on, so they travel with the page.
                    adapter_metadata={
                        "sections": course["sections"],
                        "section_count": course.get("section_count"),
                        "question_count": course.get("question_count"),
                        "json_path": course.get("json_path"),
                    },
                    rendered_html=f"<h1>{course['name']}</h1>",
                    is_published=True,
                )
                for course in courses
            ]
        )

    context = ServiceContext(
        correlation_id=f"import-faq-{uuid.uuid4().hex}",
        actor_ref="system:import_faq",
    )
    release = begin_release_validation(
        TransitionContentRelease(release_id=release.id, expected_revision=release.revision),
        context=context,
    )
    release = mark_release_ready(
        MarkReleaseReady(
            release_id=release.id,
            expected_revision=release.revision,
            asset_manifest_checksum=asset_manifest_checksum_for(release.id),
        ),
        context=context,
    )
    source.refresh_from_db()
    activate_content_release(
        ActivateContentRelease(
            source_id=source.id,
            release_id=release.id,
            expected_source_revision=source.revision,
            expected_release_revision=release.revision,
            reason="import-faq",
        ),
        context=context,
    )
    return {
        "courses": len(courses),
        "sections": int(payload["counts"]["sections"]),
        "questions": int(payload["counts"]["questions"]),
        "release": str(release.id),
        "sequence": sequence,
        "applied": True,
    }


def _course_digest(course: dict[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(course, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--reviewed-file", type=Path, default=REVIEWED_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the reviewed file and write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(path=args.reviewed_file.resolve(), apply=not args.dry_run)
    except FaqImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
