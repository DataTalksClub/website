"""The ``course.yaml`` ``catalog:`` block: the authored course catalogue copy.

Two readers validate one repository's catalogue copy with the same rules: the
content sync parser turns the block into the catalogue's synced ``course``
rows, and the course-repository ingest accepts a repository only when its
catalog block is well-formed, so an authoring defect upstream fails the
ingest with a bounded error instead of surfacing later as a sync failure.
Both readers fail closed; neither invents a value the repository did not
declare.

One block per repository, listing the editions whose catalogue copy the
course publishes: an edition's ``finished`` flag states that the edition's
run has concluded, the counts and deadlines are carried exactly as authored,
and a repository that declares no block simply publishes no catalogue copy.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, NoReturn

#: The stable-key shape every synced record's slug must satisfy (the
#: projection builder's ``SAFE_KEY``, restated here so the ingest validates
#: the block without importing the projection build).
EDITION_SLUG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,199}$")

#: The mechanical ``<family>-<year>`` tail an edition slug must end with --
#: the catalogue's public paths address an edition at
#: ``/courses/<family>/<year>``, derived from exactly these two parts.
EDITION_YEAR_TAIL = re.compile(r"^(?:.+)-(\d{4})$")

EDITION_FIELDS = frozenset(
    {
        "slug",
        "title",
        "finished",
        "homework_count",
        "project_count",
        "first_deadline",
        "last_deadline",
    }
)
EDITION_REQUIRED = EDITION_FIELDS - {"title"}

MAX_SLUG_LENGTH = 200
MAX_TITLE_LENGTH = 500
MAX_DEADLINE_LENGTH = 40
MAX_COUNT = 1000


def parse_course_catalog(
    raw: Any,
    *,
    fail: Callable[[str, str], NoReturn],
    source_path: str = "course.yaml",
) -> tuple[dict[str, Any], ...]:
    """Validate one repository's catalog block; return its edition mappings.

    ``fail(code, pointer)`` raises in the caller's own idiom -- the sync
    parser raises its bounded parser error, the repository ingest its schema
    error -- so one rule set produces each reader's native diagnostics.
    """

    if not isinstance(raw, dict) or set(raw) != {"editions"}:
        fail("course_catalog_rejected", "/catalog")
    editions = raw["editions"]
    if not isinstance(editions, list) or not editions:
        fail("course_catalog_rejected", "/catalog/editions")
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for index, entry in enumerate(editions):
        pointer = f"/catalog/editions/{index}"
        if not isinstance(entry, dict) or not EDITION_REQUIRED <= set(entry) <= EDITION_FIELDS:
            fail("course_edition_rejected", pointer)
        slug = entry["slug"]
        if (
            not isinstance(slug, str)
            or EDITION_SLUG.fullmatch(slug) is None
            or ".." in slug
            or EDITION_YEAR_TAIL.fullmatch(slug) is None
        ):
            fail("course_edition_slug_rejected", f"{pointer}/slug")
        if slug in seen:
            fail("duplicate_course_edition", pointer)
        seen.add(slug)
        finished = entry["finished"]
        if type(finished) is not bool:
            fail("course_edition_finished_rejected", f"{pointer}/finished")
        title = entry.get("title")
        if title is not None and (
            not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_LENGTH
        ):
            fail("course_edition_title_rejected", f"{pointer}/title")
        parsed.append(
            {
                "slug": slug,
                "title": title,
                "finished": finished,
                "homework_count": _edition_count(
                    entry["homework_count"], f"{pointer}/homework_count", fail
                ),
                "project_count": _edition_count(
                    entry["project_count"], f"{pointer}/project_count", fail
                ),
                "first_deadline": _edition_deadline(
                    entry["first_deadline"], f"{pointer}/first_deadline", fail
                ),
                "last_deadline": _edition_deadline(
                    entry["last_deadline"], f"{pointer}/last_deadline", fail
                ),
            }
        )
    return tuple(parsed)


def _edition_count(value: Any, pointer: str, fail: Callable[[str, str], NoReturn]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        fail("course_edition_count_rejected", pointer)
    return value


def _edition_deadline(value: Any, pointer: str, fail: Callable[[str, str], NoReturn]) -> str:
    # YAML resolves an unquoted ISO stamp to a datetime; both spellings
    # normalize to the same ISO form the reviewed records carry. An explicitly
    # empty deadline says the edition publishes no deadlines at all (a
    # permanently self-paced edition).
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or len(value) > MAX_DEADLINE_LENGTH or "\x00" in value:
        fail("course_edition_deadline_rejected", pointer)
    text = value.strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        fail("course_edition_deadline_rejected", pointer)
