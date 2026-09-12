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


def load_event_identities() -> int:
    """Insert the synthetic event identity manifest exactly as it is checked in."""

    from events.models import Event, ensure_public_id_sequence
    from scripts.prod.identity_manifest import load_identity_manifest

    manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    events = [
        Event(
            id=item.id,
            public_id=item.public_id,
            title=item.title,
            slug=item.slug,
            source_repository=item.source.repository,
            source_revision=item.source.revision,
            source_key=item.source.source_key,
            source_path=item.source_path,
            source_checksum=item.source_checksum,
        )
        for item in manifest.events
    ]
    Event.objects.bulk_create(events)
    ensure_public_id_sequence()
    return len(events)


def load_event_content() -> int:
    """Attach the synthetic event content, the way the production import does.

    Identity alone publishes nothing: :func:`events.queries.published_event_records`
    reads ``EventContent``, so without this a test database holds addressable
    events and no event pages.
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


def load_reviewed_faq() -> int:
    """Publish the synthetic course FAQ, the way the production import does."""

    from scripts.prod.import_faq import run

    return int(run(path=FAQ_PROJECTION, apply=True)["courses"])


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


def load_homepage_testimonials() -> int:
    from courses.services.testimonials import import_homepage_testimonials

    return import_homepage_testimonials(HOMEPAGE_TESTIMONIALS).total


@transaction.atomic
def load_reviewed_reference_data() -> dict[str, int]:
    """Populate a freshly migrated database with the reference rows."""

    from events.models import Event

    if Event.objects.exists():
        return {"events": 0, "testimonials": 0}
    events = load_event_identities()
    return {
        "events": events,
        "event_content": load_event_content(),
        "docs": load_reviewed_docs(),
        "faq": load_reviewed_faq(),
        "public_content": load_reviewed_public_content(),
        "testimonials": load_homepage_testimonials(),
    }
