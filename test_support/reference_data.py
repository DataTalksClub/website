"""Reference data the whole test database starts from.

Two sets of rows used to arrive in a test database because a migration inserted
them: public event identities and reviewed homepage testimonials.  Neither is
schema, so neither belongs in a migration -- but both are content the product
is built around, and hundreds of tests read them without creating them.

Loading them here keeps that arrangement while moving the rows to where data
belongs: this runs once, after ``migrate`` has built the test database, and
writes exactly what the retired seeding migrations wrote.  The production path
for the same two sets is ``scripts/prod/import_events.py`` and
``scripts/prod/import_testimonials.py``.

Every row below comes from ``test_support/fixtures/reference/`` -- a small,
synthetic, checked-in set, run through the real production importers.  It used
to be the real reviewed content snapshot under ``temporary/content/`` (421
events, 1,684 aliases, 2,203 content documents): the owner's ruling was that
"the test suites shouldn't care about these files", and that tree has since
moved outside this repository to ``~/prod/dtc-data/content-staging/`` (see
``_docs/architecture/database-only-content.md``), which CI and a fresh
checkout cannot reach.  Every test that only needs *some* realistic rows to
exist -- the overwhelming majority, since most either derive their
expectations from the database itself or assert on HTTP status and structure
-- keeps working unchanged against this smaller set.  A minority of tests that
asserted the real corpus's exact shape (a literal 421, a literal 1,684, a
pinned real digest) were rewritten or removed alongside this change; see the
commit that introduced this fixture set for the full accounting.

Every importer here still runs for real: this only supplies a different,
smaller input.  The one exception is the public content catalogue
(articles/podcasts/books/people/wiki/courses/media): its real loader
(``scripts/prod/public_projection_source.load_checked_projection``) validates
its input against the exact accepted upstream revisions, source repositories,
and a handful of pinned counts from the real reviewed snapshot (issue #253) --
by design, so a compromised or drifted upstream is refused rather than
silently imported.  A synthetic catalogue cannot satisfy that pin and still be
synthetic, so ``load_reviewed_public_content`` below calls the same
``scripts/prod/import_public_content.run`` production write path with just its
file-reading, upstream-pinned loader swapped out -- the database-writing half
that ``content.catalogue`` actually reads runs unchanged and unmocked.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.db import transaction

#: Where the small, synthetic reference fixtures live. Test support and
#: ``scripts/prod`` each name the location they read, and neither imports it
#: from the other.
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "reference"
EVENT_IDENTITY_MANIFEST = FIXTURE_ROOT / "event_identity_manifest.json"
EVENT_CONTENT = FIXTURE_ROOT / "events.json"
DOCS_PROJECTION = FIXTURE_ROOT / "docs_projection.json"
FAQ_PROJECTION = FIXTURE_ROOT / "faq_projection.json"
HOMEPAGE_TESTIMONIALS = FIXTURE_ROOT / "homepage_testimonials.json"
PUBLIC_CONTENT_CATALOGUE = FIXTURE_ROOT / "public_content_catalogue.json"
SLACK_PAGE = FIXTURE_ROOT / "slack_page.json"
PODCAST_PLATFORMS_SEED = FIXTURE_ROOT / "podcast_platforms.json"

#: The collections the synthetic catalogue carries as lists in JSON, but which
#: the real in-memory catalogue (and everything that reads it) expects as
#: tuples -- the same shape ``load_checked_projection`` returns.
_CATALOGUE_COLLECTIONS = (
    "articles",
    "podcasts",
    "books",
    "people",
    "wiki",
    "courses",
    "media",
    "podcast_platforms",
)


def event_schedules() -> dict[str, dict]:
    """The synthetic content records indexed by the identity they belong to.

    The shared event row cannot exist without a schedule, so the checked-in
    identity manifest and the checked-in content fixture are joined here -- the
    same join the production rebuild performs (#412).
    """

    payload = json.loads(EVENT_CONTENT.read_text(encoding="utf-8"))
    return {record["identity_id"]: record for record in payload}


def load_event_identities() -> int:
    """Insert the synthetic event rows exactly as they are checked in.

    Each manifest identity becomes one shared ``events.Event`` row (its
    ``content_id`` is the site identity UUID) plus one ``content.EventSource``
    provenance row, scheduled from the synthetic content fixture.
    """

    from community_base.events.models import Event

    from content.models import EventSource
    from events.identity import (
        encode_event_tags,
        ensure_public_id_sequence,
        package_kind_for_type,
    )
    from scripts.prod.identity_manifest import load_identity_manifest

    manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    schedules = event_schedules()
    events = []
    sources = []
    for item in manifest.events:
        record = schedules[str(item.id)]
        starts_at = datetime.fromisoformat(record["starts_at"])
        ends_at = datetime.fromisoformat(record["ends_at"]) if record.get("ends_at") else None
        events.append(
            Event(
                content_id=item.id,
                public_id=item.public_id,
                title=item.title,
                slug=item.slug,
                status="upcoming",
                start_datetime=starts_at,
                end_datetime=ends_at,
                kind=package_kind_for_type(record.get("type")),
                tags=encode_event_tags(
                    event_type=record.get("type"),
                    season=record.get("season"),
                    episode=record.get("episode"),
                ),
                source_repo=item.source.repository,
                source_path=item.source_path,
                source_commit=item.source.revision,
            )
        )
        sources.append(
            EventSource(
                event=events[-1],
                repository=item.source.repository,
                revision=item.source.revision,
                source_key=item.source.source_key,
                source_path=item.source_path,
                source_checksum=item.source_checksum,
            )
        )
    Event.objects.bulk_create(events)
    EventSource.objects.bulk_create(sources)
    ensure_public_id_sequence()
    return len(events)


def load_event_content() -> int:
    """Attach the synthetic event content, the way the production import does.

    The identities above are created scheduled but undescribed;
    :func:`events.content_import.import_event_content` writes each row's
    description, speakers and links, and the public records read the result.
    """

    from scripts.prod.import_events import import_content

    return int(import_content(source=EVENT_CONTENT, apply=True)["events"])


def load_reviewed_docs() -> int:
    """Publish the synthetic documentation, the way the production import does.

    The docs used to be read straight out of a file in the app, so every test
    that touched a documentation route got them for free. They are database
    rows now, and hundreds of tests still read them without creating them, so
    the same import production runs seeds them here -- once, after ``migrate``,
    like the event identities above.
    """

    from scripts.prod.import_docs import run

    return int(run(path=DOCS_PROJECTION, apply=True)["pages"])


def load_synced_docs() -> int:
    """Publish the synthetic documentation the way the engine does.

    The documentation pages are ``community_base.knowledge_base`` rows in the
    ``docs`` section, written by the ``dtc-docs`` parser (D7.1), not the staged
    release this module also seeds. Production writes them by parsing the
    repository checkout; this seeds the same rows from the synthetic import
    payload, through the same package upsert the parser uses -- the stored
    public path, the site-rendered HTML, and the front matter that has no column
    in the page's record.
    """

    from community_base.content_sync.models import ContentSource as EngineContentSource
    from community_base.knowledge_base import sync as knowledge_base_sync
    from community_base.knowledge_base.models import SECTION_DOCS

    from content.docs_rendering import render_docs_markdown

    payload = json.loads(DOCS_PROJECTION.read_text(encoding="utf-8"))
    source = EngineContentSource.objects.get_or_create(
        slug="dtc-docs",
        defaults={
            "repo_name": "DataTalksClub/docs",
            # A synthetic secret so the row satisfies the engine's own shape
            # rules; nothing here reads or keeps a real credential.
            "webhook_secret": "test-support-synthetic-secret",
        },
    )[0]

    declared_assets = [asset["source_path"] for asset in payload.get("assets", [])]
    pages = list(payload["pages"])
    keys_by_public_path = {
        str(page["public_path"]): _docs_stable_key(str(page["source_path"])) for page in pages
    }
    # A parent must already be stored when its child is written, and the file
    # orders pages by path, so the shallowest public paths go first.
    pages.sort(key=lambda page: (str(page["public_path"]).count("/"), str(page["public_path"])))
    for page in pages:
        referenced = [ref for ref in declared_assets if f"/docs/{ref}" in page["body"]]
        if page["public_path"] == "/docs/":
            # The synthetic payload declares an asset no body references; the
            # real corpus's landing page carries the brand avatar, so the root
            # page owns the declared remainder here.
            images = referenced + [ref for ref in declared_assets if ref not in referenced]
        else:
            images = referenced
        stable_key = _docs_stable_key(str(page["source_path"]))
        parent_path = page.get("parent_path")
        body = str(page["body"])
        body_html, headings = render_docs_markdown(body)
        knowledge_base_sync.upsert_page(
            source,
            section=SECTION_DOCS,
            slug=stable_key,
            title=page["title"],
            summary=page.get("description") or "",
            body=body,
            body_html=body_html,
            parent_slug=keys_by_public_path.get(str(parent_path)) if parent_path else None,
            nav_order=int(page.get("nav_order") or 0),
            public_path=page["public_path"],
            record={
                "images": images,
                "headings": [dict(heading) for heading in headings],
                "body_sha256": page["body_sha256"],
                "parent": page.get("parent"),
                "grand_parent": page.get("grand_parent"),
                "grand_parent_path": page.get("grand_parent_path"),
                "nav_order": page.get("nav_order"),
                "has_children": bool(page.get("has_children")),
                "has_toc": bool(page.get("has_toc", True)),
                "permalink": page.get("permalink"),
                "edit_url": page.get("edit_url") or "",
            },
            commit_sha=page.get("source_revision", ""),
            source_path=page["source_path"],
            checksum=page["body_sha256"],
        )
    return len(pages)


def _docs_stable_key(source_path: str) -> str:
    """The docs parser's stable key: the source path without its ``index`` stem."""

    parts = [part for part in source_path.removesuffix(".md").split("/") if part]
    if parts[-1] == "index":
        parts.pop()
    return "/".join(parts) or "index"


def load_reviewed_faq() -> int:
    """Publish the synthetic course FAQ, the way the production import does."""

    from scripts.prod.import_faq import run

    return int(run(path=FAQ_PROJECTION, apply=True)["courses"])


def load_synced_faq() -> int:
    """Publish the synthetic course FAQ as synced rows, the way the engine does.

    The FAQ pages read ``SyncedDocument`` rows written by the ``dtc-faq``
    parser (issue #384), not the staged release this module also seeds. This
    seeds the same courses from the synthetic import payload in the parser's
    record shape: the section tree with the raw question bodies and the
    source-relative image paths the read model translates at request time.
    """

    import hashlib
    import json

    from community_base.content_sync.models import ContentSource as EngineContentSource

    from content.faq_data import _faq_question_slug
    from content.models import SyncedDocument

    payload = json.loads(FAQ_PROJECTION.read_text(encoding="utf-8"))
    source = EngineContentSource.objects.get_or_create(
        slug="dtc-faq",
        defaults={
            "repo_name": "DataTalksClub/faq",
            # A synthetic secret so the row satisfies the engine's own shape
            # rules; nothing here reads or keeps a real credential.
            "webhook_secret": "test-support-synthetic-secret",
        },
    )[0]

    rows = []
    for course in payload["courses"]:
        slug = course["slug"]
        sections = []
        declared_images: list[str] = []
        for section in course["sections"]:
            questions = []
            for order, question in enumerate(section["questions"], start=1):
                source_path = str(question.get("source_path") or "")
                filename = source_path.rsplit("/", 1)[-1]
                images = []
                for image in question.get("images") or []:
                    image_file = str(image["public_path"]).rsplit("/", 1)[-1]
                    declared_images.append(image_file)
                    images.append(
                        {
                            "id": str(image["id"]),
                            "description": str(image.get("description") or ""),
                            "path": image_file,
                        }
                    )
                questions.append(
                    {
                        "id": str(question["id"]),
                        "slug": _faq_question_slug(filename) if filename else None,
                        "question": str(question["question"]),
                        "sort_order": order,
                        "images": images,
                        "body": str(question["answer"]),
                        "source_path": source_path,
                    }
                )
            sections.append(
                {
                    "id": str(section["id"]),
                    "name": str(section.get("name") or section.get("title") or ""),
                    "comment": "",
                    "questions": questions,
                }
            )
        record = {
            "course": slug,
            "course_name": str(course["name"]),
            "slack_channel": "",
            "sections": sections,
            "declared_images": sorted(set(declared_images)),
            "source_path": f"_questions/{slug}/_metadata.yaml",
            "provenance": {
                "repository": "DataTalksClub/faq",
                "revision": str(payload.get("source", {}).get("revision", "")),
                "source_path": f"_questions/{slug}/_metadata.yaml",
                "source_key": slug,
                "checksum": "",
            },
        }
        checksum = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        record["provenance"]["checksum"] = checksum
        rows.append(
            SyncedDocument(
                source=source,
                content_kind="faq",
                stable_key=slug,
                slug=slug,
                title=course["name"],
                summary="",
                public_path=f"/faq/{slug}.html",
                source_path=record["source_path"],
                checksum=checksum,
                record=record,
            )
        )
    SyncedDocument.objects.bulk_create(rows)
    return len(rows)


def _synthetic_catalogue() -> dict[str, Any]:
    import json

    raw = json.loads(PUBLIC_CONTENT_CATALOGUE.read_text(encoding="utf-8"))
    catalogue = dict(raw)
    for name in _CATALOGUE_COLLECTIONS:
        catalogue[name] = tuple(catalogue[name])
    return catalogue


def load_reviewed_public_content() -> int:
    """Publish the synthetic editorial catalogue, the way the production import does.

    ``scripts.prod.import_public_content.run`` is the real, unmocked write
    path: it opens a reviewed release, converts the catalogue's records to
    ``ContentDocument`` rows, and activates the release, exactly as production
    does. Only ``load_reviewed_catalogue`` -- the file-reading step that
    checks the real catalogue against the accepted upstream pin -- is replaced
    with the small synthetic set, because that pin is specifically about the
    real reviewed snapshot and a synthetic stand-in cannot satisfy it (see the
    module docstring).
    """

    import scripts.prod.import_public_content as import_public_content

    catalogue = _synthetic_catalogue()
    with (
        patch.object(import_public_content, "load_reviewed_catalogue", return_value=catalogue),
        patch.object(import_public_content, "REVIEWED_SLACK_PAGE", SLACK_PAGE),
    ):
        report = import_public_content.run(apply=True)
    # Re-running the seeder's input returns the replay receipt, which carries
    # no document count: the identical artifact already owns its release.
    return int(report.get("documents", 0))


def load_synced_wiki() -> int:
    """Publish the synthetic wiki the way the package engine does.

    The wiki pages are ``community_base.knowledge_base`` rows in the ``wiki``
    section, written by the ``dtc-podwiki`` parser (D7.1); the graph, the search
    corpus and the declared asset paths stay ``SyncedDocument`` singletons the
    catalogue reads. Production writes both by parsing the repository checkout;
    this seeds them from the synthetic catalogue's wiki records, so tests read
    the same content from the authority the pages actually use.
    """

    import hashlib

    from community_base.content_sync.models import ContentSource as EngineContentSource
    from community_base.knowledge_base import sync as knowledge_base_sync
    from community_base.knowledge_base.models import SECTION_WIKI

    from content.models import SyncedDocument

    catalogue = _synthetic_catalogue()
    source = EngineContentSource.objects.get_or_create(
        slug="dtc-podwiki",
        defaults={
            "repo_name": "DataTalksClub/podwiki",
            # A synthetic secret so the row satisfies the engine's own shape
            # rules; nothing here reads or keeps a real credential.
            "webhook_secret": "test-support-synthetic-secret",
        },
    )[0]

    def _checksum(record: dict[str, Any]) -> str:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    for record in catalogue["wiki"]:
        knowledge_base_sync.upsert_page(
            source,
            section=SECTION_WIKI,
            slug=record["slug"],
            title=record["title"],
            summary=record.get("summary", ""),
            public_path=record["public_path"],
            record=record,
            commit_sha=record["provenance"]["revision"],
            source_path=record["provenance"]["source_path"],
            checksum=_checksum(record),
        )
    singleton_records = {
        "wiki_graph": catalogue["wiki_graph"],
        "wiki_search": catalogue["wiki_search"],
        # The synced asset row carries the declared paths the staged manifest
        # published: the same mapping, one authority behind.
        "wiki_assets": {"wiki_assets": catalogue["manifest"]["wiki_assets"]},
    }
    SyncedDocument.objects.bulk_create(
        SyncedDocument(
            source=source,
            content_kind=kind,
            stable_key=kind,
            slug="",
            title=kind,
            summary="",
            public_path=f"/-/podwiki/{kind}",
            source_path=kind,
            checksum=_checksum(record),
            record=record,
        )
        for kind, record in singleton_records.items()
    )
    return len(catalogue["wiki"]) + len(singleton_records)


def _sync_engine_sources() -> tuple[object, object]:
    """The two engine sources the editorial catalogue and its media ride on."""

    from community_base.content_sync.models import ContentSource as EngineContentSource

    editorial_source = EngineContentSource.objects.get_or_create(
        slug="dtc-content",
        defaults={
            "repo_name": "DataTalksClub/content",
            # A synthetic secret so the row satisfies the engine's own shape
            # rules; nothing here reads or keeps a real credential.
            "webhook_secret": "test-support-synthetic-secret",
        },
    )[0]
    people_source = EngineContentSource.objects.get_or_create(
        slug="dtc-main-site",
        defaults={
            "repo_name": "DataTalksClub/datatalksclub.github.io",
            "webhook_secret": "test-support-synthetic-secret",
        },
    )[0]
    return editorial_source, people_source


def load_synced_editorial() -> int:
    """Publish the synthetic editorial records as synced rows, the way the engine does.

    The articles, podcasts, books and people read ``SyncedDocument`` rows --
    written by the ``dtc-content`` and ``dtc-main-site`` parsers (issue #384).
    Production writes these rows by parsing the repository checkout; this seeds
    the same rows from the synthetic catalogue. The staged records carry fields
    the projection build derived from *other* collections -- the profile
    credits, the public image address -- and the parser records do not, so the
    derived fields are stripped here and the declared image is stored under its
    source form, which is what the read model derives from.
    """

    from content.models import SyncedDocument

    catalogue = _synthetic_catalogue()
    editorial_source, people_source = _sync_engine_sources()

    kinds = {"articles": "article", "podcasts": "podcast", "books": "book"}
    rows = []
    for collection, kind in kinds.items():
        for record in catalogue[collection]:
            held = dict(record)
            image_source = held.pop("image_path", "")
            held.pop("image_source", None)
            held["image_source"] = image_source.lstrip("/")
            held.pop("author_profiles", None)
            held.pop("guest_profiles", None)
            held.pop("media_available", None)
            provenance = held.get("provenance") or {}
            rows.append(
                SyncedDocument(
                    source=editorial_source,
                    content_kind=kind,
                    stable_key=held["slug"],
                    slug=held["slug"],
                    title=held["title"],
                    summary=held.get("description") or held.get("summary") or "",
                    public_path=held["public_path"],
                    source_path=str(provenance.get("source_path") or held["slug"]),
                    checksum=str(provenance.get("checksum") or "0" * 64),
                    record=held,
                )
            )
    for record in catalogue["people"]:
        held = dict(record)
        image_source = held.pop("image_path", "")
        held.pop("image_source", None)
        held["image_source"] = image_source.lstrip("/")
        held.pop("media_available", None)
        held.pop("relationships", None)
        held.pop("roles", None)
        provenance = held.get("provenance") or {}
        rows.append(
            SyncedDocument(
                source=people_source,
                content_kind="people",
                stable_key=held["slug"],
                slug=held["slug"],
                title=held["title"],
                summary=held.get("summary") or "",
                public_path=held["public_path"],
                source_path=str(provenance.get("source_path") or held["slug"]),
                checksum=str(provenance.get("checksum") or "0" * 64),
                record=held,
            )
        )
    SyncedDocument.objects.bulk_create(rows)
    return len(rows)


def load_synced_media() -> int:
    """Publish the synthetic media records as synced rows, the way the parsers do.

    The media records are written by the ``dtc-content`` media parser (one per
    ``images/{posts,podcast,books}`` tree file) and the ``dtc-main-site``
    people parser (one per profile picture) -- issue #384.  The synthetic
    catalogue's media collection already holds the parser record shape -- key,
    public address, content type and the serving checksum -- so each record
    seeds the row of the source whose tree it stands for: profile pictures for
    the legacy main site, everything else for the editorial repository.
    """

    from content.models import SyncedDocument

    editorial_source, people_source = _sync_engine_sources()
    rows = []
    for record in _synthetic_catalogue()["media"]:
        held = dict(record)
        is_people_picture = held["record_key"].startswith("images/authors/")
        provenance = held.get("provenance") or {}
        rows.append(
            SyncedDocument(
                source=people_source if is_people_picture else editorial_source,
                content_kind="media",
                stable_key=held["record_key"],
                slug=held["record_key"],
                title=held["record_key"],
                summary="",
                public_path=held["public_path"],
                source_path=str(provenance.get("source_path") or held["record_key"]),
                checksum=str(provenance.get("checksum") or "0" * 64),
                record=held,
            )
        )
    SyncedDocument.objects.bulk_create(rows)
    return len(rows)


def load_synced_site_pages() -> int:
    """Publish the podcast platforms and the ``/slack`` page as synced rows.

    Both are single rows on the editorial source: the platform catalog and the
    Slack page are authored in the structured content repository now
    (``podcast-platforms.yaml`` and ``slack.yaml``), and the catalogue reads
    them like every other synced kind (issue #384).  The synthetic platform
    fixture keeps the reviewed record shape -- ``key``/``title`` included --
    because that is exactly what the parser derives from the authored file.
    """

    import hashlib
    import json

    from content.models import SyncedDocument

    def _checksum(record: dict[str, Any]) -> str:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    platforms = json.loads(PODCAST_PLATFORMS_SEED.read_text(encoding="utf-8"))
    page = json.loads(SLACK_PAGE.read_text(encoding="utf-8"))["page"]
    slack_record = {
        "public_path": "/slack",
        "title": page["title"],
        "lead": page["lead"],
        "channels": page["channels"],
        "troubleshooting_url": page["troubleshooting_url"],
    }
    editorial_source, _ = _sync_engine_sources()
    rows = [
        SyncedDocument(
            source=editorial_source,
            content_kind="podcast_platforms",
            stable_key="podcast_platforms",
            slug="",
            title="Podcast platforms",
            summary="The listening platforms the show publishes.",
            public_path="/-/content/podcast_platforms",
            source_path="podcast-platforms.yaml",
            checksum=_checksum({"platforms": platforms}),
            record={"platforms": platforms},
        ),
        SyncedDocument(
            source=editorial_source,
            content_kind="slack_page",
            stable_key="slack",
            slug="slack",
            title=slack_record["title"],
            summary=slack_record["lead"],
            public_path="/slack",
            source_path="slack.yaml",
            checksum=_checksum(slack_record),
            record=slack_record,
        ),
    ]
    SyncedDocument.objects.bulk_create(rows)
    return len(rows)


def load_synced_course_catalog() -> int:
    """Publish the synthetic course catalogue copies as synced rows.

    The catalogue copy is authored per course repository in its ``course.yaml``
    ``catalog:`` block (issue #384); the records here are what the parser
    would write for two repositories that declare editions. Two sources, so
    the merged collection and its cross-repository order are exercised the way
    a real database exercises them.
    """

    import hashlib
    import json

    from community_base.content_sync.models import ContentSource as EngineContentSource

    from content.models import SyncedDocument

    def _checksum(record: dict[str, Any]) -> str:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _edition(
        slug: str,
        repo_name: str,
        family_slug: str,
        year: int,
        title: str,
        *,
        finished: bool,
        homework_count: int,
        project_count: int,
        first_deadline: str,
        last_deadline: str,
    ) -> dict[str, Any]:
        revision = "0" * 40
        return {
            "slug": slug,
            "public_path": f"/courses/{family_slug}/{year}",
            "title": title,
            "finished": finished,
            "homework_count": homework_count,
            "project_count": project_count,
            "first_deadline": first_deadline,
            "last_deadline": last_deadline,
            "provenance": {
                "repository": repo_name,
                "revision": revision,
                "source_path": "course.yaml",
                "source_key": slug,
                # The parser records the source file's sha256; the synthetic
                # seed carries the same field with a synthetic digest.
                "checksum": hashlib.sha256(f"{repo_name}:{slug}".encode()).hexdigest(),
                "source_url": f"https://github.com/{repo_name}/blob/{revision}/course.yaml",
            },
        }

    editions = [
        (
            "de-zoomcamp",
            "DataTalksClub/data-engineering-zoomcamp",
            [
                _edition(
                    "de-zoomcamp-2026",
                    "DataTalksClub/data-engineering-zoomcamp",
                    "de-zoomcamp",
                    2026,
                    "Data Engineering Zoomcamp 2026",
                    finished=False,
                    homework_count=8,
                    project_count=3,
                    first_deadline="2026-01-26T23:59:59+00:00",
                    last_deadline="2026-05-04T23:00:00+00:00",
                ),
                _edition(
                    "de-zoomcamp-2025",
                    "DataTalksClub/data-engineering-zoomcamp",
                    "de-zoomcamp",
                    2025,
                    "Data Engineering Zoomcamp 2025",
                    finished=True,
                    homework_count=7,
                    project_count=3,
                    first_deadline="2025-02-03T23:59:59+00:00",
                    last_deadline="2025-06-02T23:59:59+00:00",
                ),
            ],
        ),
        (
            "ml-zoomcamp",
            "DataTalksClub/machine-learning-zoomcamp",
            [
                _edition(
                    "ml-zoomcamp-2025",
                    "DataTalksClub/machine-learning-zoomcamp",
                    "ml-zoomcamp",
                    2025,
                    "Machine Learning Zoomcamp 2025",
                    finished=True,
                    homework_count=9,
                    project_count=4,
                    first_deadline="2025-02-17T23:59:59+00:00",
                    last_deadline="2025-06-16T23:59:59+00:00",
                ),
                _edition(
                    "ml-zoomcamp-2024",
                    "DataTalksClub/machine-learning-zoomcamp",
                    "ml-zoomcamp",
                    2024,
                    "Machine Learning Zoomcamp 2024",
                    finished=True,
                    homework_count=11,
                    project_count=3,
                    first_deadline="2024-02-12T23:59:59+00:00",
                    last_deadline="2024-06-10T23:59:59+00:00",
                ),
            ],
        ),
    ]
    total = 0
    for slug, repo_name, records in editions:
        source = EngineContentSource.objects.get_or_create(
            slug=slug,
            defaults={
                "repo_name": repo_name,
                # A synthetic secret so the row satisfies the engine's own
                # shape rules; nothing here reads or keeps a real credential.
                "webhook_secret": "test-support-synthetic-secret",
            },
        )[0]
        rows = [
            SyncedDocument(
                source=source,
                content_kind="course",
                stable_key=record["slug"],
                slug=record["slug"],
                title=record["title"],
                summary="",
                public_path=record["public_path"],
                source_path="course.yaml",
                checksum=_checksum(record),
                record=record,
            )
            for record in records
        ]
        SyncedDocument.objects.bulk_create(rows)
        total += len(rows)
    return total


def load_homepage_testimonials() -> int:
    from courses.services.testimonials import import_homepage_testimonials

    return import_homepage_testimonials(HOMEPAGE_TESTIMONIALS).total


@transaction.atomic
def load_reviewed_reference_data() -> dict[str, int]:
    """Populate a freshly migrated database with the reference rows."""

    from community_base.events.models import Event

    if Event.objects.exists():
        return {"events": 0, "testimonials": 0}
    events = load_event_identities()
    return {
        "events": events,
        "event_content": load_event_content(),
        "docs": load_reviewed_docs(),
        "synced_docs": load_synced_docs(),
        "faq": load_reviewed_faq(),
        "synced_faq": load_synced_faq(),
        "public_content": load_reviewed_public_content(),
        "synced_wiki": load_synced_wiki(),
        "synced_editorial": load_synced_editorial(),
        "synced_media": load_synced_media(),
        "synced_site_pages": load_synced_site_pages(),
        "synced_course_catalog": load_synced_course_catalog(),
        "testimonials": load_homepage_testimonials(),
    }
