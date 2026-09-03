# Ingest script inventory

Every data source, and the full journey its data takes — not just the last
script that writes to the database, but every stage before it: what pulls raw
data locally, what cleans or repackages it, what actually writes to prod. Where
a source has more than one journey (a real pipeline plus a local-dev-only
shortcut, for instance), each is its own numbered entry so it's never confused
with the real path.

Every script path below is a link to the actual file. Companion to
[`data-ingest.md`](data-ingest.md) (narrative deep-dive) and issue #310
(tracking checklist) — this is the at-a-glance map. Some sources have no
script yet; those are listed too, so the gap is visible rather than silent.

Verified against the real code and a real end-to-end dry run on 2026-09-03.

---

# 1. Course repositories

Three real stages, run in this order for the offline path; the webhook path
collapses 1.2 into itself (it fetches the commit archive directly, no checkout
needed).

## 1.1 Register sources

`manage.py seed_course_repository_sources` (`make content-sources`) — command
defined in [`content_sync/management/commands/seed_course_repository_sources.py`](../../content_sync/management/commands/seed_course_repository_sources.py)

Source: [`content_sync/course_repository_sources.json`](../../content_sync/course_repository_sources.json),
a pinned, checked-in registration input — not a list in code.
Transform: creates or leaves alone one `ContentSource` row per registered
repository. Idempotent.
Destination: `content_sync.ContentSource`.

## 1.2 Checkout

`make content-checkouts` (wraps `manage.py pull_course_repositories
--checkout-plan` and `git clone`/`fetch` per source) — Makefile target, and
[`content_sync/management/commands/pull_course_repositories.py`](../../content_sync/management/commands/pull_course_repositories.py)

Source: GitHub, live network — the only network step in this journey.
Transform: clones a fresh repository or fast-forwards an existing one to the
registered branch.
Destination: a local git checkout per source, on disk only — not the database.

## 1.3 Ingest

[`content_sync/course_repository_ingest.py`](../../content_sync/course_repository_ingest.py),
driven by either the signed GitHub push webhook
([`content_sync/course_repository_webhook.py`](../../content_sync/course_repository_webhook.py))
or `manage.py pull_course_repositories --from-disk <checkout-root>`

Source: the checkout from 1.2 (pull path), or a webhook push payload's commit
archive fetched directly (webhook path — skips 1.2).
Transform: parses `course.yaml`, modules, units, homework, projects. Snapshot
transport and draft-detection are shared via
[`content_sync/snapshot.py`](../../content_sync/snapshot.py) /
[`content_sync/drafts.py`](../../content_sync/drafts.py), not reimplemented
per source.
Destination: [`courses/models/`](../../courses/models) (`Cohort`, `Module`,
`Unit`, `Homework`, `Project`).

---

# 2. Pre-2023 Zoomcamp history

Single stage — the raw source is a git repository maintained outside this
project, not a file that needs preparing first.

## 2.1 Import

[`scripts/prod/import_legacy_zoomcamp.py`](../../scripts/prod/import_legacy_zoomcamp.py)
(+ [`scripts/prod/legacy_zoomcamp/`](../../scripts/prod/legacy_zoomcamp))

Source: a local checkout of the `zoomcamp-scoring` repository (e.g.
`~/git/zoomcamp-scoring`, outside this repository), one edition at a time.
Transform: parses homework/project scoring and certificate data per edition.
Destination: `accounts_customuser` (password left unusable), `Enrollment`,
`Submission`, `ProjectSubmission`.

Notes: the only importer that bootstraps an entirely empty database. Verified
today against all 7 real editions — 1,207s total wall time, idempotent on
replay.

---

# 3. CMP course content

Three distinct journeys under this source — only the first reaches prod.

## 3.1 Import (production)

[`scripts/prod/import_cmp_content.py`](../../scripts/prod/import_cmp_content.py)
(+ [`courses/services/cmp_content_import.py`](../../courses/services/cmp_content_import.py))

Source: the CMP production export, read in place. **Precise path, not a
directory**: `/data/tmp/rds-export/cmp/rds-prod-<YYYYMMDD>-<HHMMSS>.db` — a
new file lands here daily (e.g. `rds-prod-20260903-182132.db`, the newest as
of this writing). There is **no "latest" symlink**; `--source` is a required
argument (`scripts/prod/import_cmp_content.py:47`) and the operator picks the
newest file by the date in its name. Older snapshots also exist directly
under `/data/tmp/rds-export/` without the `cmp/` subdirectory
(`rds-prod-20260902-012536.db` and earlier) — the `cmp/` subdirectory is the
current location; confirm which is authoritative before using an old path
verbatim from an earlier report.
Transform, exactly:

**Copied verbatim from CMP, no rewriting:** homework slug (including on
modules-format cohorts whose own repository declares a different one — CMP
wins outright), homework/question/project/criteria title and body text,
registration campaign copy and dates.
**Never derived from CMP, always read from the reviewed catalogue instead:**
a cohort's family and title. `COURSE_FAMILY_TITLES` and
`COHORT_FAMILY_IDENTITIES` in
[`courses/course_family_catalog.py`](../../courses/course_family_catalog.py)
are the only source for these — CMP's own family grouping is never trusted or
invented from, because doing that once already split the `ai-dev-tools`
family and needed migration `0052` to repair.
**Preserved from the repository side when a homework pairs against CMP's:**
`source_content_id`, the imported instructions Markdown, source path, units,
and module binding. The row is *renamed*, never replaced — only its slug and
CMP's own fields change. Replacing it outright previously caused the
course-repository path to refuse on its next pull with
`homework_slug_collision`.
**Replaced as a whole set, not merged field-by-field:** a homework's
questions. **Never touched:** any account, enrollment, submission, answer,
review, or learner registration row — this importer is content-only by
design.
Destination: `courses.models`.

Notes: now bootstraps its own reviewed families/cohorts rather than depending
on a separate seed running first. Open: `llm-zoomcamp-2026` has 2 unreconciled
repository homework rows (`homework-06`, `homework-07`).

## 3.2 Local development seed — not a path to prod

[`courses/services/local_course_seed.py`](../../courses/services/local_course_seed.py),
reading [`scripts/production_like_course_specs.json`](../../scripts/production_like_course_specs.json)

Source: a frozen, checksum-pinned JSON file carrying CMP's *shape* with
invented copy — not a live database.
Transform: none; direct write.
Destination: `courses.models`, and only on a LOCAL/TEST SQLite database — it
refuses to run anywhere else.

Notes: exists purely so a developer without CMP access still sees a realistic
catalogue locally. Listed here specifically so it's never mistaken for 3.1.

## 3.3 Local staging bulk copy — dev rehearsal only

[`courses/services/local_cmp_content_import.py`](../../courses/services/local_cmp_content_import.py)

Source: a full CMP database snapshot, staged under this project's `.tmp/`
before reading.
Transform: bulk copy into an empty local catalogue (cannot reconcile against
an already-populated one — that's what 3.1's importer is for).
Destination: a local dev database only.

---

# 4. CMP learner accounts

## 4.1 Import

[`scripts/prod/import_cmp_learners.py`](../../scripts/prod/import_cmp_learners.py)
(+ [`accounts/services/cmp_learner_import.py`](../../accounts/services/cmp_learner_import.py))

Source: the same CMP production export as 3.1 — same precise path convention,
`/data/tmp/rds-export/cmp/rds-prod-<YYYYMMDD>-<HHMMSS>.db`, read in place,
required `--source` argument, no default.
Transform, exactly:

**Copied verbatim from the export's `email` column:** the address itself —
never rewritten, never case-folded (`CustomUser.save()` computes
`normalized_email` on its own from whatever arrives).
**Explicitly not copied, deliberately reset regardless of what the export
holds:** the password — `set_unusable_password()` is called unconditionally,
no password hash ever travels. `is_staff` and `is_superuser` — always written
`False`, even though the export carries five superuser/staff rows; copying
that column together with a usable password hash is the one combination that
would grant production administrator rights by import. Staff access is
granted afterward, through Studio, to named people, never by this importer.
**Never created:** a `SocialAccount` row — OAuth linking happens at sign-in
time through `ConsolidatingSocialAccountAdapter`, never at import time.
**Left at the model default, not derived from the export:** `identity_state`,
which stays `legacy`.
Six tables are explicitly never read at all: `django_session`,
`socialaccount_socialaccount`, `socialaccount_socialapp`,
`socialaccount_socialapp_sites`, `socialaccount_socialtoken`,
`accounts_token`. Deduplicates against source #2's rows by
`normalized_email`. Resumable via a persisted per-table high-water mark
([`accounts/migrations/0002_cmp_learner_import_progress.py`](../../accounts/migrations/0002_cmp_learner_import_progress.py)) —
proven with a real `kill -9` mid-run and clean resume.
Destination: `accounts_customuser`, `account_emailaddress`.

## 4.2 Learner activity — not built

No script. Enrollments, submissions, answers, registrations, criteria
responses, peer reviews, project evaluation scores, wrapped statistics —
roughly 470,000 of the export's ~510,000 learner rows. **Largest open gap in
this entire inventory.**

---

# 5. Event identity manifest

## 5.1 Import

[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py) (also
`manage.py import_event_identities`)

Source: the checked-in, human-reviewed
[`events/event_identity_manifest.json`](../../events/event_identity_manifest.json)
(421 events, 1,684 aliases).
Transform: allocates `public_id` via `EventPublicIdSequence`; writes aliases.
Destination: [`events/models.py`](../../events/models.py) (`Event`,
`EventAlias`).

## 5.2 New-event identity creation — in flight

No dedicated stage yet. `create_event_identity()` in
[`events/identity.py`](../../events/identity.py) exists and works but has zero
callers anywhere — a fresh Luma/Eventbrite export's new events currently have
no automatic path into the manifest at all. Being wired into this journey
now: detect an event with no existing identity, call `create_event_identity()`
directly (not the reviewed-manifest path), report what was created.
Registration-count activation (6.3) stays a separate, human-gated step
regardless.

---

# 6. Luma + Eventbrite registration aggregates

Three stages: clean the raw protected export, derive aggregates from it,
activate a mapping for public display. The middle stage is where "attendee
data exists but is deliberately not written to the database" — see 9 below
for the plan to change that.

## 6.1 Prepare

[`scripts/prepare_event_registration_sources.py`](../../scripts/prepare_event_registration_sources.py)
(`prepare_luma`, `prepare_eventbrite`)

Source: a raw Luma export (paired CSV + JSON checkpoint per event) and/or a
raw Eventbrite zip archive, both owner-provided, passed explicitly via
`--luma-source`/`--eventbrite-source` — no default input location.
Transform: validates schema (required columns present, checkpoint well-formed),
repackages into a normalized layout, computes a tree/file checksum. Never
prints event names, provider identifiers, or attendee fields — enforced by
the script's own docstring contract.
Destination: `.local/migration-data/events/{luma-aggregate-v1, <eventbrite
archive>}` by default — a cleaned, gitignored, still-protected intermediate.
Attendee-level data survives this stage; it's validated and repackaged, not
aggregated yet.

## 6.2 Derive aggregates

[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py), via
[`events/importers.py`](../../events/importers.py) (`derive_luma`,
`derive_eventbrite`)

Source: the prepared intermediate from 6.1. Default path
`.local/migration-data/events/luma-aggregate-v1`
(`LUMA_RELATIVE_SOURCE` in `import_events.py:86`) — **currently missing from
this worktree** despite working earlier today; unexplained, flagged, not yet
chased down. A cached copy from an earlier run exists at
`.tmp/luma-prepared-20260831/luma-aggregate-v1/` (dated Aug 31, so itself
already behind — see 6.3's note on the September gap).
Transform: aggregates to counts only — the module's own docstring states "no
attendee value crosses this module boundary" — and proposes canonical event
mappings.
Destination: `HistoricalRegistrationAggregateRevision`,
`HistoricalRegistrationSourceRun`.

## 6.3 Activate

No script — a human reviews a proposed mapping and flips it from
`mapping_review_required` to active.

Notes: only 3 of 421 provider mappings are activated today; the other 380
render no public count. Confirmed today: the newest available local snapshot
of Luma data only covers events through August 2026, so four real events on
Luma dated September 8–15, 2026 aren't in this pipeline at all yet — not a
bug, just the gap between a periodic export and Luma's live state.

---

# 7. Testimonials

Single stage.

## 7.1 Import

[`scripts/prod/import_testimonials.py`](../../scripts/prod/import_testimonials.py)

Source: [`courses/homepage_testimonials.json`](../../courses/homepage_testimonials.json),
reviewed and checked in.
Transform: none beyond validation; direct write, replay-safe.
Destination: `Testimonial` (homepage and per-course placement).

---

# 8. Content — wiki, podcast, articles, people, books

Two entirely separate journeys exist for the same upstream repository: the
database pipeline (built, not yet serving) and what actually serves the site
today (a static build, not a database importer at all).

## 8.1 Database sync — built, deferred

[`scripts/prod/sync_content.py`](../../scripts/prod/sync_content.py) (+
[`content_sync/dtc_content/`](../../content_sync/dtc_content))

Source: `DataTalksClub/content` on GitHub.
Transform: parity-diffed per route against the checked projection
([`content_sync/dtc_content/parity.py`](../../content_sync/dtc_content/parity.py))
before any cutover.
Destination: [`content/models.py`](../../content/models.py) (`ContentSource`,
`ContentRelease`, `ContentDocument`, `ContentRelation`, `ContentAsset`).

Notes: confirmed safe to run in parallel with what's live — its own docstring
says "until a page reads from ContentDocument, a release activated here
changes nothing a visitor sees." Deferred past launch by design, not by
default.

## 8.2 What actually serves the site today

[`scripts/build_public_projection.py`](../../scripts/build_public_projection.py) —
a build-time script, not in `scripts/prod/`, not a database importer.

Source: three pinned upstream checkouts (`DataTalksClub/content`,
`DataTalksClub/datatalksclub.github.io`, `DataTalksClub/podwiki`) at exact
revisions.
Transform: builds the whole public projection — articles, podcasts, books,
people, wiki, media manifest — with digest-verified provenance.
Destination: [`content/public_projection/`](../../content/public_projection)`*.json`,
checked into git, served directly by
[`content/public_data.py`](../../content/public_data.py).

---

# 9. Event registrants (attendee-level) — being designed, not yet built

## 9.1 Planned journey

Source: the same prepared intermediate as 6.1 (attendee-level data already
present there, currently discarded at 6.2).
Transform: per event, once 5.2 has ensured that event has an identity, parse
its registrant rows. Consolidate each one against `accounts_customuser` by
`normalized_email` — the same table and matching logic that fixed source #4's
879 collisions — so someone who both took a course and registered for an
event resolves to one account, never two. An unmatched registrant gets a new
registrant-only identity in the same identity space (no login capability),
so a later match merges rather than duplicates.
Destination: a new registrant-identity model plus a per-event registration
fact table — not yet written. Admin-only visibility, matching the existing
member-email rule. Public event pages are unaffected — they keep showing 6's
aggregate counts.

Notes: backfill scope is every event from the first one onward, not just new
events going forward. Sequenced behind 5.2 landing.

---

# 10. Mailchimp newsletter subscriptions — not yet built

## 10.1 Planned journey

Source: a Mailchimp export, to be fetched by the owner and read in place —
same handling as the CMP and RDS exports, never copied into the worktree,
never committed.
Transform: matched by email against the same consolidated identity 9.1
resolves.
Destination: a subscription-status fact on that identity — not a third,
disconnected table.

---

# 11. Sponsors — not built

No script exists; `Sponsor` has 0 rows as of today's dry run. Source
undecided. Tracked as B9 in
[`production-data-migration.md`](production-data-migration.md).

---

# 12. FAQ — not built

`content_sync/faq/` does not exist. Source would be `DataTalksClub/faq` (6
courses / 70 sections / 1,401 questions). Today: a pinned JSON projection
with a CI checker only — no real sync builder, in either direction.

Notes: FAQ was named as one of the two good *presentation* models (alongside
podwiki) — that's about how FAQ content is laid out and served, unrelated to
this entry, which is the still-open question of how FAQ's own source
repository gets ingested.

---

# 13. Docs — not built

`content_sync/docs/` does not exist. Source would be `DataTalksClub/docs`
(106 pages / 39 assets). Same gap as FAQ: a pinned JSON projection with a CI
checker only.

Notes: docs *presentation* Pass 0 landed today, entirely on the existing
projection, no source-repository changes. This entry is the separate
question of syncing the docs repository itself — covered in
`.tmp/content-ingest-design.md` and `.tmp/docs-layout-proposal.md`
(uncommitted, not linkable), with five owner decisions pending, including
whether `/docs/` even stays live (CloudFront currently 302s it away
regardless of what Django serves).

---

# 14. Podwiki

`DataTalksClub/podwiki` — the wiki's own repository, separate from
`DataTalksClub/content` and staying that way (282 pages, plus the wiki graph
and search corpus).

## 14.1 What actually serves the site today

Folded into 8.2 — [`scripts/build_public_projection.py`](../../scripts/build_public_projection.py)
reads podwiki as one of its three pinned upstream checkouts
(`WIKI_REPOSITORY`, `build_public_projection.py:82`), alongside
`DataTalksClub/content` and `DataTalksClub/datatalksclub.github.io`.
Destination: `content/public_projection/{wiki,wiki_graph,wiki_search}.json`.

## 14.2 Database sync — not built

No `content_sync/podwiki/` or equivalent exists. Same gap as FAQ (12) and
docs (13): a static build only, no dynamic sync builder in either direction.

---

# 15. Public media objects (images)

The object-store-backed media pipeline — distinct from the content/data
pipelines above, which write rows; this one writes bytes to S3 and reconciles
them against what the rows reference. Covered in detail in
[`production-data-migration.md`](production-data-migration.md), summarized
here for completeness.

## 15.1 Hydrate

`manage.py public_media_hydrate`

Source: the pinned upstream content checkouts (same ones 8.2 reads).
Transform: content-sniffs every file (JPEG/PNG/GIF magic bytes checked, not
trusted from the extension), sanitizes SVGs (rejects `<script>`, `<style>`,
event handlers, remote `href`/`src`/`url()` — no exemption for anything,
including owner-supplied artwork).
Destination: the local media store (or S3, depending on
`PUBLIC_MEDIA_STORE_BACKEND`), keyed by normalized record identity, not by
the incoming filename.

## 15.2 Publish

`manage.py public_media_publish` (S3 backend only — refuses under `local`)

Source: the hydrated local store from 15.1.
Transform: uploads to the configured bucket.
Destination: `s3://dtc-website-media/images/{authors,posts,books,podcast}/`
and `site-assets/{home,sponsors,testimonials}/` — flattened today from the
former `public-projection/` prefix.

## 15.3 Verify

`manage.py public_media_verify`

Source: the live bucket, compared against `media.json`'s records.
Transform: none — a reconciliation report only (`matched`/`missing`/`extra`/
`mismatched` counts).
Destination: none; this is a check, not a write.

---

# 16. `rds-aisl_prod` (second production database) — undecided

No script, no decision made yet. Precise path, confirmed today: a second,
separate database rotates daily alongside the main CMP export —
`/data/tmp/rds-export/rds-aisl_prod-<YYYYMMDD>-<HHMMSS>.db` (6 daily
snapshots present, Aug 28 – Sep 2, ~48–55 MB each; naming and rotation
pattern match `rds-prod-*` exactly). 108 tables, 151,402 rows per an earlier
audit. Not referenced in any migration document before that audit found it.

Notes, found while checking this entry's precision, not yet investigated
further: `/data/tmp/rds-export/relay/` holds a same-pattern `rds-relay-*.db`
export (Sept 2), and `/data/tmp/rds-export/website/` holds
`rds-dtc_website-bootstrap.db` — both unexplored, both outside this
inventory's current scope, flagged so they aren't lost.

Needs an owner decision on scope before this can become a scoped task, let
alone a script.
