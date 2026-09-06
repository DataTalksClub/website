# Database-only public content

## Rule

All public website content is loaded from the database.

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

Every public surface reads the database. What is left on disk under
`temporary/content/` is ingest input. `scripts/prod/*` is its main reader, but
not its only one -- `ci/content_update.py` shape-checks it,
`content_sync/dtc_content/parity.py` compares against it,
`content/media_store.py`'s `media_records()` reads its media records for
operator tooling, and `test_support/reference_data.py` runs the importers over
it to fill every Django test database. None of those is a public request path.

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

The projection *files* are still checked in as that ingest input, and
`scripts/projection_build/` holds the code that checks and builds them. Neither
is on a public request path. "Removal order" below is what is left of them and
what each part waits on.

### Still to do

- Docs and FAQ images are still files in `content/docs_assets/` and
  `content/faq_assets/`; their records are database rows. Moving the bytes to the
  public media store is the media-objects program, not this one.
- The wiki's default social card is still a design asset on disk
  (`content/wiki_assets/`). The route asks the published manifest before serving
  it, so what is published is a database fact; only the bytes are a file.
- Delete what is left of the projection, in the order below. None of it can
  start before production is ingested, and two of the things people expect to
  delete -- `temporary/content/luma_event_descriptions.json` and three of the
  four `scripts/projection_build/` modules -- do not come out at all.

## The staging tree is not a second source of truth

Three terms, used with these meanings here and in `_docs/runbooks/data-ingest.md`:
a **source** is where data is authored upstream, **staging** is `temporary/content/`
where a reviewed, processed copy waits, and the **production database** is the
target every public page reads. Data moves one way, source → staging → database.


`temporary/content/` is a **staging layer**: the reviewed form of each source,
sitting between the original data and the database, and existing for no other
reason than to be pumped into it once. Some of it is a straight capture; some of
it was rewritten during review and exists in that form nowhere else.

`temporary/content/public_projection/events.json` is the clearest case. It was
built offline from the legacy site's `_data/events.yaml`, and then edited: the
event description bridge matched 159 events to their Luma descriptions, removed
the "about the speaker" biography and the platform boilerplate from each one, and
bound every surviving link to a reviewed destination
(`_docs/event-description-bridge.md`). Rebuilding it needs an exporter checkout
an operator holds privately. So it is not a cache of something we could re-derive
-- it is the reviewed content itself, and `scripts/prod/import_events.py` is what
it is for.

Three properties keep this from being a file-backed fallback in disguise:

- nothing on a public request path reads it, and no startup check touches it;
- the record *records* its legacy origin as provenance, and the importer re-checks
  that tuple against the identity row rather than trusting it;
- once production is ingested, the tree is deleted. It is scaffolding with a
  removal date, not an input the running site has.

## Target database reads

- General imported pages and assets: `ContentSource`, `ContentRelease`,
  `ContentDocument`, `ContentRelation`, `ContentAsset`, and `ActiveContentPath`.
- Events: `Event` and `EventAlias` for identity, `EventContent` with `EventSpeaker`
  and `EventLink` for what the page says. Nothing joins to JSON.
- Courses and curriculum: existing course-platform models.
- Sponsors and testimonials: existing core/course database models.
- Navigation and other editable site records: database models such as
  `SiteNavigationEntry`.

The public read path should resolve only active, published database rows. Missing
rows return an empty collection or 404; they never trigger a filesystem fallback.

## Removal order

Steps 1-3 are done: every public surface has a database query service with
empty-state tests, every view reads it, and the deployment seeds, runtime file
readers and startup digest checks are gone. What follows is the rest, stated as
what remains, what each item blocks on, and the order it comes out in.

**Two corrections to the instruction "delete `temporary/content/` and
`scripts/projection_build/`".** Neither is a whole-directory delete.

- `temporary/content/luma_event_descriptions.json` is *not* one-time staging. It
  is written by `scripts/staging/luma_event_descriptions.py` for events found in
  a Luma export, and it grows each time one is processed. It outlives the
  production ingest.
- Three of the four modules in `scripts/projection_build/` outlive it too.
  `scripts/staging/luma_event_descriptions.py` imports `LINK_POLICY_VERSION` and
  `MARKDOWN_POLICY_VERSION` from `event_description_bridge.py`,
  `normalize_description_html` and `NORMALIZATION_SCHEMA_VERSION` from
  `event_speaker_bio_normalization.py`, and the reviewed destinations in
  `event_description_link_policy.py` are what an unreviewed link is refused
  against. It also imports `BridgeBuildError`, `MARKDOWN` and the renderer from
  `scripts/build_event_description_bridge.py`, so that "offline rebuilder for a
  frozen artifact" is a live dependency of the ongoing staging path. Only
  `public_projection_source.py` is tied to the frozen tree.

### Stage 0 -- run the ingest

Run the `scripts/prod/` importers against the production database and record the
counts. Nothing below moves until this has happened; `temporary/content/` is the
reviewed content itself and is not re-derivable from a checkout we hold.

### Stage 1 -- repoint the readers that are not the ingest

Each of these reads `temporary/content/` for a reason other than filling
production, so running the ingest does not retire it.

1. **`test_support/reference_data.py`** runs the real importers over the staging
   tree to populate *every* Django test database -- 421 events, 1,684 aliases and
   2,203 content documents, plus docs, FAQ and testimonials. This is the largest
   blocker and it is not an ingest question: deleting the tree without replacing
   this deletes the test corpus.
2. **`ci/content_update.py`** shape-checks the staging artifacts per family and
   is what `make content-update-check` and `.github/workflows/content-update.yml`
   run. Retire the contract or point it at the database.
3. **`content_sync/dtc_content/parity.py`** compares an adapter bundle against
   the checked tree at one frozen commit, with its digests pinned in
   `content_sync/dtc_content/contract.py` (`PROJECTION_MANIFEST_SHA256`,
   `PROJECTION_TREE_SHA256`). Those digests are already stale -- issue #253.
4. **`content/media_store.py`'s `media_records()`** reads
   `temporary/content/public_projection/media.json` for operator tooling and the
   offline fixture store. The `/images/...` view resolves its record from the
   database and does not call it.

### Stage 2 -- separate the live staging code from the frozen build

Move the Markdown renderer, the link policy and the description normaliser named
in the second correction above under `scripts/staging/`, where the one-way
`source -> staging -> database` trip they serve is described. Until that is done,
the frozen legacy build and the ongoing Luma staging path cannot be deleted
independently, because they are the same code.

### Stage 3 -- delete, in this order

1. `scripts/repin_projection_digests.py`, with the part of
   `scripts/tests/test_public_projection_media_digest.py` that drives it. Nothing
   else calls it; it exists only to move the manifest's digest scope without the
   full rebuild issue #253 says is impossible.
2. `scripts/build_public_projection.py`, once `content_sync/dtc_content/parity.py`
   and the four tests that import it
   (`content/tests/test_public_projection_builder.py`, `test_podcast_platforms.py`,
   `test_sponsor_article_charts.py`, `test_review_skeleton.py`) no longer do.
   **It is the sole holder of rules nothing else asserts** -- the `_people`
   front-matter allowlist, the podwiki graph's node and link counts, the article
   block builder's segment rules. Those move into the ingest or the content
   adapter first, or they are lost silently.
3. `scripts/projection_build/public_projection_source.py`, once
   `scripts/prod/import_public_content.py` has run for the last time and
   `ci/content_update.py` no longer imports `load_checked_projection` or
   `EXPECTED_REVISIONS` from it.
4. `temporary/content/public_projection/` and the reviewed JSON files beside it,
   **except `luma_event_descriptions.json`**.
5. `scripts/projection_build/__init__.py`, once the three surviving modules have
   moved to `scripts/staging/` and the package is empty.

### Stage 4 -- the names that are left

After stage 3 nothing called a projection is a file, so each of these is a
rename, and each has a cost worth checking before paying it.

- **`content/docs_projection.py`** and its exported `docs_projection()` read
  `ContentDocument` and `ContentAsset`. Renaming them reaches outside `content/`:
  `playwright_tests/test_foundation_smoke.py`, `test_accessibility.py` and
  `test_docs_navigation.py` import the symbol, `ci/content_update.py` imports
  `DOCS_SOURCE_REVISION`, and `.github/workflows/content-update.yml` names the
  test module `content.tests.test_docs_projection` by path.
- **`content/media_store.py`'s `PROJECTION_ROOT`** is the *local hydration
  target* an operator fills, not the staging tree; it is deliberately a directory
  that does not exist in a fresh checkout. It outlives the projection entirely
  and wants a name that says "local media root".
  `REVIEWED_PROJECTION_ROOT` beside it dies with stage 3.4.
- **`BRIDGE_PUBLIC_PATH`** in `scripts/projection_build/event_description_bridge.py`
  is `"content/event_description_bridge.json"`, a path that moved to
  `temporary/content/`. It cannot be corrected on its own: the same literal is
  pinned inside the checked manifest at
  `temporary/content/public_projection/manifest.json` as the
  `event_description_bridge` binding, the importer compares the two, and
  `scripts/repin_projection_digests.py` recomputes only digest and scope fields.
  Correcting it means rewriting a checked binding, which only a full rebuild
  does -- and that is issue #253.

### Verification for each stage

A fresh migrated database renders empty hubs and 404s for absent detail records;
an ingested database renders those records with no source file in the image.
Both already hold and must keep holding after every step above.

Useful audit commands:

```text
rg -n "public_projection|docs_projection|faq_projection|event_identity_manifest|event_description_bridge" .
rg -n "read_text|read_bytes|json\.load" content core events
```
