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
| Articles, podcasts, books, people, wiki, courses, media, graph, search, routes | `content/public_data.py` -> `ContentDocument` | `scripts/prod/import_public_content.py` |
| Documentation | `content/docs_projection.py` -> `ContentDocument`/`ContentAsset` | `scripts/prod/import_docs.py` |
| Course FAQ | `content/faq_data.py` -> `ContentDocument` | `scripts/prod/import_faq.py` |
| `/slack` | `content/review_views.py` -> `ContentDocument` | (page row) |
| Article FAQ sections | `content/article_faq.py` -> the article's own row | with the article |
| Events | `events/queries.py` -> `Event`/`EventContent` | identity: `scripts/prod/import_events.py`; content: source decision pending |
| Sponsors | `core/sponsors.py` -> `Sponsor` | `scripts/prod/import_sponsors.py` |
| Testimonials | `courses/services/testimonials.py` -> `Testimonial` | `scripts/prod/import_testimonials.py` |
| Featured cohort copy | `Cohort.delivery_format`/`promo_summary`/`CohortBuildItem` | course ingest |

An empty database is a normal state on every one of these: hubs render empty and
detail routes 404. Nothing falls back to a file.

The projection *files* are still checked in as that ingest input, and
`scripts/projection_build/` holds the code that checks and builds them. Neither
is on a public request path. Removing them is the last step, once the ingests
have run against the production database.

### Still to do

- Event content has no importer: `scripts/prod/import_events.py` records that
  its only current source is the legacy repository being retired, and that the
  replacement source is undecided. Until then `EventContent` stays empty and the
  event pages render empty.
- Docs and FAQ images are still files in `content/docs_assets/` and
  `content/faq_assets/`; their records are database rows. Moving the bytes to the
  public media store is the media-objects program, not this one.
- Delete `temporary/content/` and `scripts/projection_build/` once production is
  ingested.

## Target database reads

- General imported pages and assets: `ContentSource`, `ContentRelease`,
  `ContentDocument`, `ContentRelation`, `ContentAsset`, and `ActiveContentPath`.
- Events and aliases: `Event` and `EventAlias`; add database fields/models for
  schedule, description, speakers, links, and media instead of joining to JSON.
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
