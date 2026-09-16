"""Course catalogue copy parser: the editions each course repository declares.

The catalogue copy -- the per-edition ``finished`` flag, the homework and
project counts and the first/last deadlines -- used to be staged from a
checked-in production-like specification claiming CMP provenance.  The owner
ruled it authored content: each course repository states its own catalogue
copy in the ``catalog:`` block of its ``course.yaml``, and this parser reads
it like every other synced kind, so it refreshes on push rather than on
ingest cadence.

The record rules follow the reviewed projection's course pass
(``_courses``): one record per declared edition, its public path derived
mechanically from the edition slug (``<family>-<year>`` becomes
``/courses/<family>/<year>``, with the one reviewed family-slug correction
applied), ``finished`` stating that the edition's run has concluded, and the
counts and deadlines exactly as authored.  A repository whose ``course.yaml``
declares no ``catalog:`` block publishes no catalogue records -- the
collection follows the repositories, as the platform catalog does.
"""

from datetime import datetime
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base

CONTENT_KIND = "course"
SOURCE_FILE = "course.yaml"

#: The engine sources that publish course catalogue copy: one per registered
#: course repository (``content_sync/course_repository_sources.json`` is the
#: registration input for the same set).  A course repository is its own sync
#: source, so the catalogue composes the collection across all of them the
#: way the media records compose across their two.
COURSE_SOURCE_SLUGS = (
    "ai-dev-tools-zoomcamp",
    "de-zoomcamp",
    "llm-zoomcamp",
    "ml-zoomcamp",
    "mlops-zoomcamp",
    "sma-zoomcamp",
)

#: Exactly the fields one edition's catalogue copy may carry; ``title`` is
#: optional and defaults to the repository's own course title with the
#: edition year appended, the derivation the staged specification's titles
#: followed.
_EDITION_FIELDS = frozenset(
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
_EDITION_REQUIRED = _EDITION_FIELDS - {"title"}

#: A deadline is an ISO-8601 stamp; an explicitly empty one says the edition
#: publishes no deadlines at all (a permanently self-paced edition).
_MAX_DEADLINE_LENGTH = 40
_MAX_COUNT = 1000


def _deadline(value, *, field: str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    text = builder._string(value, field=field, maximum=_MAX_DEADLINE_LENGTH)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        base.fail(f"{field} rejected", SOURCE_FILE)


def _edition_record(raw, course_title: str) -> dict:
    """One edition's catalogue record, in the reviewed course record shape."""

    if not isinstance(raw, dict) or not _EDITION_REQUIRED <= set(raw) <= _EDITION_FIELDS:
        base.fail("course edition record rejected", SOURCE_FILE)
    slug = builder._safe_key(raw["slug"], field="course edition slug")
    try:
        family_slug, year = builder.cohort_family_identity(slug)
    except ValueError:
        base.fail("course edition slug rejected", SOURCE_FILE)
    finished = raw["finished"]
    if type(finished) is not bool:
        base.fail("course edition finished flag rejected", SOURCE_FILE)
    record = {
        "slug": slug,
        "public_path": f"/courses/{family_slug}/{year}",
        "title": builder._string(
            raw.get("title", f"{course_title} {year}"),
            field="course edition title",
            maximum=500,
        ),
        "finished": finished,
        "homework_count": _edition_count(raw["homework_count"], "course edition homework count"),
        "project_count": _edition_count(raw["project_count"], "course edition project count"),
        "first_deadline": _deadline(raw["first_deadline"], field="course edition first deadline"),
        "last_deadline": _deadline(raw["last_deadline"], field="course edition last deadline"),
    }
    return record


def _edition_count(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_COUNT:
        base.fail(f"{field} rejected", SOURCE_FILE)
    return value


class CourseCatalogParser:
    """Publishes each course repository's declared editions as synced rows."""

    def discover(self, checkout, source):
        if source.slug not in COURSE_SOURCE_SLUGS:
            return []
        paths = {PurePosixPath(str(relative)).as_posix() for relative in checkout.files()}
        if SOURCE_FILE not in paths:
            # The catalogue copy is authored content: a repository that
            # declares none offers no editions, and the stale rows go with
            # the source.
            return []
        raw = builder._load_yaml(base.snapshot_path(checkout, SOURCE_FILE))
        if not isinstance(raw, dict):
            base.fail("course manifest rejected", SOURCE_FILE)
        title = builder._string(raw.get("title"), field="course title", maximum=200)
        if "catalog" not in raw:
            return []
        catalog = raw["catalog"]
        if not isinstance(catalog, dict) or set(catalog) != {"editions"}:
            base.fail("course catalog rejected", SOURCE_FILE)
        editions = catalog["editions"]
        if not isinstance(editions, list) or not editions:
            base.fail("course catalog rejected", SOURCE_FILE)
        items: list[SourceItem] = []
        seen: set[str] = set()
        for entry in editions:
            record = _edition_record(entry, title)
            if record["slug"] in seen:
                base.fail("duplicate course edition", SOURCE_FILE)
            seen.add(record["slug"])
            record["provenance"] = builder._provenance(
                repository=f"https://github.com/{source.repo_name}",
                revision=checkout.commit_sha,
                source_path=SOURCE_FILE,
                source_key=record["slug"],
                checksum=base.checksum_of(checkout, SOURCE_FILE),
            )
            items.append(
                SourceItem(
                    key=record["slug"],
                    path=SOURCE_FILE,
                    data={
                        "record": record,
                        "checksum": base.checksum_of(checkout, SOURCE_FILE),
                    },
                )
            )
        return items

    def upsert(self, item, source, media):
        record = item.data["record"]
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=record["slug"],
            slug=record["slug"],
            title=record["title"],
            summary="",
            public_path=record["public_path"],
            source_path=SOURCE_FILE,
            checksum=item.data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug not in COURSE_SOURCE_SLUGS:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)


register_parser(CONTENT_KIND, CourseCatalogParser())
