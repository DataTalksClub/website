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

## Current violations to remove

### Main public projection

The largest violation is the public projection (currently parked as a
migration-only helper at `temporary/content/public_projection/`, loaded by
`content/public_data.py`). It currently bundles articles, books, courses, events,
media metadata, people, podcasts/transcripts, wiki pages, search data, graph data,
editorial route aliases, podcast platform links, and a wiki image.

Runtime consumers include:

- `core/views.py` and `core/home_content.py`;
- `content/public_views.py`, `content/wiki_content.py`,
  `content/person_content.py`, `content/person_chip.py`, and
  `content/podcast_content.py`;
- `events/identity.py` and event services that resolve a database Event back to a
  projected record;
- sitemap and media-serving paths;
- the `content.E002` startup check in `content/apps.py`.

Remove the projection directory, loader/validator, startup check, file-backed
media inventory, projection builders, repinning tools, parity code, and tests that
assert the bundled inventory. Replace every runtime query with database models and
database query services. Empty querysets must be normal page states.

### Other checked-in content stores

These are separate file-backed public-content sources and must also be removed or
migrated into database models before their runtime readers are deleted:

| Data | Checked-in source | Runtime reader |
| --- | --- | --- |
| Documentation | `content/docs_projection.json` | `content/docs_projection.py` |
| Course FAQ | `content/faq_projection.json` | `content/faq_data.py` |
| Article FAQ | `content/article_faq.json` | `content/article_faq.py` |
| Sponsor directory | `core/sponsor_directory.json` | `core/sponsors.py` |
| Event descriptions | `temporary/content/event_description_bridge.json` (migration helper) | `content/event_description_bridge.py` |
| Event identity seed | `temporary/content/event_identity_manifest.json` (migration helper) | `events/identity.py` |
| Event normalization evidence used at runtime | `_docs/migrations/event-speaker-bio-normalization.json` | `content/event_speaker_bio_normalization.py` |

Audit hardcoded public copy and inventories in `core/home_content.py`, navigation
defaults, sponsor history, podcast platform metadata, wiki categories, sitemap
inventories, and route-specific featured records. Presentation labels may remain
code-owned; publishable records and editorial facts must be database fields.

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
rg -n "public_projection|docs_projection|faq_projection|article_faq|sponsor_directory|event_identity_manifest|event_description_bridge" .
rg -n "read_text|read_bytes|json\.load" content core events
```
