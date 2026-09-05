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
`temporary/content/` is one-time ingest input, read only by `scripts/prod/*`:

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
is on a public request path. Removing them is the last step, once the ingests
have run against the production database.

### Still to do

- Docs and FAQ images are still files in `content/docs_assets/` and
  `content/faq_assets/`; their records are database rows. Moving the bytes to the
  public media store is the media-objects program, not this one.
- The wiki's default social card is still a design asset on disk
  (`content/wiki_assets/`). The route asks the published manifest before serving
  it, so what is published is a database fact; only the bytes are a file.
- Delete `temporary/content/` and `scripts/projection_build/` once production is
  ingested.

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

1. Add database query services and empty-state tests for each public surface.
2. Switch views, homepage, detail resolution, media, search, graph, feeds, and
   sitemaps to those database services.
3. Remove deployment-time seeds and all runtime file readers/startup checks.
4. Delete checked-in content JSON, projection assets, builders, parity code, and
   obsolete projection tests.
5. Verify a fresh migrated database renders empty hubs and 404s for absent detail
   records, then verify imported database records render without source files in
   the image.

Useful audit commands:

```text
rg -n "public_projection|docs_projection|faq_projection|event_identity_manifest|event_description_bridge" .
rg -n "read_text|read_bytes|json\.load" content core events
```
