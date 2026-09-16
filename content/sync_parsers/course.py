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

from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content.course_catalog import parse_course_catalog
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
        course_title = builder._string(raw.get("title"), field="course title", maximum=200)
        if "catalog" not in raw:
            return []
        editions = parse_course_catalog(
            raw["catalog"],
            fail=lambda code, pointer: base.fail(code, SOURCE_FILE),
        )
        items: list[SourceItem] = []
        for edition in editions:
            slug = edition["slug"]
            family_slug, year = builder.cohort_family_identity(slug)
            title = edition["title"] or builder._string(
                f"{course_title} {year}", field="course edition title", maximum=500
            )
            record = {
                "slug": slug,
                "public_path": f"/courses/{family_slug}/{year}",
                "title": title,
                "finished": edition["finished"],
                "homework_count": edition["homework_count"],
                "project_count": edition["project_count"],
                "first_deadline": edition["first_deadline"],
                "last_deadline": edition["last_deadline"],
                "provenance": builder._provenance(
                    repository=f"https://github.com/{source.repo_name}",
                    revision=checkout.commit_sha,
                    source_path=SOURCE_FILE,
                    source_key=slug,
                    checksum=base.checksum_of(checkout, SOURCE_FILE),
                ),
            }
            items.append(
                SourceItem(
                    key=slug,
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
