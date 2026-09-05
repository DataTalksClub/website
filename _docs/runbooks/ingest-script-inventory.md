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

## The main design principle

**Import machinery is disposable. Only the data it produces is permanent.**

- Every source is ingested by a plain script in `scripts/prod/` — never a
  Django management command, never a Makefile wrapper around one. A script
  is something with a defined beginning and end; a management command reads
  as permanent application surface even when it isn't.
- Whatever an import needs to be idempotent, resumable, or safe to run
  concurrently — a progress watermark, a claim-tracking table, a run
  receipt with a uniqueness guarantee — is owned by the script, not the
  production model it writes into. `CustomUser`, `Event`, and every other
  live model carry only the fields the running application actually reads.
  A source-system id, a "have I already claimed this row" flag, an
  idempotency checksum: none of that belongs on a permanent model just
  because the importer that populates it needed it once.
- Production code has no special case for "this record came from an
  import." An imported account, event, or record is indistinguishable
  from any other once it exists — no `if account.is_legacy_imported`
  anywhere in a live request path. If a live code path needs to branch on
  something, that something is a real, general property of the record
  (verification state, lifecycle), never its provenance.
- **Once the real migration has run in production and the data is in,
  the scripts — and everything they needed only to run safely — are
  deleted entirely.** Nothing about how the data got here is meant to
  outlive the import. The result is permanent; the machinery is not.

Where an existing source violates this (an import-only field or table
still living on a permanent model), it's called out explicitly in that
source's entry below rather than left implicit, so it doesn't get
mistaken for how things are supposed to stay.

---

# 1. Course repositories

Three real stages, run in this order for the offline path; the webhook path
collapses 1.2 into itself (it fetches the commit archive directly, no checkout
needed).

## 1.1 Register sources

[`scripts/prod/sync_course_repository_sources.py`](../../scripts/prod/sync_course_repository_sources.py)
(`make content-sources`)

Source: [`content_sync/course_repository_sources.json`](../../content_sync/course_repository_sources.json),
a pinned, checked-in registration input — not a list in code.
Transform: creates or leaves alone one `ContentSource` row per registered
repository. Idempotent.
Destination: `content_sync.ContentSource`.

## 1.2 Checkout

`make content-checkouts` (wraps
[`scripts/prod/sync_course_repositories.py`](../../scripts/prod/sync_course_repositories.py)
`--checkout-plan` and `git clone`/`fetch` per source) — Makefile target

Source: GitHub, live network — the only network step in this journey.
Transform: clones a fresh repository or fast-forwards an existing one to the
registered branch.
Destination: a local git checkout per source, on disk only — not the database.

## 1.3 Ingest

[`content_sync/course_repository_ingest.py`](../../content_sync/course_repository_ingest.py),
driven by either the signed GitHub push webhook
([`content_sync/course_repository_webhook.py`](../../content_sync/course_repository_webhook.py))
or `scripts/prod/sync_course_repositories.py --from-disk <checkout-root>`

Source: the checkout from 1.2 (pull path), or a webhook push payload's commit
archive fetched directly (webhook path — skips 1.2).
Transform, exactly two files at the repository root plus the module/homework
tree:

- **`course.yaml`** — structural metadata: `slug`, `title`, `outcome` (a
  one-line summary), `repository_url`, `docs_url`, `faq_url`, `hashtag`,
  `published`, and `description_path`. That last field may **only** ever be
  the literal string `SITE.md` —
  [`content_sync/course_repository.py`](../../content_sync/course_repository.py)
  fails closed (`course_description_path_not_site_md`) on anything else, so
  a repository cannot silently point its description at some other file.
- **`SITE.md`** — the actual website copy: the longer description a course
  page renders (the "What you'll build"-style prose). Read only because
  `course.yaml` names it; a repository with no `SITE.md` yields no
  description at all rather than an error.
- Modules, units, homework, and projects, parsed from the repository's own
  directory structure (`cohorts/<year>/`, per-module folders). Snapshot
  transport and draft-detection are shared via
  [`content_sync/snapshot.py`](../../content_sync/snapshot.py) /
  [`content_sync/drafts.py`](../../content_sync/drafts.py), not reimplemented
  per source.

Destination: [`courses/models/`](../../courses/models) (`Cohort`, `Module`,
`Unit`, `Homework`, `Project`), plus the course's description fields.

**A repository without `course.yaml` at its root cannot be ingested by
either route at all** — confirmed live 2026-09-03:
`mlops-zoomcamp` and `stock-markets-analytics-zoomcamp` both 404 on
`course.yaml` despite each already having a real `SITE.md` with real
description copy already written. The content exists; only the small
structured file pointing at it is missing. See 1.4.

Because both are registered in 1.1 (they're now registerable even though
1.4's underlying fix hasn't landed), `make content-pull` with no filter
refuses the whole batch with a non-zero exit — every other registered
repository still ingests and commits successfully underneath a red exit
code. Pass `--stable-id` (see `sync_course_repositories.py`) to exclude the
two known-bad repositories explicitly, or read a non-zero exit here as
"check which repository failed," not "nothing landed."

## 1.4 Add the missing `course.yaml` — mlops-zoomcamp, stock-markets-analytics-zoomcamp

Not a script — a one-time fix to the two upstream repositories themselves,
each needing a `course.yaml` authored against the same schema
`ai-dev-tools-zoomcamp`/`llm-zoomcamp`/`ml-zoomcamp`/`de-zoomcamp` already
use, pointing `description_path` at the `SITE.md` each repository already
has. Once added, both become registerable in 1.1 and ingestible by 1.2/1.3
exactly like the other four — no code change needed here, since the ingest
already refuses cleanly on a missing file and needs nothing special to
accept one that exists.

**Check after landing**: confirm the data actually pulled, not just that
the file exists — register the source (1.1), check it out (1.2), run the
ingest (1.3), then query `Cohort`/`Module`/`Homework` for that `stable_id`
and confirm non-zero rows, the same verification done today for
`de-zoomcamp` (924 files, 7 modules, 7 homework, 88 units, landing on real
cohort rows) rather than trusting a clean exit code alone.

---

# 2. Pre-2023 Zoomcamp history

One raw source — a local checkout of the `zoomcamp-scoring` repository (e.g.
`~/git/zoomcamp-scoring`, outside this repository) — but six real internal
stages, not one script. The orchestrator
([`scripts/prod/import_legacy_zoomcamp.py`](../../scripts/prod/import_legacy_zoomcamp.py))
runs 2.5 then 2.6 per edition; 2.5 and 2.6 each call down into 2.1–2.4 as
needed. Listed in the order data actually flows, not file order.

## 2.1 Locate each edition's files

[`scripts/prod/legacy_zoomcamp/editions.py`](../../scripts/prod/legacy_zoomcamp/editions.py)

Source: the `zoomcamp-scoring` checkout's directory layout. 2022/2023
cohorts share one shape — `old/<course>-<year>/data/processed/hw-<slug>.csv`
(one row per learner, keyed by an upstream `sha1(email)` hash), the matching
`data/answers/answers-<slug>.json` (question text/points), a
`processed/project-<slug>.csv`, a `project/assignment-<slug>.csv`, a
`graduates*.csv`, and a `courses/<repo-slug>-<year>/graduates.json` for
hosted certificate URLs. 2021 ML Zoomcamp predates that shape and uses a
flatter, per-week layout with no per-cohort subdirectory, described as a
special case (`ML_ZOOMCAMP_2021`).
Transform: none — this module only resolves *where* each edition's files
are, not their contents.
Destination: none; an in-memory map consumed by 2.5 and 2.6.

## 2.2 Recover the real email behind the scoring hash

[`scripts/prod/legacy_zoomcamp/email_recovery.py`](../../scripts/prod/legacy_zoomcamp/email_recovery.py)

Source: the same checkout's **raw** (not processed) weekly Google Form
exports, graduate lists, and — for a few editions — a leaderboard email
reveal. The graded/processed exports 2.5 reads only ever carry the hash,
never the plaintext address.
Transform: joins the hash back to a real email, once per cohort, entirely
in memory.
Destination: none. The plaintext values are never written to disk by this
module — only handed to 2.3, which decides what to do with them.

## 2.3 Resolve or create the learner's account

[`scripts/prod/legacy_zoomcamp/identity.py`](../../scripts/prod/legacy_zoomcamp/identity.py)

Source: the recovered email from 2.2 (or its absence).
Transform, exactly: the **account** uses the real, recovered email — so a
learner who already has, or later creates, a real DataTalks.Club account
with that same address gets their historical cohorts, scores and
certificates attached to it, not to an orphaned duplicate. The learner's
**displayed** identity (leaderboard name, certificate name) is always a
freshly generated placeholder, never their real name — deliberately: we
don't have their real chosen leaderboard name from back then, and a real
name doesn't belong on a public leaderboard by default anyway, which is
already how every current, non-historical enrollment on this platform
behaves. When no email can be recovered at all (rare), the learner falls
back to a synthetic, clearly-marked local account keyed only by the
upstream scoring hash.
Destination: `accounts_customuser`, password left unusable (fixed today,
commit `1427ff3` — this call site previously left the password field blank
rather than explicitly unusable).

## 2.4 Enrich homework with real module content — optional

[`scripts/prod/legacy_zoomcamp/homework_content.py`](../../scripts/prod/legacy_zoomcamp/homework_content.py)

Source: a local checkout of the matching `DataTalksClub/<course>-zoomcamp`
repository (e.g. `~/git/data-engineering-zoomcamp`), if present —
`cohorts/<year>/`. Entirely public course content; no learner data
involved.
Transform: matches each imported homework to its real week/module folder by
**leading number, not position** (some editions skip a week), and returns
the real title and `homework.md` content in place of the generic
Google-Form-style label the graded export alone provides.
Destination: none; feeds into 2.5's `Homework`/`Question` writes. If no
local checkout is available, 2.5 falls back to the generic label — this
stage is a quality improvement, not a requirement.

## 2.5 Import scores

[`scripts/prod/legacy_zoomcamp/scoring_import.py`](../../scripts/prod/legacy_zoomcamp/scoring_import.py)

Source: the **processed/graded** exports located by 2.1 — never `raw/`,
which still carries GitHub links and free-text feedback alongside plaintext
email; that file is 2.2's job, not this module's.
Transform: parses homework and project scores per cohort, resolving each
learner through 2.3 and enriching titles/content through 2.4 when available.
Destination: `Cohort`, `Homework`, `Question`, `Answer`, `Submission`,
`ProjectSubmission`.

## 2.6 Import certificates

[`scripts/prod/legacy_zoomcamp/certificate_import.py`](../../scripts/prod/legacy_zoomcamp/certificate_import.py)

Source: the plaintext `email,name` graduate exports from 2.1, joined where
possible to the hosted certificate PDF URL in the current-format
`graduates.json` — matched by name, the only field both files share.
Transform: resolves the same real-email-backed account 2.3 uses; the stored
certificate name is always the freshly generated placeholder, never the
real one, same rule as 2.3.
Destination: certificate fields on `Enrollment`/`Cohort`.

## 2.7 Orchestrate — the actual entry point

[`scripts/prod/import_legacy_zoomcamp.py`](../../scripts/prod/import_legacy_zoomcamp.py)

Runs 2.5 then 2.6 for one edition at a time (`--list` to see what's
discoverable, `--cohort` to select one). Recalculates cohort-level totals
after scoring. Certificates run independently of scoring — a cohort with no
certificate source is reported (`"source": false`), not treated as an
error.

Notes: the only importer in this whole inventory that bootstraps an
entirely empty database. Verified today against all 7 real editions —
1,207s total wall time, idempotent on replay.

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
proven with a real `kill -9` mid-run and clean resume. Which `CustomUser`
this importer already created or attached for a given CMP source id is
tracked by `CmpClaimsStore`, a `--claims-file` JSON file this importer owns
(default: project-local `.tmp/`) — not a column on `CustomUser`. An earlier
revision carried that id as `CustomUser.cmp_source_user_id`; it is gone,
along with the migration that added it, following this inventory's own main
design principle above.
Destination: `accounts_customuser`, `account_emailaddress`.

## 4.2 Learner activity — not built

No script. Enrollments, submissions, answers, registrations, criteria
responses, peer reviews, project evaluation scores, wrapped statistics —
roughly 470,000 of the export's ~510,000 learner rows. **Largest open gap in
this entire inventory.**

## 4.3 Reconciliation

[`scripts/prod/import_account_reconciliation.py`](../../scripts/prod/import_account_reconciliation.py)
(+ [`scripts/prod/account_reconciliation/`](../../scripts/prod/account_reconciliation/))

Not part of 4.1's journey — it runs after every account-writing source (1, 2,
and 4.1) has landed, over whatever accounts exist by then, merging real
duplicate people the sources above wrote independently (the pre-2024 legacy
importer and the CMP importer see each other's writes only through 4.1's own
cross-source `normalized_email` match; everything else — the same person on
two *different* verified addresses, a username/email collision, an
authority-signature difference — is this step's job). Source: no export, no
network; it reads and writes only the database it's pointed at. Three modes,
never destination tables directly written by hand: dry-run (report-only,
finds candidate duplicate groups by shared normalized email, verified email,
username, or provider UID — never auto-merges), apply (writes, against one
reviewed mapping document a human produced from the dry-run's candidates),
rollback-check (proves the apply's evidence — aliases, unchanged relationship
checksums — is still intact; does not reverse anything). Full walkthrough:
[`account-reconciliation.md`](account-reconciliation.md).

Destination, on apply: `identity_state` flips (`absorbed` on the source row,
`active` on the survivor), every reparentable relation (enrollments,
submissions, project votes, wrapped statistics, and more — see
`accounts/identity_inventory.py`'s `ACCOUNT_RELATIONS`) moves to the
survivor, and one durable `accounts_accountidentityalias` row records the
mapping so a request that ever lands on the absorbed id resolves to the
survivor for the life of the application
(`accounts/identity_resolution.py`, `accounts/middleware.py`).

**This is the one entry in this whole inventory whose accompanying model —
`accounts_accountreconciliationrun`, the idempotency/concurrency record for
apply — is a real database table rather than script-owned file/dict state.**
Two simultaneous real applies of the same mapping must resolve to exactly
one merge, with the loser safely receiving the winner's cached result — a
real database `UniqueConstraint` gives that compare-and-swap atomically,
engine-enforced; a JSON file cannot, without reinventing cross-process
locking to guard an operation the migration runbook calls out by name as
having **no rollback**. See
[`scripts/prod/account_reconciliation/__init__.py`](../../scripts/prod/account_reconciliation/__init__.py)'s
module docstring for the full reasoning, and
[`accounts/tests/test_account_reconciliation.py`](../../accounts/tests/test_account_reconciliation.py)
for both properties proven against a real database, not asserted by reading
the code.

---

# 5. Event identity and content

## 5.1 Identity import

[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py) —
`import_identities()`. The former standalone `manage.py import_event_identities`
command (and its `import_event_identity_manifest` alias) are retired: they
wrapped this exact call, and had no caller once
`scripts/prepare_local_data.py` was repointed at the function directly.

Source: the checked-in, human-reviewed
[`events/event_identity_manifest.json`](../../events/event_identity_manifest.json)
(421 events, 1,684 aliases).
Transform: allocates `public_id` via `EventPublicIdSequence`; writes aliases.
Destination: [`events/models.py`](../../events/models.py) (`Event`,
`EventAlias`).

## 5.2 Content import

[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py) —
`import_content()`, run straight after `import_identities()` in the same
`run()`.

Source: the checked-in, reviewed
[`temporary/content/public_projection/events.json`](../../temporary/content/public_projection/events.json)
(421 records, 159 of them carrying a description). A staging artifact, not a
serving path: built offline from the legacy `_data/events.yaml` and then
rewritten by the description bridge, which stripped the "about the speaker"
biography and the platform boilerplate and bound every surviving link to a
reviewed destination (`_docs/event-description-bridge.md`).
Transform: [`events/content_import.py`](../../events/content_import.py)
validates the complete candidate, then resolves each record against the
identity row by its exact legacy tuple, title and slug. It reconciles only —
a record naming an identity the database does not hold is a refusal, never a
new event — and refuses a description carrying no bridge provenance.
Destination: [`events/models.py`](../../events/models.py) (`EventContent`,
`EventSpeaker`, `EventLink`), read by
[`events/queries.py`](../../events/queries.py).

Speakers and links are an ordered set the record owns outright, so a re-run
replaces them wholesale rather than merging; an unchanged record reports
`unchanged` and writes nothing.

## 5.3 New-event identity creation

`discover_new_luma_event_identities()` in
[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py), called
from that module's own `run()` — live, not a gap. `create_event_identity()`
in [`events/identity.py`](../../events/identity.py) is its one caller: for
each event in the real Luma export with no existing manifest-reviewed or
auto-created identity, it creates one directly (never the reviewed-manifest
path), matched on exact case/whitespace-normalized title plus date, same
discipline used throughout this migration. Verified against the real export
2026-09-03: 166 candidates, 164 already-tracked, 0 newly created (idempotent
re-run), 2 with no usable title/date metadata reported rather than guessed.
Registration-count activation (6.3) stays a separate, human-gated step
regardless.

## 5.4 Staged content for discovered events

Two scripts, because the build and the load are different jobs with different
inputs.

**Build**: [`scripts/build_luma_event_descriptions.py`](../../scripts/build_luma_event_descriptions.py)
over [`scripts/staging/luma_event_descriptions.py`](../../scripts/staging/luma_event_descriptions.py).

Source: a description export root holding `descriptions/*.md` beside
`_json/*.json`, one pair per event, in the operator's gitignored `.local/`.
Plus [`_docs/migration-data/local-event-type-input.json`](../../_docs/migration-data/local-event-type-input.json),
the reviewed `type` per description file, which a person maintains and which
ships empty.
Transform: resolves each pair to an `Event` by the provider's own event id —
the exact `source_key` 5.3 minted the row under, never a title or a slug —
takes `starts_at` from the checkpoint, takes `type` only from the reviewed
file, renders the Markdown through the description bridge's own Markdown and
link policies, and strips the speaker biography and the platform footer with
the same `normalize_description_html` the 421 went through. `ends_at` is
deliberately not taken: Luma derives it from a nominal duration.
Destination: `temporary/content/luma_event_descriptions.json`, a staging
artifact. Reporting is the default; `--write` replaces the file.

Nothing is inferred. A destination with no reviewed decision stops that event
and is reported **by URL** (approving one is an edit to
[`scripts/projection_build/event_description_link_policy.py`](../../scripts/projection_build/event_description_link_policy.py)
by a person, and host approval alone is deliberately not enough); an export the
reviewed type file does not name is reported and skipped; an export whose event
has no identity yet is reported and skipped. Verified against the real export
2026-09-05: 166 pairs, 164 resolved, 4 stopped for link review over 6 distinct
destinations, and 2 with no identity because 5.3 could not name them.

**Load**: `import_new_content()` in
[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py), called
from that module's own `run()` straight after 5.3 and again under
`--discover-new-events-only`.

Transform: [`events/content_import.py`](../../events/content_import.py)'s
`import_new_event_content()` validates the whole candidate — envelope,
declared counts and content digest recomputed, then every record — before
writing a row, and reconciles each one against the identity's own source
triple rather than the legacy tuple 5.2 uses. That is what keeps it off the
421: their triples name the legacy repository and can never equal a provider
one. A record naming an identity the database does not hold is a refusal,
never a new event; so is a description arriving without the review that decided
its type. A missing artifact is a normal state, reported as `present: false`.
Destination: [`events/models.py`](../../events/models.py) (`EventContent`,
`EventSpeaker`, `EventLink`), same rows 5.2 writes.

Replaying is safe, and measured: 160 records created on the first run, then
`replayed: true` with `unchanged: 160` on the second.

---

# 6. Luma + Eventbrite registration aggregates

Three stages: clean the raw protected export, derive aggregates from it,
resolve and activate each aggregate for public display. The middle stage is
where "attendee data exists but is deliberately not written to the database"
— see 9 below for the plan to change that.

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
(`LUMA_RELATIVE_SOURCE` in `import_events.py:86`) — gitignored and
worktree-local by design, so it is normal for it to be absent from any given
worktree, this one included.

**The durable copy — the one a real migration run should point at — lives
outside any worktree, at `/data/tmp/luma-eventbrite-export/luma-aggregate-v1/`**
(`chmod 700` directory, `600` files, the same protected-export handling as
`/data/tmp/rds-export/` and `/data/tmp/mailchimp-export/`). It was moved there
from the now-unreliable worktree-scratch copy at
`.tmp/luma-prepared-20260831/luma-aggregate-v1/` and verified byte-for-byte
identical first (332 files, sha256 diff clean). Point `--luma-source` at it
directly, or symlink/copy it to `.local/migration-data/events/luma-aggregate-v1`
if a given worktree should resolve the default path automatically.
Transform: aggregates to counts only — the module's own docstring states "no
attendee value crosses this module boundary".
Destination: `HistoricalRegistrationAggregateRevision`,
`HistoricalRegistrationSourceRun`. There is no separate mapping model or
review-state row: each aggregate revision either resolves directly to a
canonical `Event` (its nullable `event` field, set once, never retargeted) or
it does not.

## 6.3 Resolve and activate

**Resolution** — does this provider event correspond to a canonical
`Event` — happens two ways, both applied every run of
[`scripts/prod/import_events.py`](../../scripts/prod/import_events.py), never
as a persisted review queue or a Studio page:

- *Explicit*: staging an aggregate resolves it immediately when
  `--current-registration-input` names its exact provider identity (see
  [`_docs/migration-data/local-current-registration-input.json`](../migration-data/local-current-registration-input.json)),
  and re-resolves an already-staged, still-unresolved aggregate on replay once
  the file has been extended. A human edits this JSON file and re-runs the
  script; there is no other way to name an exact pair.
- *Automatic*: `events.services.resolve_unmatched_aggregates` resolves
  whatever is still unresolved after staging, only when exactly one canonical
  `Event` shares the provider event's date and that event's
  case/whitespace-normalized title equals the provider event's normalized
  title exactly — no fuzzy or ranked match. Eventbrite's export carries no
  event-level title or date at all, so every unresolved Eventbrite row is
  reported unmatched under that reason.

Anything neither tier resolves is reported clearly (provider event
identifier, date, and why it's ambiguous) and stays `event=null` until a
human adds the right entry to the JSON input file.

**Activation** — whether a *resolved* aggregate's count is actually live for
public display — is a separate, still-gated step: Studio's
dry-run/validate/activate flow (`events.services.activate_source`), or the
narrower `activate_explicit_current_source` the import script calls for
exactly the aggregates the current-registration-input file names. A resolved
but not-yet-activated aggregate still renders no public count.

Notes: only a minority of provider events are resolved today; the rest
render no public count.

**Known gap — the Luma export on this machine is stale and only the site
owner can refresh it.** There is no live Luma API integration anywhere in
this codebase (confirmed by grep across `*.py` for any Luma API call —
none exists); registration data only ever arrives as an owner-provided
export, prepared by 6.1 and durably staged at
`/data/tmp/luma-eventbrite-export/luma-aggregate-v1/`. That export's newest
event is dated 2026-08-25, and the paired CSV/JSON checkpoints on disk cover
166 events in total, none dated after 2026-08-29. Real Luma events after
that date are not in this pipeline at all — a known example is several real
events dated September 8–15, 2026 that Luma already shows but this database
has never seen. This is expected staleness (a periodic export lagging
Luma's live state), not a bug, and it is not fixable from this sandbox: it
needs the site owner to pull a fresh Luma export (paired CSV + JSON
checkpoint per event, same shape as the existing one) covering events
through the current date, hand it to whoever runs the import, who then
reruns 6.1 (`prepare_event_registration_sources.py --luma-source
<new-export>`) followed by 6.2/6.3 (`scripts/prod/import_events.py`) to
derive, resolve, and activate the new events. Tracked in
[issue #310](https://github.com/DataTalksClub/website/issues/310).

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

# 8. Content — podcast, articles, people, books

One repository, `DataTalksClub/content`. **Not wiki, FAQ, or docs** — those
are separate repositories with their own entries (14, 12, 13) even though
8.2's build script happens to read all of them in one run; if you came here
looking for how wiki/FAQ/docs are ingested, that's the wrong section.

Two entirely separate journeys exist for this one repository: the database
pipeline (built, not yet serving) and what actually serves the site today
(a static build, not a database importer at all).

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
Destination: [`temporary/content/public_projection/`](../../temporary/content/public_projection)`*.json`,
checked into git, served directly by
[`content/public_data.py`](../../content/public_data.py).

---

# 9. Event registrants (attendee-level)

## 9.1 Import

[`scripts/prod/import_event_registrants.py`](../../scripts/prod/import_event_registrants.py),
via [`events/registrant_import.py`](../../events/registrant_import.py).

Source: the same prepared Luma export directory as 6.1/6.2 (attendee-level
rows already present there, discarded by 6.2's aggregate-only adapters). The
durable copy is `/data/tmp/luma-eventbrite-export/luma-aggregate-v1/`. Reads
it independently of [`events/importers.py`](../../events/importers.py), whose
own adapters (`derive_luma`, `derive_eventbrite`, used by 6.2) have a hard
"no attendee value crosses this module boundary" contract that this journey
must not violate; `events/registrant_import.py` is the one module that does
cross it, deliberately kept separate.

Transform: per event, once 5.3 has ensured that event has an identity
(`events.identity.resolve_source_identity`), parse its registrant rows —
an event with no identity yet is reported under `awaiting_identity_events`
and skipped, never created here. Each row is consolidated against
`accounts_customuser` by `normalized_email` first — the same table and field
that fixed source #4's 879 collisions, and the same lookup shape as
`accounts.services.cmp_learner_import`'s `_find_cross_source_match` — so
someone who both took a course and registered for an event resolves to one
account, never two. If nothing matches there, a previously-seen
registrant-only identity from earlier in the same run (or an earlier run) is
reused before anything new is created; only then is a brand-new,
login-incapable registrant-only identity minted. Consolidation lookups are
global across the run — a person on event 3 is recognised again on event 300
— but processing itself is sequenced one event at a time, per the owner's
stated design, each inside its own transaction.

Destination: [`events/models.py`](../../events/models.py) —
`EventRegistrantIdentity` (the consolidated person: either `account` set to
an existing `CustomUser`, or `normalized_email` set on a registrant-only row,
never both), `EventRegistration` (one provider registration fact per
identity: event, provider, status, `registered_at` — never a name, email,
phone number, or the provider's own per-attendee token; replay safety comes
entirely from the transaction/progress-marker mechanism below, not from a
natural key stored on this table, so there is no reason to keep that
protected token around permanently), and `EventRegistrantImportProgress`
(per-`(provider, external_event_identifier)` completion marker; an event's
rows are only written once, inside one transaction, and a completed event is
skipped without reopening its file on replay). Admin-only: **no Studio
surface reads either table in this first pass** — a deliberate, conservative
default, not an oversight. Public event pages are unaffected — they keep
showing 6's aggregate counts; a later pass may derive that aggregate from
these rows instead, but this journey does not change how a public page gets
its count.

Notes: Eventbrite is not read yet — the durable export currently holds only
the Luma side, and this codebase's own Eventbrite adapter never needed that
provider's attendee-level column names (it only ever counted rows), so there
is no verified real schema to build or test an Eventbrite reader against yet.
`EventRegistration.Provider` and the matching logic are already
provider-generic; adding Eventbrite is a second `discover_*`/`read_*` pair,
not a model or matching-logic change. Backfill scope is every event from the
first one onward, not just new events going forward. Sequenced behind 5.3,
which has landed.

---

# 10. Mailchimp newsletter subscriptions

## 10.1 Import

[`scripts/prod/import_mailchimp_subscriptions.py`](../../scripts/prod/import_mailchimp_subscriptions.py)
(+ [`accounts/services/mailchimp_subscription_import.py`](../../accounts/services/mailchimp_subscription_import.py))

Source: a Mailchimp audience export, fetched by the owner and read in place —
same handling as the CMP and RDS exports: `--export-dir` is a required
argument, no default location, never copied into the worktree, never
committed. The export actually carries three CSVs (subscribed, unsubscribed,
cleaned), but this importer opens exactly one of them — matched by filename
prefix so the export's own per-export hash suffix doesn't need to be typed
exactly: `subscribed_email_audience_export_*.csv`. The unsubscribed and
cleaned files sit in the same directory and are never globbed for, opened,
or parsed — not merely unused; an earlier draft of this importer read all
three and wrote `False` for an unsubscribed/cleaned match, but the owner
narrowed scope before that shipped (quoted verbatim: "when we import we set
subscribed only to those who are subscribed in mailchimp"). For context only
— no database write is tied to either number — the real export's
unsubscribed and cleaned files carry 7,247 and 1,354 rows respectively.
Transform, exactly:

**Read from every row:** only the `Email Address` column
(`EMAIL_COLUMN` in the service module), matched against
`accounts_customuser.normalized_email` — the same field and case-insensitive
matching discipline used throughout this migration (source #4's CMP/legacy
dedup, source #9's planned registrant consolidation). This importer does
**not** wait on 9.1's registrant-identity model landing — it matches straight
against `accounts_customuser`, the identity that already exists today.
**Never read, from any row:** `OPTIN_IP`, `CONFIRM_IP`, `NOTES`. A structural
check of the real export (all three files, 130,854 / 7,247 / 1,354 rows)
found `NOTES` empty on every single row in every file, and `OPTIN_IP`/
`CONFIRM_IP` are real signup-IP PII nothing else this migration imports
carries — minimizing what gets stored is the safer default absent an
identified use. `TAGS` was inspected the same way while building this
importer (about 70% of subscribed rows carry it, average ~1.7
comma-separated values drawn from a small fixed vocabulary — not free text,
not sensitive) but is likewise never stored: no importer-facing use for it
exists today, so the same minimization default applies.
**A row in the subscribed file, matched to an existing account:** that
account's `newsletter_subscribed` is set `True`, explicitly — even though
this is usually a no-op against the field's own default, Mailchimp's
subscribed file is treated as the authoritative confirmation, not just an
absence of contrary evidence.
**A row with no matching account:** never creates one. Counted and reported
(`unmatched_rows`) for the caller to relay — out of scope by design, the
same "person we know about but isn't a member yet" class of row source #9's
planned registrant-identity model exists to hold; building a second,
parallel version of that model here would conflict with that design.
**An account with no match in the subscribed file** (including one that
*would* match Mailchimp's unsubscribed or cleaned file, since those are
never read): left completely untouched, at whatever value it already holds —
the model default (`True`) for an account no earlier run has touched.
Destination: [`accounts/models.py`](../../accounts/models.py)
(`CustomUser.newsletter_subscribed`, default `True` for every account
regardless of how it was created — a new signup, the legacy zoomcamp
importer, or the CMP learner importer all get it for free, none of them need
to know this field exists).

Notes: not resumable like source #4's importer — the matching step is a
fast, idempotent point lookup against an already-indexed column, so batching
(CSV streamed in fixed-size chunks, one `normalized_email__in` query plus one
`bulk_update` per chunk) is enough; a full re-run from row zero is cheap
(measured: ~2.7s for the real 130,854-row subscribed file against a database
of 20,239 real CMP-imported accounts — 12,723 matched, 118,131 unmatched, 0
accounts created) and idempotent (a second run's `accounts_changed` is 0). If
more than one existing account shares a `normalized_email` (a
pre-reconciliation duplicate), every one of them is updated, not just one
arbitrarily chosen row.

## 10.2 Event-category tags — runs independently of 10.1, after source #9

[`scripts/prod/import_mailchimp_event_tags.py`](../../scripts/prod/import_mailchimp_event_tags.py)
(+ [`events/mailchimp_tag_import.py`](../../events/mailchimp_tag_import.py),
mapping in [`events/mailchimp_event_tag_categories.py`](../../events/mailchimp_event_tag_categories.py))

Source: the same Mailchimp **subscribed** export CSV as 10.1, read in place,
same `--export-dir` convention — but this script re-opens it independently
and reads its own single column pair (`Email Address`, `TAGS`); it does not
depend on 10.1 having run first. It reads *after* source #9 conceptually,
though not by file dependency: an identity this importer resolves is drawn
from the exact same pool source #9 (Luma/Eventbrite registrants) already
populates, via the shared, now-public
[`events.registrant_import.resolve_registrant_identity`](../../events/registrant_import.py)
(renamed from a module-private helper specifically so this importer could
reuse it rather than reinvent it).

32 distinct `TAGS` values exist across the real export (structural count
confirmed by 10.1; re-verified directly by this importer's own real-export
run below). Three are dropped outright, never imported under any
circumstance: `registered-in-slack`, `Berlin DataTalks Club Group`,
`ai-bootcamp-free-email-course` — none maps to a real Course or Event, owner
decision, listed in `DROPPED_MAILCHIMP_TAGS` so a reader can tell "considered
and rejected" apart from "not yet reviewed." The remaining values split into
two families with different status:

**Event-category tags — this entry.** Eight tags (`event`,
`event-conference`, `event-podcast`, `event-production`, `event-analytics`,
`event-data`, `events-soft`, `events-data-science`) map, through the
hardcoded, reviewed `MAILCHIMP_EVENT_TAG_CATEGORIES` table, onto
`events.models.EventRegistrantInterestSignal.Category` (`general`,
`conference`, `podcast`, `production`, `analytics`, `data`, `soft_skills`,
`data_science`). A Mailchimp tag is never a specific event the way a real
Luma/Eventbrite row from source #9 is — it names a broad, self-selected or
campaign-applied interest with no event identified anywhere in the source
data — so this lands as a category/interest signal on
`EventRegistrantIdentity`, in a dedicated table
(`EventRegistrantInterestSignal`: `identity`, `category`, `source`), never as
a new `EventRegistration` row. Folding it into `EventRegistration` would
either fabricate an `event` FK that does not exist in the source or blur two
different kinds of fact (a real per-event attendance record vs. a broad
tag-derived interest) in one table; see that model's own docstring for the
full reasoning.
Transform, exactly: a row carrying none of the 8 event tags is skipped before
any identity lookup happens — no identity created, no signal written, nothing
about it stored. A row carrying at least one is consolidated by
`normalized_email` through source #9's exact discipline (existing account
wins, then an existing registrant-only identity, then a brand-new
registrant-only identity — never a second, parallel identity space for
Mailchimp-tag people), then gets one `EventRegistrantInterestSignal` row per
matched category, written through `get_or_create` against a
`(identity, category, source)` unique constraint — the idempotency guarantee:
a replayed row finds the same identity and the same already-present signals,
writing nothing new.
Destination: [`events/models.py`](../../events/models.py) —
`EventRegistrantInterestSignal` only. Never `CustomUser`, never
`EventRegistration`.

Notes: real-export run against a rehearsal database seeded with 20,239 real
CMP-imported accounts (source #4) and 28,722 real Luma-derived
registrant-only identities (source #9, 164 auto-created event identities,
51,924 registration rows) — of the export's 130,854 subscribed rows, 8,606
carry at least one of the 8 event tags (`rows_by_tag` per-tag counts:
`event` 8,513, `event-conference` 719, `event-podcast` 712,
`event-production` 409, `event-analytics` 127, `event-data` 42,
`events-soft` 34, `events-data-science` 31 — exactly the counts found while
scoping this importer), producing 10,587 interest-signal rows: 2,069 rows
matched an existing account, 3,538 matched an existing registrant-only
identity, 2,999 created a brand-new one. A second run changed nothing
(`signals_created_total` 0, all 10,587 already present). `--dry-run` computes
every count through read-only queries only (a would-be-new identity is never
inserted to compute it), confirmed to agree with a real run on the same
input.

**Course tags — blocked on [#286](https://github.com/DataTalksClub/website/issues/286), completely out of scope for this entry.**
The remaining tags are per-course, per-launch labels (`de-zoomcamp-1`,
`de-zoomcamp-2`, `de-zoomcamp-2024`, `de-zoomcamp-2025`, `de-zoomcamp-2026`,
`ml-zoomcamp-1`, `ml-zoomcamp-2`, `ml-zoomcamp-2023`, `ml-zoomcamp-2024`,
`ml-zoomcamp-2025`, `mlops-zoomcamp-1`, `mlops-zoomcamp-2023`,
`mlops-zoomcamp-2024`, `mlops-zoomcamp-2025`, `llm-zoomcamp-2024`,
`llm-zoomcamp-2025`, `llm-zoomcamp-2026`, `ai-dev-tools-zoomcamp-2025`). The
ordinal tags (`-1`, `-2`) predate each course's switch to year-named tags;
the owner resolved the exact year each one means, cross-checked against the
real pre-2023 editions [`scripts/prod/legacy_zoomcamp/editions.py`](../../scripts/prod/legacy_zoomcamp/editions.py)
actually imported (source #2) — not a guess:

| Tag | Cohort year |
| --- | --- |
| `de-zoomcamp-1` / `de-zoomcamp` | 2022 |
| `de-zoomcamp-2` | 2023 |
| `ml-zoomcamp-1` / `ml-zoomcamp` | 2021 |
| `ml-zoomcamp-2` | 2022 |
| `ml-zoomcamp-2023` | 2023 (already year-named) |
| `mlops-zoomcamp-1` / `mlops-zoomcamp` | 2022 |
| `mlops-zoomcamp-2023` | 2023 (already year-named, no `-2`) |
| `llm-zoomcamp-2024`/`-2025`/`-2026` | year-named throughout, no ordinal tags — first launch was 2024 |
| `ai-dev-tools-zoomcamp-2025` | year-named, single launch year |

This table is settled and ready to use, but storing or assigning anything
from it is still blocked: [#286](https://github.com/DataTalksClub/website/issues/286)
is an explicit decision gate on `CourseInterest` identity, dedupe key, and
retention ("Engineering must not infer those choices... A PM recommendation,
silence, code convenience... must not start engineering"). No course-tag row
is imported, no `CourseInterest` is created, until the owner answers `I1`,
`I2`, `I3`, or an equally exact replacement on that issue.

sma-zoomcamp carries no tags in the export by design, not a gap — it was
never imported here and its registrations live on an external form (owner
confirmed).

---

# 11. Sponsors

Single stage, but the destination model predates it: `Sponsor` and
`SponsorPlacementAssignment` ([`core/models.py`](../../core/models.py)) and
their Studio/admin-API service
([`core/sponsors.py`](../../core/sponsors.py)) already existed for the
events_hub "Supported by" band on `/events`. This entry is the second
placement onto the same model, not a new one — the reviewed set below is
what makes `public_directory` the second value the
`core_sponsor_placement_allowlist` constraint permits, alongside
`events_hub`.

## 11.1 Import

[`scripts/prod/import_sponsors.py`](../../scripts/prod/import_sponsors.py)

Source: [`core/sponsor_directory.json`](../../core/sponsor_directory.json),
reviewed and checked in. Every entry carries the `key` `Sponsor` already
reserves as its natural identifier, so a second run is keyed on that rather
than on name matching.
Transform: every write goes through `core.sponsors`' shared
`create_sponsor`/`update_sponsor`/`archive_sponsor`/`reactivate_sponsor` —
the same service Studio and the admin API call — never a bypassed ORM write.
An entry with a `position` (the four companies retired from
`core.sponsor_history.FEATURED_SUPPORTERS`: dltHub, Astronomer, Kestra,
Snowplow) is created `active` with one `public_directory` assignment at that
position. An entry with `position: null` (the remaining 29 names retired
from `FEATURED_SUPPORTERS ∪ PAST_SUPPORTERS`) is created `draft` and archived
in the same run — `create_sponsor` never accepts `archived` directly, so
reaching it is always create-then-archive, the two steps a Studio editor
would take by hand. `PAST_SUPPORTERS` named every company ever sponsored,
including the four still featured today; since `key` is unique, that overlap
became one lifecycle axis instead of two rows per overlapping company —
`core.sponsors.public_supporter_history()` reproduces the original list
exactly by reading every `active` or `archived` sponsor, not just the
archived ones. Reconciling an already-archived row (a reviewed-file
correction after the first import) reactivates it, applies the edit, and
re-archives it — again, the same three Studio steps, never a direct write to
an archived row.
Destination: `Sponsor` (`public_directory` placement for the featured four,
`archived` lifecycle with no placement for the rest) and `AuditEvent` (one
append-only audit record per write, same as every other `core.sponsors`
mutation).

Notes: `core/views.py`'s `home` and `sponsors` views both read
`core.sponsors.public_sponsors()` (the `public_directory` placement) and
`sponsors` additionally reads `core.sponsors.public_supporter_history()` —
homepage and `/sponsors` render the identical featured set from the
identical placement, so this is one placement key, not two. Sponsor logos
(`logo_asset_key`, resolved through the guarded `Sponsor.logo_url`, never a
raw `{% static %}`/`static()` call on the stored value — see
`courses.models.Testimonial.portrait_url` for the same guard) and the
long-form directory `description` are populated only by this import today;
neither is yet a Studio- or admin-API-writable field, unlike every other
`Sponsor` field, which editors already manage in Studio exactly as they do
for an events_hub sponsor.

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
Destination: `temporary/content/public_projection/{wiki,wiki_graph,wiki_search}.json`.

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

[`scripts/prod/sync_public_media_hydrate.py`](../../scripts/prod/sync_public_media_hydrate.py)

Source: the pinned upstream content checkouts (same ones 8.2 reads).
Transform: content-sniffs every file (JPEG/PNG/GIF magic bytes checked, not
trusted from the extension), sanitizes SVGs (rejects `<script>`, `<style>`,
event handlers, remote `href`/`src`/`url()` — no exemption for anything,
including owner-supplied artwork).
Destination: the local media store (or S3, depending on
`PUBLIC_MEDIA_STORE_BACKEND`), keyed by normalized record identity, not by
the incoming filename.

## 15.2 Publish

[`scripts/prod/sync_public_media_publish.py`](../../scripts/prod/sync_public_media_publish.py)
(S3 backend only — refuses under `local`)

Source: the hydrated local store from 15.1.
Transform: uploads to the configured bucket.
Destination: `s3://dtc-website-media/images/{authors,posts,books,podcast}/`
and `site-assets/{home,sponsors,testimonials}/` — flattened today from the
former `public-projection/` prefix.

## 15.3 Verify

[`scripts/prod/sync_public_media_verify.py`](../../scripts/prod/sync_public_media_verify.py)

Source: the live bucket, compared against `media.json`'s records.
Transform: none — a reconciliation report only (`matched`/`missing`/`extra`/
`mismatched` counts).
Destination: none; this is a check, not a write.

**Known gap — real-bucket orphan status needs a credentialed check.** See
[`public-media-objects.md`](public-media-objects.md) for the full account,
including a 2026-09-04 correction: an earlier report claiming a read-only
credentialed check found `matched: 997, extra: 0` (no orphans) was
fabricated — this sandbox has no real AWS access at all, confirmed
repeatedly (`aws sts get-caller-identity` fails 403 every time; the real
verify command run in this sandbox actually returns `matched: 0,
unreadable_count: 997`). The real-bucket orphan question (the ~257 objects
estimated from the local manifest diff) remains genuinely open and needs
someone with real credentials to check. Tracked in
[issue #310](https://github.com/DataTalksClub/website/issues/310).

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
