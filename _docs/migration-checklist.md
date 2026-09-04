# Migration checklist

Use this checklist for the final migration into the unified website. A checked
source migration means that its source revision or snapshot is frozen and
recorded, the import is repeatable, counts and sampled records reconcile, public
routes work, and rollback evidence exists. Importing data alone is not completion.

Do not copy secrets, tokens, unnecessary personal data, or expired operational
state. Production imports must follow the retention and consent rules in the
authoritative specifications under `_docs/specs/`.

## Courses and learners

- [ ] Import pre-CMP course history from `DataTalksClub/zoomcamp-scoring`.
  - [ ] Cohorts, homework and project definitions.
  - [ ] Graded submissions, scores, leaderboards and certificates.
  - [ ] Reconcile recovered learner identities without duplicating current accounts.
- [ ] Import the current Course Management Platform database.
  - [ ] Courses, cohorts, curriculum, homework and projects.
  - [ ] Accounts, enrollments, registrations and profile snapshots.
  - [ ] Homework/project submissions, peer reviews, votes and complaints.
  - [ ] Scores, leaderboards, certificates and historical Wrapped data.
  - [ ] Registration campaigns, notification preferences and durable job state.
  - [ ] Reconcile historical registration-count baselines without double-counting
        imported native rows.
- [ ] Apply and verify the reviewed legacy course-to-family/cohort mapping.
- [ ] Preserve legacy numeric IDs, public paths, calendar UIDs and API aliases.
- [ ] Freeze legacy course writes, run the final delta import, and reconcile all
      row counts and computed statistics before cutover.

## Public editorial content

- [ ] Import the pinned DataTalks.Club Jekyll website content.
  - [ ] Articles/blog posts and article FAQ sections.
  - [ ] Podcast episodes, transcripts, guests, listening links and media.
  - [ ] Books, people, events, tools and other collection records.
  - [ ] Images, downloads and other referenced static assets.
  - [ ] Frontmatter, publication dates, authorship and structured metadata.
- [ ] Import Docs from the pinned `DataTalksClub/docs` source.
- [ ] Import course FAQ content from the pinned FAQ source.
- [ ] Import the wiki from legacy Podwiki.
  - [ ] Pages, headings, stable fragments, typed links and citations.
  - [ ] Graph and search projections.
- [ ] Verify every source commit, generated projection digest and provenance
      record before activating the release.

## Accounts and identity

- [ ] Build one identity reconciliation across CMP accounts, historical-course
      participants, event registrations and mailing-list contacts.
- [ ] Review duplicate, conflicting and quarantined identities; never merge
      authority, consent or profile values by assumption.
- [ ] Preserve account status, staff authority, groups, permissions and relevant
      session-revocation state.
- [ ] Preserve required privacy/consent evidence and outstanding deletion or
      correction requests.
- [ ] Decide which legacy authentication identities are migrated and which OAuth,
      API and service credentials must be rotated instead of copied.

## Events and community data

### Located local Luma snapshot (verified 2026-08-31)

The protected Luma export is present in the main checkout at
`/home/alexey/git/dtc-website/.local/migration-data/events/luma/`. It is outside any linked
worktree because the preparation script resolves protected migration data from
the Git common checkout. The directory contains the raw per-event CSVs, matching event checkpoint
JSON files, and descriptions.

The adapter-ready copy is at
`/home/alexey/git/dtc-website/.local/migration-data/events/luma-aggregate-v1/`. It contains 166
CSV/checkpoint pairs and matches the recorded tree checksum in
`_docs/migration-data/event-registration-sources.json`:

- capture completed: `2026-08-29T20:46:15.172888+00:00`;
- input rows: `51,924`;
- accepted aggregate rows: `51,873`;
- excluded rows: `51`;
- activation state: `mapping_review_required`.

This export is protected attendee-level migration data, not an API credential or a live Luma
connection. No Luma token is stored in the repository, worktrees, CI artifacts, or this document.
The source and prepared directories are gitignored. Never copy their rows into logs, screenshots,
issues, reports, or public projections. The checksum and aggregate facts above are the only safe
facts intended for migration evidence.

- [ ] Provision the protected local source bundle under the repository root at
      `.local/migration-data/events/` (currently
      `/home/alexey/git/dtc-website/.local/migration-data/events/`). The directory
      is intentionally gitignored; never commit, log or screenshot attendee-level
      fields from it.
  - [ ] Prepare adapter-ready copies from the main checkout (including when the
        command is run from a worktree) with
        `uv run python scripts/prepare_event_registration_sources.py --luma-source
        /home/alexey/git/dtc-website/.local/migration-data/events/luma
        --eventbrite-source
        /home/alexey/git/dtc-website/.local/migration-data/events/eventbrite/export.zip
        --replace`;
        the command reports only checksums and aggregate schema/count facts.
- [ ] Import the canonical event catalogue and stable event identities.
- [ ] Import Luma registration totals.
  - [x] Create a fresh per-event CSV export with the Luma exporter and record its
        cutoff, tree checksum and safe aggregate facts in
        `_docs/migration-data/event-registration-sources.json`.
  - [ ] Map every Luma event ID exactly to a canonical website event or an explicit
        reviewed exclusion; quarantine ambiguous and missing mappings.
  - [ ] Count only statuses accepted by the reviewed policy and discard attendee
        identity fields after deriving aggregate evidence.
  - [ ] Activate the reviewed Luma aggregates and verify the registration count is
        visible on each mapped public event page as `N registered`. This is one
        registration aggregate, not attendance or check-in evidence.
- [ ] Import Eventbrite registration totals.
  - [x] Record the protected Eventbrite archive checksum, normalize its wrapper for
        the aggregate adapter and validate every embedded event CSV against an
        accepted schema fingerprint. Safe facts are recorded in
        `_docs/migration-data/event-registration-sources.json`.
  - [ ] Map every Eventbrite event ID exactly to a canonical website event or an
        explicit reviewed exclusion; quarantine ambiguous and missing mappings.
  - [ ] Count only statuses accepted by the reviewed policy and discard attendee
        identity fields after deriving aggregate evidence.
  - [ ] Activate the reviewed Eventbrite aggregates and verify the registration count
        is visible on each mapped public event page.
- [ ] Reconcile events present in both Luma and Eventbrite with an explicit coverage
      policy so totals are combined only when the populations are provably disjoint.
- [ ] Preserve calendar aliases, attendance state and event-email preferences.
- [ ] Decide whether any live Q&A rooms, questions, answers or moderator state need
      migration; otherwise record them as intentionally retired.
- [ ] Migrate sponsors, testimonials and other Studio-managed site records that are
      not already supplied by the editorial projection.
- [ ] Decide whether Slack/community records require migration. Do not copy Slack
      member data merely because the integration exists.

## Email and Mailchimp

- [ ] Import Mailchimp contacts with Mailchimp authoritative for legacy marketing
      subscription state.
  - [ ] Subscribed, unsubscribed, cleaned and suppressed states.
  - [ ] Audience/list identity, tags and required merge fields.
  - [ ] Consent source and timestamps where available.
  - [ ] Normalize and deduplicate contacts against website identities.
- [ ] Import required Datamailer history with sending disabled.
- [ ] Reconcile queued/in-flight messages, templates, purposes and idempotency keys.
- [ ] Prove there is only one active sender for each approved purpose before
      enabling the new email path.
- [ ] Send no migration, welcome or course email merely because a row was imported.

## URLs, search and discoverability

- [ ] Validate the complete legacy URL, redirect, fragment and asset manifest for
      main site, courses, docs, FAQ and Podwiki.
- [ ] Preserve canonical URLs, metadata, structured data, feeds and sitemap entries.
- [ ] Rebuild and verify public search, FAQ search and wiki graph/search indexes.
- [ ] Check internal links and external references against the activated projection.
- [ ] Keep compatibility redirects one-hop and preserve safe query strings.

## Configuration that is not data migration

- [ ] Configure production OAuth applications, email providers, object storage,
      domains and webhook secrets through the approved secret stores.
- [ ] Rotate API tokens and service credentials; do not copy development or legacy
      secrets into migration artifacts.
- [ ] Recreate scheduled jobs, queues, cache invalidations and monitoring rules from
      reviewed configuration rather than database accidents.
- [ ] Record explicit decisions for analytics history, old logs and transient job
      records; default to retention-safe non-migration.

## Rehearsal and cutover

- [ ] Record source snapshot IDs, commits, checksums, schema versions and row counts.
- [ ] Run every importer twice and prove the second run is an idempotent no-op.
- [ ] Reconcile totals and sample records across every source and destination.
- [ ] Verify permissions and private-data isolation with representative accounts.
- [ ] Test public routes, search, downloads and desktop/mobile rendering.
- [ ] Rehearse backup, restore and rollback using production-shaped copies.
- [ ] Freeze old writes, drain or classify old jobs, run bounded final deltas and
      record the cutover timestamp.
- [ ] Keep old systems read-only until the acceptance and rollback window closes.
- [ ] Record owners and dispositions for every unchecked item before declaring the
      migration complete.
