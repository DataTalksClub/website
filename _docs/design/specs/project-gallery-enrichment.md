# Project discovery and offline enrichment

Status: implementation contract for a follow-up issue. The gallery improvement in
#401 implements discovery over existing database rows only. It does not create the
models below, fetch repositories, invoke a model, or run a backfill.

Owner: `courses`. This supplements [Courses and cohorts](../../specs/04-courses-and-cohorts.md),
[database-only content](../../architecture/database-only-content.md), and the
[design system](../design-system.md). Existing submission, scoring, visibility,
repository, cohort and leaderboard destinations remain authoritative.

## Product boundary

`/courses/projects` is a discovery surface for individual learner submissions, not
a list of assignment types or a ranked assessment of learners. It must be useful
before enrichment: show the submitted repository address, actual assignment,
course/cohort, public enrollment display name, and existing assessment/vote state.
Do not call an assignment title an individual project's title. Repository names
are identifiers, not generated descriptions.

The public [Streamlit gallery](https://datatalksclub-projects.streamlit.app/) was
inspected on 2026-09-16 for product ideas: course/year selection, repository search,
project-title discovery, deployment/cloud metadata, and direct repository access.
It is not a data source or a visual specification. Its charts, copy, styling,
processed titles, and classifications are not imported.

The current page uses native GET filters (`q`, `course`, `year`, `sort`, `page`),
database-backed facets, bounded literal search, deterministic pagination, and
repository-first cards in the site's cream/lavender shell. The later dataset may
extend that contract without introducing request-time network calls or making
unenriched submissions disappear.

## Source facts and enriched fields

| Field group | Authority | Public behavior |
| --- | --- | --- |
| Submission identity, repository URL, submitted commit and timestamp | Existing `ProjectSubmission` | Preserve the recorded destination; do not silently replace it with a discovered demo or repository root. |
| Assignment, course/cohort title and route identity | Existing `Project` → `Cohort` → `Course` | Live database relationships; never inferred from repository text. A year is filter metadata, not a cohort route identifier. |
| Contributor label/link, score, pass state, votes | Existing public enrollment/submission behavior | No inference from a GitHub account, commit author, model response, or private profile. |
| Project title and short summary | Reviewed enrichment revision | Optional, attributable descriptions of the submitted artifact. No unsupported claims about quality, production use, employment, or learner competence. |
| Technologies, topic, deployment style, cloud provider | Reviewed evidence-backed classifications | Optional multi-valued facets. Unknown is absence, not a guessed label or an “Error” tag. |
| Source evidence, confidence, processing/review state | Enrichment provenance | Staff-only detail; a bounded public provenance label may expose source date and revision basis, not prompts, private input, or provider payloads. |

No live page reads a checked-in JSON dataset, job artifact, local cache, GitHub, or
Streamlit. All accepted public values are persisted database records. Template
interface labels are not an editorial fallback for missing project descriptions.

## Proposed database model

Introduce additive, optional records; do not add required fields to historical
submission rows or rewrite scores and assignments.

1. `ProjectDiscoveryRecord`: UUID primary key, one-to-one `submission` (cascade on
   an approved submission deletion), nullable `active_revision`, revision counter,
   and created/updated timestamps. The publication service validates that the
   active revision belongs to this exact record. No active revision means the
   existing repository-first card.
2. `ProjectDiscoveryRevision`: UUID, record FK, immutable input fingerprint,
   submitted URL/commit fingerprint, resolved repository identity and immutable
   source commit, source basis (`submitted_commit` or `observed_default_ref`),
   fetched timestamp, source manifest digest, pipeline version and code revision, schema
   version, provider/model identifier and reported model revision, prompt version
   and digest, inference-configuration digest, candidate title (maximum 200
   characters), candidate summary (maximum 700 characters), structured field
   evidence, per-field confidence, validation report, state, and timestamps.
   Review state is `candidate`, `approved`, `rejected`, or `failed`; failed attempts
   hold a bounded failure category, not a fabricated title. Reviewed rows record
   reviewer identity, review time and a reason. Use a uniqueness constraint on
   `(record, input_fingerprint, pipeline_version)`.
3. `ProjectDiscoveryTag`: a database-owned taxonomy with a unique `(kind, key)` and
   reviewed display label. Kinds are technology, topic, deployment and provider.
   Revision-to-tag through rows carry evidence references and confidence, with a
   unique `(revision, tag)` constraint. Do not manufacture public tags from a
   Python fallback vocabulary.

The evidence schema identifies a field/tag, repository-relative path, source
commit, content hash, line/span locator, extraction method, and a short bounded
supporting excerpt where permitted. Validate this operational JSON strictly;
arbitrary HTML, unbounded provider responses, or a file-backed content projection
are not accepted. Candidate and evidence length limits apply before database
writes. Confidence lies in `[0, 1]` and is an assessment signal, not a calibrated
truth probability or a publication permission.

Keep revisions rather than overwriting approved text. Manual corrections create a
new reviewed revision with human provenance; automatic processing cannot replace
them. A separate explicit publish operation moves the active pointer with an
expected record revision and an audit record. Studio and the admin API must call
the same permission-checked publication service when that follow-up ships.

## Allowed offline input and execution

The job selects only submissions currently eligible for the public gallery:
visible course, visible cohort, and not volunteer-review-only. It reads the
submitted repository URL and commit from the database. It does not use learner
email, private profiles, registration answers, review comments, source exports,
cookies, tokens, or browser sessions as enrichment input.

Initial fetch support is public HTTPS GitHub repositories. Unsupported providers,
private repositories and authentication challenges are recorded as unsupported or
unavailable, without prompting a learner for access. Existing valid outbound
repository links remain usable even when enrichment does not support the host.
Reject userinfo, credential-shaped query parameters, local/private/link-local
destinations, unsafe redirects, path traversal and unbounded responses before
fetching. Revalidate every redirect and restrict source hosts. Never use a
submitted URL as a shell argument, archive extraction destination, or model tool
instruction.

Prefer the submitted commit if it is a valid full commit and can be verified in
that repository. If it is missing, a worker may resolve the current default ref
once, then fetch immutable content at that SHA and explicitly record
`observed_default_ref`. This is not evidence of the repository's state when the
learner submitted it. A supplied but invalid/unresolvable commit is a validation
failure, not permission to substitute HEAD silently.

Read a bounded allowlist of textual files: repository/project README, declared
dependency manifests and declarative deployment manifests relevant to the
submitted subdirectory. Start with at most 20 files, 100 KiB per file and 500 KiB
per submission, plus time and provider-budget limits. No repository code,
notebooks, build scripts, installers, macros, or network instructions are executed.
Do not recursively crawl links, download datasets, follow symlinks outside the
allowed tree, or fetch binary/model artifacts. The implementation issue must
freeze these budgets and test them.

Treat all source text as untrusted data, including apparent instructions to the
model. Use a fixed, versioned extraction prompt and a constrained output schema;
the model has no arbitrary tools or secrets. Prefer deterministic extraction for
explicit dependency/provider evidence; use inference only where supported by
source evidence. Redact or reject secret/PII canaries before provider submission
and publication. Do not publish copied README sections as summaries. Record
repository license/usage constraints and keep excerpts short and attributable.

Model and prompt versions must be recorded, not guessed. If a provider does not
expose an immutable model revision, record `unverified`, retain the reported model
name and timestamp, and require explicit human review. Do not claim reproducible
model output solely because an API alias or seed stayed the same.

## Identity, retries and publication

The durable upsert identity is the existing submission primary key, not a URL,
title, person, course/year pair, or repository owner. Two submissions sharing one
repository remain separate records. A fetch cache may reuse identical immutable
repository bytes, but never merge their enrollment or assessment metadata.

Compute the input fingerprint from canonical submitted URL/commit facts, resolved
source commit and ordered content hashes, extraction schema/prompt/configuration
versions, and pipeline code revision. Re-running the same identity and fingerprint
is a no-op or resumes a known failed attempt; it cannot create duplicate public
cards or tags. Provider retries are bounded and use the job's idempotency identity.
Use the repository's durable job/lease pattern, not an in-process loop hidden in a
web request. Each record is validated and persisted transactionally; a batch can
report partial success without partially publishing one record.

Candidates are not public. Approval requires supporting evidence for every
nonempty field, schema/safety checks, and an identified authorized reviewer. A
confidence threshold alone never publishes model-authored content. Publishing
rechecks current gallery visibility and source identity inside the transaction.
A concurrent URL/commit change makes the candidate stale and prevents publication.
The page must also reject an active revision whose submitted-source fingerprint
no longer matches the live submission.

Re-enrichment creates a new candidate. A timeout, rate limit, deleted repository,
invalid response, low-confidence field or unsupported provider never erases a
still-applicable approved revision. It also never changes a learner's pass state.
Field absence is allowed: an evidenced title with no provider classification can
be approved; absent fields and tags simply do not render. Rejected/failed candidates
cannot replace the active pointer. Hide an enriched record immediately when its
underlying course/cohort/submission is no longer publicly eligible.

## Page, query and API contract

The base record always has its current repository/assignment/course/cohort context.
An applicable approved title may become the card heading; its short summary and
available tags follow. Keep the submitted repository address visible and preserve
the original outbound destination. A missing title still shows that address;
missing summary/tags do not leave placeholders, skeletons, blank cards or disabled
controls. Do not imply verification, endorsement or production readiness.

Extend search only to approved titles/summaries/tags, alongside the current literal
repository/assignment search. New technology/provider/deployment facets appear
only when applicable approved records supply them, using explicit database labels.
Keep course/year filters, 120-character search limit, native GET submission,
allowlisted sort values, escaped field errors, clear/reset behavior and pagination
query preservation. Results and facet counts use the same public visibility gate.
Unknown or retired selections show a recoverable invalid-filter/no-results state;
they must not silently widen a shared filter URL. Keep stable identity tie-breakers
for every sort; do not compare scores across different course rubrics as a ranking.

No API is added by #401. If a later issue exposes the same gallery through an API,
use a versioned public read serializer over this query service, not raw ORM models.
Its allowlist is submission public identity, repository destination, current public
course/cohort context, existing public display label and assessment state, optional
approved title/summary/tags, and bounded public provenance. Exclude private user
identifiers, email, prompts, raw evidence/provider payloads, rejected candidates
and job errors. HTML and API must share visibility, filtering, pagination and
publication rules. Search/learner-facing responses keep the existing no-store and
noindex boundaries until a separate cache/SEO review authorizes a change.

Use `select_related` for record/active revision and `prefetch_related` for bounded
approved tags, after pagination. Facets are SQL aggregates, not a Python scan of
all submissions. Add portable indexes for record/submission identity, revision
idempotency, review state/updated time, through-table tag/revision lookups, and
publication joins. Measure the existing `(project, submitted_at, id)` and
course/cohort filter paths before adding redundant indexes. Record explain plans
and query counts for empty, small and multi-thousand-row fixtures; card rendering
must not add one query per result. Introduce a portable database-backed search
projection only if measured latency requires it; no request-time embedding or
external search provider is assumed by this design.

## Rollout, artifacts and rollback

Ship additive migrations first with no content generation in migrations or deploy.
Run a named dry-run against an explicit bounded selection, then an authorized
small pilot. Review its candidates before publishing. A later resumable backfill
uses stable submission-id checkpoints, a frozen job configuration, concurrency
and cost caps, and stop/retry controls. Recheck visibility and source identity on
every resumed record; a checkpoint is not a permanent authorization.

Persist a job/run record and minimized manifest: run/configuration identifiers,
pipeline/schema/prompt/model versions, selected/completed/skipped/failed counts,
failure categories, fetch/model latency, bytes and token/cost totals where known,
candidate/approval counts, and record/revision IDs. Record unavailable cost/version
metrics as unavailable. Logs never include emails, credentials, full URL queries,
raw repository contents, model responses, or private registration data. Temporary
development artifacts stay under `.tmp/`; production job artifacts use private
managed storage with a reviewed retention period and access audit. Artifacts are
evidence, never a public request fallback.

Rollback first moves active pointers back to prior applicable approved revisions,
with audit and expected-revision checks, or clears them to restore the base gallery.
It does not delete submissions or revert assessment/identity data. Keep job and
revision provenance through the agreed retention window; destructive cleanup is a
separate authorized operation. Additive migration rollback is rehearsed separately
from content-pointer rollback and is never the routine recovery path.

## Acceptance tests for the follow-up

- [ ] Existing and sparse submissions render useful cards with zero enrichment rows;
  missing/failed/stale/low-confidence candidates add no invented public fields.
- [ ] Hidden families/cohorts, volunteer-only submissions, private fields and rejected
  candidates are excluded consistently from cards, facets, search and any API.
- [ ] Immutable submitted-commit and observed-default-ref inputs are distinguished;
  source changes during a job prevent stale publication.
- [ ] Duplicate/concurrent jobs are idempotent; partial failures preserve prior
  approved content and checkpoints resume without duplicate records.
- [ ] Every published field/tag has bounded evidence and review provenance. Human
  corrections survive automatic reprocessing; rollback restores the exact prior
  applicable revision or the unenriched base card.
- [ ] SSRF, redirect, traversal, large-file, secret/PII and prompt-injection fixtures
  fail closed. Repository content is never executed and normal requests never fetch it.
- [ ] Model/prompt/configuration provenance is complete or explicitly unverified;
  unknown values cannot be promoted as confident public facts.
- [ ] Search is bounded and literal; filters, stable ordering, pagination, invalid
  selections, no-results recovery and existing destinations work with mixed data.
- [ ] Query counts do not scale with card count; aggregate counts remain correct
  with multiple tags/votes; measured larger-fixture latency meets the owning issue's
  agreed budget on the supported database backends.
- [ ] Desktop 1440 and mobile 390/320, both themes, long names, empty/partial data,
  keyboard focus, 44px controls, contrast and reduced motion pass browser review.
- [ ] Dry-run, pilot, backfill resume, publication audit, bounded metrics/artifacts,
  pointer rollback and additive migration forward/backward paths have tests.

The follow-up issue must assign worker, publication, retention and independent
verification owners before enabling any fetch, inference or backfill.
