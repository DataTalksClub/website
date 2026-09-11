# Database-only public content

## Rule

Public website content is loaded from the database, with the homepage exception below.

The home landing page’s fixed presentation copy — including the hero, illustrated
learner journey, benefit blurbs and closing call to action — may live directly in
its templates. Its illustrations remain static design assets. This exception does
not cover database-owned course, event, testimonial or other published records
displayed on the homepage.

Runtime views, templates, services, startup checks, and deployment commands must
not load public content from:

- hardcoded Python records or editorial constants;
- checked-in JSON files;
- generated file-backed projections;
- a bundled snapshot or fallback used when the database is empty.

Importers may read external source material only as an ingestion step. They must
validate it and write it to database models. Public requests then read those
models, including on an empty database. Deployment runs migrations only and does
not seed content.

Operational configuration, schemas, migrations, test fixtures, and static design
assets are not public content, but they must not be used as a hidden public-content
fallback.

## Where the content lives now

Every public surface reads the database. The reviewed ingest input
`scripts/prod/*` reads no longer lives on disk in this repository: as of
2026-09-11 it moved to `~/prod/dtc-data/content-staging/` (an operator's local
data directory, backed up to a private S3 bucket -- see that directory's own
`README.md`), the same way `~/prod/dtc-data/eventbrite-content/staging/eventbrite_descriptions.json`
moved a little earlier the same day. `scripts/prod/*` is its main reader, and
`content/media_store.py`'s `media_records()` reads its media records for
operator tooling. Neither is a public request path, and CI cannot reach
either: nothing that runs in CI depends on this tree being present any more
(see below).

Two things that used to read this tree are gone outright, on the owner's
ruling that a one-time ingest-parity check has no business running as an
ongoing regression test: `ci/content_update.py` (the shape-check contract,
with its workflow and composite action) and
`content_sync/tests/test_dtc_content_accepted_checkout.py` (one frozen
historical commit's adapter-parity verification, env-gated and never run by
default anyway). `content_sync/dtc_content/parity.py` itself stays --
`content_sync/dtc_content/repository.py` still imports it unconditionally for
a real, ongoing production check gated to that same frozen commit.

`test_support/reference_data.py` no longer reads this tree either. It runs
the same real `scripts/prod/*` importers -- unmocked, for real -- over a
small, synthetic, git-tracked fixture set at
`test_support/fixtures/reference/` to fill every Django test database. The one
exception is the editorial catalogue (articles/podcasts/books/people/wiki/
courses/media): its real loader
(`scripts/prod/public_projection_source.load_checked_projection`) validates
its input against the exact accepted upstream revisions and a handful of
pinned counts from the real reviewed snapshot (issue #253) -- by design, so a
drifted or compromised upstream is refused rather than silently imported. A
synthetic catalogue cannot satisfy that pin and still be synthetic, so
`test_support/reference_data.py` calls `scripts/prod/import_public_content.run`
(the real, unmocked database-writing path) with only that one file-reading,
upstream-pinned loader swapped out. See that module's docstring for the full
reasoning.

| Surface | Read path | Ingest |
| --- | --- | --- |
| Articles, podcasts, books, people, wiki, courses, media, graph, search, routes | `content/catalogue.py` -> `ContentDocument` | `scripts/prod/import_public_content.py` |
| Documentation | `content/docs_projection.py` -> `ContentDocument`/`ContentAsset` | `scripts/prod/import_docs.py` |
| Course FAQ | `content/faq_data.py` -> `ContentDocument` | `scripts/prod/import_faq.py` |
| `/slack` | `content/review_views.py` -> `ContentDocument` | (page row) |
| Article FAQ sections | `content/article_faq.py` -> the article's own row | with the article |
| Events | `events/queries.py` -> `Event`/`EventContent` | `scripts/prod/import_events.py` (identity, then content) |
| Sponsors | `core/sponsors.py` -> `Sponsor` | `scripts/prod/import_sponsors.py` |
| Testimonials | `courses/services/testimonials.py` -> `Testimonial` | `scripts/prod/import_testimonials.py` |
| Featured cohort copy | `Cohort.delivery_format`/`promo_summary`/`CohortBuildItem` | course ingest |

An empty database is a normal state on every one of these: hubs render empty and
detail routes 404. Nothing falls back to a file.

`content/catalogue.py` is a function per kind, not a dictionary holding every
kind at once. The kinds share one module because they share the source, the
active release, the stored editorial order and the cache key that follows an
import. What a reader sees differently from what is stored -- an article or
profile body with its source's link metadata cleaned out, a profile pointed at
the live event routes, a wiki graph checked before it can be drawn -- is decided
there, beside the query that returns it.

The compatibility layer that reassembled these rows into the old projection
dictionary is gone: `content/public_data.py` no longer exists. Its event display
helpers are `content/event_content.py`, its route inventory is
`content/public_routes.py`, and its graph safety contract is
`content/public_graph.py`.

The projection *files* live outside this repository now, at
`~/prod/dtc-data/content-staging/`, as ingest input. `scripts/staging/` holds
the code that builds a staging artifact from a reviewed source
(`event_description_bridge.py`, `event_speaker_bio_normalization.py`,
`event_description_link_policy.py`, `luma_event_descriptions.py`); the code
that checks the frozen legacy build,
`scripts/prod/public_projection_source.py` (moved from the now-retired
`scripts/projection_build/` package -- it is a live dependency of
`scripts/prod/import_public_content.py`, so it moved rather than deleted).
Neither is on a public request path.

### Still to do

- Docs and FAQ images are still files in `content/docs_assets/` and
  `content/faq_assets/`; their records are database rows. Moving the bytes to the
  public media store is the media-objects program, not this one.
- The wiki's default social card is still a design asset on disk
  (`content/wiki_assets/`). The route asks the published manifest before serving
  it, so what is published is a database fact; only the bytes are a file.
- Once production is ingested (Stage 0 below), decide whether the
  now-external staging tree still needs to exist at all, or whether it can be
  deleted from `~/prod/dtc-data/content-staging/` the way earlier plans
  expected it to be deleted from this repository. `luma_event_descriptions.json`
  does not come out either way -- it is not one-time staging, see below.

## The staging tree is not a second source of truth

Three terms, used with these meanings here and in `_docs/runbooks/data-ingest.md`:
a **source** is where data is authored upstream, **staging** is
`~/prod/dtc-data/content-staging/` (outside this repository; it lived at
`temporary/content/` inside it until 2026-09-11) where a reviewed, processed
copy waits, and the **production database** is the target every public page
reads. Data moves one way, source → staging → database.

`~/prod/dtc-data/content-staging/` is a **staging layer**: the reviewed form of
each source, sitting between the original data and the database, and existing
for no other reason than to be pumped into it once. Some of it is a straight
capture; some of it was rewritten during review and exists in that form
nowhere else. Moving it out of this repository does not change what it is --
an operator's reviewed local data, backed up to S3, the same way
`~/prod/dtc-data/rds-export/`, `~/prod/dtc-data/mailchimp-export/`, and its
other existing subdirectories already are (see that directory's own
`README.md`).

`public_projection/events.json` is the clearest case. It was built offline
from the legacy site's `_data/events.yaml`, and then edited: the event
description bridge matched 159 events to their Luma descriptions, removed the
"about the speaker" biography and the platform boilerplate from each one, and
bound every surviving link to a reviewed destination
(`_docs/event-description-bridge.md`). Rebuilding it needs an exporter checkout
an operator holds privately. So it is not a cache of something we could re-derive
-- it is the reviewed content itself, and `scripts/prod/import_events.py` is what
it is for.

Three properties keep this from being a file-backed fallback in disguise:

- nothing on a public request path reads it, and no startup check touches it;
- the record *records* its legacy origin as provenance, and the importer re-checks
  that tuple against the identity row rather than trusting it;
- it is not part of this repository or its CI checkout at all -- it lives on an
  operator's machine, backed up to S3, and reaches the database only when an
  operator runs a `scripts/prod/*` importer by hand. It is scaffolding with a
  removal date, not an input the running site has.

## Target database reads

- General imported pages and assets: `ContentSource`, `ContentRelease`,
  `ContentDocument`, `ContentRelation`, `ContentAsset`, and `ActiveContentPath`.
- Events: `Event` for identity, `EventContent` with `EventSpeaker`
  and `EventLink` for what the page says. Nothing joins to JSON.
- Courses and curriculum: existing course-platform models. The shared current
  curriculum (added 2026-09-07 under #320) is part of this read path:
  `SharedCurriculum`, `SharedModule`, `SharedLesson`, `CohortSharedModule`, and
  `SharedLessonReadState` are what shared course pages read. Raw GitHub is an
  import input and an archive link only — a schema-2 archive cohort renders a
  notice plus its derived immutable GitHub URL, never imported lesson bodies.
  Current relative lesson images and code files are imported into managed storage
  with database rows (`SharedCurriculumAsset`); a shared lesson page resolves
  assets through that database row and the managed store, never a mutable GitHub
  branch URL and never a request-time fetch. Test fixtures under
  `content_sync/tests/fixtures/` are test data only and are never a runtime
  content fallback.
- Sponsors and testimonials: existing core/course database models.
- Navigation and other editable site records: database models such as
  `SiteNavigationEntry`.

The public read path should resolve only active, published database rows. Missing
rows return an empty collection or 404; they never trigger a filesystem fallback.

## Removal order -- superseded by relocation

This section originally planned a whole-directory *delete* of
`temporary/content/`, gated on production being ingested first (Stage 0) and
on repointing every non-ingest reader (Stage 1) and separating the live
staging code from the frozen build (Stage 2) before anything could come out
(Stage 3). The owner's instruction on 2026-09-11 superseded the mechanism, not
the technical facts it was built on: **relocate the tree out of the
repository and keep it, backed up to S3, rather than wait for a production
ingest that has not happened yet and then delete it.** Stages 0's precondition
(run the ingest first) no longer gates anything -- the tree is not going away,
so there is nothing left to lose by moving it now.

What Stages 1-2 actually required has happened, by relocation and one
targeted change per reader rather than by the sequence originally planned:

1. **`test_support/reference_data.py`** no longer reads the real tree at all
   (not "repointed at the new location" -- replaced). It runs the same real
   importers over a small synthetic fixture at
   `test_support/fixtures/reference/`, with one deliberate exception for the
   editorial catalogue's upstream-pinned validation (see "Where the content
   lives now" above and that module's docstring). The real corpus's exact
   shape (421 events, 2,203 content documents, and every test that had come to
   assert that exact shape rather than real application behavior) is gone from
   the ordinary test suite; git history holds it.
2. **`ci/content_update.py`** is deleted outright, with its workflow and
   composite action -- not retired-in-place or pointed at the database. The
   owner's ruling: an ingest-parity check has no business running as an
   ongoing regression test on every push.
3. **`content_sync/dtc_content/parity.py`** stays: `content_sync/dtc_content/repository.py`
   still imports it unconditionally for a real, ongoing check gated to the one
   frozen `ACCEPTED_CONTENT_COMMIT`. What came out was the one *test* that ran
   it as an ordinary suite member despite being env-gated and never run by
   default,
   `content_sync/tests/test_dtc_content_accepted_checkout.py`. Its digests
   being stale (issue #253) is unrelated to this move and still open.
4. **`content/media_store.py`'s `media_records()`** still reads the (now
   external) tree for operator tooling; the one CI-reachable test that had
   called it directly
   (`content/tests/test_public_media_store.py::MemoryMediaStoreTests`) carries
   its own small synthetic record set instead.

Stage 2's separation is also done: `event_description_bridge.py`,
`event_speaker_bio_normalization.py` and `event_description_link_policy.py`
moved from the retired `scripts/projection_build/` package to
`scripts/staging/`, where `scripts/staging/luma_event_descriptions.py`'s live
dependency on them is described alongside it. `public_projection_source.py`
moved to `scripts/prod/`, next to its one remaining caller,
`scripts/prod/import_public_content.py`; `scripts/projection_build/__init__.py`
and the now-empty package were deleted.

**What Stage 3 said to delete did not come out** -- `scripts/repin_projection_digests.py`
and `scripts/build_public_projection.py` still exist, just repointed at the new
location, because deleting them was never asked for by this relocation and
both still have real (if narrow) callers: the former by part of
`scripts/tests/test_public_projection_media_digest.py`, the latter by the
four tests named in the original Stage 3.2 entry
(`content/tests/test_public_projection_builder.py`, `test_podcast_platforms.py`,
`test_sponsor_article_charts.py`, `test_review_skeleton.py`). Their real
callers and the rules only `build_public_projection.py` holds (the `_people`
front-matter allowlist, the podwiki graph's node and link counts, the article
block builder's segment rules) are exactly why the original Stage 3 gated
deleting them on those tests no longer needing them -- that gate is unchanged
by the relocation, and nothing here removes it. Deleting them, if ever wanted,
is a decision for whoever decides the staging tree itself should finally be
deleted (Stage 4 below) -- not a step this relocation took.

`temporary/content/public_projection/` and the reviewed JSON files beside it
are gone from this repository (moved, `git rm`'d), including
`luma_event_descriptions.json`'s new home
(`~/prod/dtc-data/content-staging/luma_event_descriptions.json`) -- it is
still not one-time staging, still grows with every Luma export processed, and
now lives beside its siblings rather than apart from them.

### Stage 4 -- delete the staging tree, if and when it is safe to

This is what the original "Removal order" called Stage 3.4/4 for the files,
now retargeted at `~/prod/dtc-data/content-staging/` instead of a path inside
this repository. Nothing here has changed the actual precondition: production
still has to be ingested first (`temporary/content/`, now
`~/prod/dtc-data/content-staging/`, is the reviewed content itself and is not
re-derivable from a checkout anyone holds), and the two things that do not
come out even then are `luma_event_descriptions.json` and
`public_projection_source.py`'s callers -- see above.

The names left over once a projection is not a file anywhere -- `content/docs_projection.py`,
`content/media_store.py`'s `PROJECTION_ROOT`/`REVIEWED_PROJECTION_ROOT`, and
`BRIDGE_PUBLIC_PATH`'s stale literal -- are unchanged by this relocation and
still wait on the same things the earlier "Stage 4" entry described (now
folded into this one rather than kept as a separate numbered stage, since
there is no longer a Stage 3 delete step ahead of it to number against).

### Verification

A fresh migrated database renders empty hubs and 404s for absent detail records;
an ingested database renders those records with no source file in the image.
Both already hold and must keep holding after every step above. The ordinary
test suite (`uv run python manage.py test`) no longer requires
`~/prod/dtc-data/` to exist at all; verify that stays true after any change to
`test_support/reference_data.py` or the fixtures under
`test_support/fixtures/reference/`.

Useful audit commands:

```text
rg -n "public_projection|docs_projection|faq_projection|event_identity_manifest|event_description_bridge" .
rg -n "read_text|read_bytes|json\.load" content core events
rg -n "prod/dtc-data" scripts content test_support
```
