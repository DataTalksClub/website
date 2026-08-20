# Course-repository curriculum sync and encrypted homework plan

Status: planning complete; implementation not started by this document  
Owning issue: [DataTalksClub/website #218](https://github.com/DataTalksClub/website/issues/218)  
Target branch/worktree: `courses-work` / `/home/alexey/git/dtc-website/.tmp/courses-work`  
Worked content repository: `DataTalksClub/llm-zoomcamp`, cohort identifier `2026`

## Outcome

A registered course repository can publish a new Course/Cohort and its curriculum by merging a
validated commit to its configured branch. A module-format Cohort presents ordered Modules;
each Module presents ordered Markdown Units followed by exactly one Homework. Projects may occupy
their already-supported top-level positions in the same flow. A legacy Cohort keeps the existing
Homework and Projects presentation and may optionally declare `format: legacy` in source metadata.

Reusable lesson Markdown is not copied into every Cohort directory. Shared module folders remain
canonical in the course repository; Cohort metadata composes them and supplies cohort-specific
Homework. Sync projects the exact repository commit into separate Cohort-owned database rows.

Structured Homework answers are committed only as authenticated ciphertext. The trusted application
runtime may decrypt transiently for the existing answer-checking and scoring algorithms. Only a
human with the dedicated answer-decryption capability can receive plaintext from a management
surface. Plaintext is never returned to learners, ordinary staff, source APIs, logs, audits,
metrics, screenshots, or repository files.

## Authority and observed baseline

- `_docs/PROCESS.md` requires a groomed GitHub issue, engineer/tester/PM gates, focused tests,
  screenshots for rendering changes, and no engineer commit before acceptance.
- `_docs/specs/04-courses-and-cohorts.md` is the product authority for Cohort-owned curriculum,
  mixed `legacy`/`modules` operation, terminal Homework, project flow, and preservation of existing
  submission/scoring/peer-review logic.
- The current branch already has `Cohort.curriculum_format`, `Module`, `Unit`,
  `CurriculumFlowItem`, terminal Homework, project-scoped criteria, duplication, and module-flow
  rendering. `Unit` currently has title/slug/link only.
- `Question.correct_answer` is currently plaintext and is read by established answer-checking,
  scoring, statistics, and answer-reveal paths. This plan inserts one resolver at that boundary;
  it does not replace those algorithms.
- `content.ContentSource`, `content_sync.webhook_delivery`, `jobs.DurableJob`, revision/idempotency
  primitives, and redacted audit services already supply the repository identity, signed-delivery,
  durable retry, provenance, and fencing building blocks.
- AI Shipping Labs demonstrates the source-managed pattern: stable content IDs, root course YAML,
  module YAML, Markdown Units, exact source commit/path provenance, idempotent upsert, and read-only
  Studio fields. Its narrative Homework is inspiration only; DataTalks.Club keeps its existing
  scored Homework/Question domain.
- LLM Zoomcamp currently stores reusable lessons under top-level module folders such as
  `01-agentic-rag/lessons/` and Cohort-specific Homework under `cohorts/2026/`.

## Scope

1. Define and validate the course-repository YAML/Markdown contract.
2. Add a commit-pinned source adapter and durable automatic sync for registered course repositories.
3. Atomically create or update Course, Cohort, Module, Unit, Homework, and Question definitions
   through `courses` application services.
4. Render source Units as first-class course pages and retain the existing Homework destinations.
5. Store source-managed correct answers as encrypted envelopes and preserve legacy plaintext
   answers for existing DB-managed cohorts.
6. Add source provenance, source-managed read-only management behavior, safe answer reveal, sync
   diagnostics, reconciliation, migration, rollback, and serious automated/browser coverage.
7. Add LLM Zoomcamp 2026 source metadata as the first reviewed module-format content change after
   the platform contract is deployed.

## Non-goals

- No rewrite of Homework submission, answer persistence, score calculation, project submission,
  peer assignment, peer review, voting, result, leaderboard, or certificate algorithms.
- No automatic conversion of every existing Cohort. Existing rows remain DB-managed `legacy` unless
  a reviewed source binding explicitly adopts them.
- No cross-repository module references, Git submodules, symlinks, remote Markdown URLs, runtime
  GitHub reads, or learner-triggered sync.
- No generic LMS interchange format or cross-Cohort database curriculum object.
- No source-authored enrollments, registration campaigns, learner state, scores, project reviews,
  or certificates.
- No source creation of Project definitions in this issue. `cohort.yaml` may place an existing
  Project slug in flow; Project authoring remains in the existing management boundary.
- No promise that encryption protects answers after the application runtime or answer key is
  compromised. Key holders and the scoring runtime are inside the trust boundary.

## Two-repository ownership

| Concern | Course-content repository | Django website repository |
| --- | --- | --- |
| Course identity/content | Root `course.yaml` | Validate and project to `courses.Course`; operational relations remain DB-owned |
| Cohort identity/schedule/format/publication intent | `cohorts/<identifier>/cohort.yaml` | Validate and project to `courses.Cohort`; enforce uniqueness/lifecycle/visibility |
| Shared module definition | Top-level `<module>/module.yaml` | Materialize one Cohort-owned `Module` for every Cohort reference |
| Unit body/order | Canonical top-level Markdown plus explicit entries in `module.yaml` | Sanitize/render and materialize Cohort-owned `Unit` rows and Unit URLs |
| Cohort Homework | `cohorts/<identifier>/<module>/homework.yaml` plus optional instructions Markdown | Upsert existing `Homework`/`Question` definitions; preserve submission/scoring relations |
| Correct answers | AES-GCM envelope only | Keyring, transient resolver, scoring adapter, admin-only reveal, audit redaction |
| Projects/rubrics | Not added by this contract | Existing DB/Studio services; source flow may reference an existing Project slug |
| Learner/operational state | Never | Enrollment, submissions, answers, states, scores, reviews, statistics, campaigns |
| Sync/publish | Merge/push valid files to configured branch | Authenticate webhook, fetch exact commit, validate, activate atomically, retry/reconcile |
| Schemas/tooling | Content PR conforms to versioned contract and runs validation | Own schemas, parser versions, validator, dry-run command, and diagnostics |

Source-owned fields become read-only in Studio/admin. DB-owned operational fields remain editable
and are never overwritten by sync. A source-managed object always shows its repository, exact
commit, and path plus an “Edit on GitHub” action.

## Canonical source layout

`<identifier>` is a slug-like Cohort route identifier, not necessarily a year. `2026` is the worked
example; `spring-2027` is also valid and would live under `cohorts/spring-2027/`.

```text
llm-zoomcamp/
├── course.yaml                              # new: reusable Course authority
├── 01-agentic-rag/
│   ├── module.yaml                          # new: shared module/unit manifest
│   └── lessons/
│       ├── 01-intro.md                      # existing canonical Unit body
│       └── 02-environment.md
├── 02-vector-search/
│   ├── module.yaml
│   └── lessons/...
└── cohorts/
    └── 2026/
        ├── cohort.yaml                      # new: Cohort format/composition
        ├── README.md                        # existing human-facing overview
        └── 01-agentic-rag/
            ├── homework.md                  # narrative instructions, no answer key
            └── homework.yaml                # new: structured questions/ciphertext
```

### What stays shared

- Lesson Markdown and module ordering metadata are canonical in top-level module folders.
- `module.yaml` paths are relative to its own directory and explicitly list Units; no implicit
  recursive scan or glob controls public order.
- Multiple Cohorts may reference the same `module.yaml`. Sync reads the shared files once per
  source commit and materializes separate Module/Unit rows for each Cohort. Source is not duplicated;
  database projection is intentionally Cohort-owned.
- A shared Unit body edit updates referencing source-managed Cohorts on the next accepted sync.
  Homework scoring identity is more restrictive: after submissions exist, question identity,
  type, options, points, answer envelope, and Homework slug cannot change through source sync.

### What remains cohort-specific

- Cohort identifier, year display metadata, title, dates, format, publication intent, and flow.
- Due dates, Homework form settings, structured questions, and encrypted correct answers.
- Project placement, because Projects and their learner lifecycle remain Cohort-owned.

## Versioned YAML contract

All mappings are strict: unknown keys fail validation. Every YAML file is UTF-8, uses
`schema_version: 1`, rejects custom tags/aliases, and is bounded by file bytes, node count, nesting,
string length, and list length. Stable `content_id` values are UUIDs and never change after publish.

### Root `course.yaml`

Expected LLM Zoomcamp path: `course.yaml`.

```yaml
schema_version: 1
content_id: 11111111-1111-4111-8111-111111111111
slug: llm-zoomcamp
title: LLM Zoomcamp
description_path: README.md
outcome: Build, evaluate, and monitor production-style LLM applications.
repository_url: https://github.com/DataTalksClub/llm-zoomcamp
docs_url: https://datatalks.club/docs/courses/llm-zoomcamp/
faq_url: https://datatalks.club/faq/llm-zoomcamp.html
hashtag: llmzoomcamp
published: true
```

Exactly one of `description` and `description_path` is allowed. Repository identity is checked
against the registered source; YAML cannot redirect sync to another repository.

### Shared `<module>/module.yaml`

Expected first LLM Zoomcamp path: `01-agentic-rag/module.yaml`.

```yaml
schema_version: 1
content_id: 21111111-1111-4111-8111-111111111111
slug: agentic-rag
title: Agentic RAG
units:
  - content_id: 31111111-1111-4111-8111-111111111111
    slug: introduction
    title: Introduction
    path: lessons/01-intro.md
  - content_id: 32222222-2222-4222-8222-222222222222
    slug: environment
    title: Environment
    path: lessons/02-environment.md
```

Unit `path` is explicit and relative to the module manifest. Title is explicit so H1 edits do not
silently rename navigation. The Markdown H1 may match but is not identity.

### Module-format `cohorts/2026/cohort.yaml`

```yaml
schema_version: 1
content_id: 41111111-1111-4111-8111-111111111111
course: llm-zoomcamp
identifier: "2026"
legacy_slug: llm-zoomcamp-2026
year: 2026
title: LLM Zoomcamp 2026
description: The 2026 live delivery of LLM Zoomcamp.
format: modules
published: true
start_date: 2026-06-08
end_date: 2026-08-17
flow:
  - module:
      source: 01-agentic-rag/module.yaml
      homework: cohorts/2026/01-agentic-rag/homework.yaml
  - module:
      source: 02-vector-search/module.yaml
      homework: cohorts/2026/02-vector-search/homework.yaml
  - project: project-01
```

Paths in `cohort.yaml` are repository-root-relative. A module item requires exactly one Homework.
A project item refers to an already-existing Project slug in the same Cohort. Positions are list
order; duplicate module/project targets fail.

### Explicit legacy `cohorts/<identifier>/cohort.yaml`

```yaml
schema_version: 1
content_id: 42222222-2222-4222-8222-222222222222
course: llm-zoomcamp
identifier: "2025"
legacy_slug: llm-zoomcamp-2025
year: 2025
title: LLM Zoomcamp 2025
description: The 2025 delivery of LLM Zoomcamp.
format: legacy
published: true
start_date: 2025-05-05
end_date: 2025-07-14
```

For `format: legacy`, `flow` is absent and module/homework source rows are not imported. The file
may create or source-bind Course/Cohort metadata, but existing DB Homework, Projects, submissions,
and presentation remain untouched. Existing Cohorts with no metadata also remain legacy and
DB-managed.

### Cohort Homework YAML

Expected first LLM Zoomcamp path: `cohorts/2026/01-agentic-rag/homework.yaml`.

```yaml
schema_version: 1
content_id: 51111111-1111-4111-8111-111111111111
slug: hw1
title: Homework 1: Agentic RAG
instructions_path: homework.md
due_at: 2026-06-22T21:59:00Z
initial_state: open
form:
  homework_url: true
  time_spent_lectures: true
  time_spent_homework: true
  faq_contribution: true
  learning_in_public_cap: 7
questions:
  - content_id: 61111111-1111-4111-8111-111111111111
    id: lesson-page-count
    type: multiple_choice
    prompt: How many lesson pages are in the dataset?
    options:
      - id: pages-24
        label: "24"
      - id: pages-72
        label: "72"
      - id: pages-240
        label: "240"
      - id: pages-720
        label: "720"
    points: 1
    answer:
      version: 1
      algorithm: A256GCM
      kdf: HKDF-SHA256
      key_id: hw-answer-2026-01
      salt: <base64url-32-random-bytes>
      nonce: <base64url-12-random-bytes>
      ciphertext: <base64url-ciphertext-with-16-byte-tag>
      context_sha256: <lowercase-sha256>
  - content_id: 62222222-2222-4222-8222-222222222222
    id: implementation-language
    type: free_form
    answer_type: exact_string
    prompt: Which language is used for the examples?
    points: 1
    answer:
      version: 1
      algorithm: A256GCM
      kdf: HKDF-SHA256
      key_id: hw-answer-2026-01
      salt: <base64url-32-random-bytes>
      nonce: <base64url-12-random-bytes>
      ciphertext: <base64url-ciphertext-with-16-byte-tag>
      context_sha256: <lowercase-sha256>
```

Supported question types map to current behavior:

| YAML | Existing type | Required fields |
| --- | --- | --- |
| `multiple_choice` | `MC` | two or more stable options, encrypted selected option ID |
| `checkboxes` | `CB` | two or more stable options, encrypted non-empty selected option-ID list |
| `free_form` | `FF` | `answer_type`, encrypted scalar unless `any` |
| `free_form_long` | `FL` | `answer_type`, encrypted scalar unless `any` |

`answer_type` values are `any`, `float`, `integer`, `exact_string`, and `contains_string`, mapped
to the existing enums. For `any`, `answer` is absent. Choice answers encrypt stable option IDs, not
one-based positions; the resolver maps IDs to the existing index representation so option display
order can be validated without changing scoring behavior.

`initial_state` is create-only. Sync never changes an operational Homework from open to scored or
back. Due date/form fields may update only while the existing lifecycle permits it.

## Path and source validation

- Resolve with POSIX semantics inside the verified checkout. Reject absolute paths, `..`, empty
  segments, backslashes, NUL/control characters, URL schemes, case-colliding names, symlinks, and
  paths whose real target leaves the checkout.
- Unit sources must be `.md`; module/course/cohort/homework manifests must be `.yaml` at their
  prescribed locations. Referenced instructions must be `.md` beside the Homework YAML.
- No globs determine order. Every Unit, Module, Homework, and Project flow reference is explicit.
- Verify the full 40-character commit belongs to the configured repository and is reachable from
  the configured branch at receipt or reconciliation time. Parse that commit, never moving HEAD.
- Enforce source-level file/byte/node/list/string limits before Markdown rendering. YAML uses a
  safe loader with aliases/custom tags disabled.
- Render Markdown through the code-owned sanitizer. Rewrite only validated relative links/assets;
  never fetch arbitrary URLs during validation or a public request.
- Enforce unique stable IDs/slugs, folder identifier equals `cohort.identifier`, Course identity
  matches root `course.yaml`, same-Cohort ownership, exact terminal Homework coverage, and
  exactly-once Module/Project flow targets.
- Decrypt every non-`any` answer during candidate validation using the declared key ID, validate
  canonical plaintext shape and option references, then discard plaintext. Missing key, invalid
  tag, malformed payload, or context mismatch invalidates the entire candidate.
- Forbid plaintext answer keys such as `correct_answer` or `answer_value` through the strict schema.

## Answer encryption and key management

### Envelope construction

Use the already-locked `cryptography` package:

1. Canonical context bytes are UTF-8 for
   `dtc-homework-answer:v1\0<course-slug>\0<homework-slug>\0<question-id>`.
2. Generate 32 random bytes as envelope salt and 12 random bytes as AES-GCM nonce.
3. Compute HKDF salt as `SHA256("dtc-hw-kdf-salt:v1\0" + random_salt + context)`.
4. Derive a 32-byte key from the selected root key with HKDF-SHA256 and
   `info = "dtc-homework-answer-key:v1\0" + context`.
5. Canonicalize plaintext as compact sorted-key UTF-8 JSON:
   `{"option_ids":[...]}` for choice questions or `{"value":"..."}` for free form.
6. Encrypt with AES-256-GCM using the random nonce and the canonical context as associated data.
   Store ciphertext with its 16-byte tag, random salt, nonce, key ID, and context checksum.

The public slugs/ID are binding context, not secret entropy. Random salt gives each envelope
independent derivation; random nonce prevents same-answer ciphertext equality. Context is used in
both HKDF and authenticated associated data, so copying an envelope to another course/homework/
question or renaming one of those identifiers fails closed.

### Keyring

- Use a dedicated `COURSE_HOMEWORK_ANSWER_KEYRING`, never Django `SECRET_KEY`, a database field,
  repository secret, CI variable visible to content PRs, or request parameter.
- Development/production receive the keyring through the approved AWS Secrets Manager/ECS secret
  boundary. The value contains `active_key_id` plus base64-encoded 32-byte keys by ID.
- Test settings use an explicit test-only key. Local development fails closed for decrypt/encrypt
  unless an explicit local test key is configured; the key is never committed.
- Web and worker runtime need decrypt access because existing submission checks/scoring execute
  there. This is trusted service access, not permission for a human to reveal plaintext.
- Human reveal requires `courses.homework_answer.decrypt`, an attributed actor, CSRF, POST,
  explicit confirmation/reason, private/no-store/noindex response, and an audit containing only
  actor, Course/Homework/Question IDs, key ID, outcome, request ID, and reason code.
- The management list/detail/API/OpenAPI/export never serializes plaintext. Ordinary course staff
  see “encrypted, key <id>”; ciphertext itself need not be shown in routine management UI.

### Authoring and rotation

- Provide an admin-only command that accepts plaintext from standard input (never a CLI argument),
  outputs only the envelope, and emits no plaintext logs. A Studio helper may provide the same
  one-time result under the decrypt/encrypt capability and no-store controls.
- A course maintainer places the returned envelope in `homework.yaml`; plaintext scratch files live
  only under local `.tmp/` and are destroyed through the normal safe local workflow.
- Rotation adds a new key ID, makes it active for new encryption, re-encrypts source envelopes in a
  focused content-repository change, activates that commit, confirms no active/source-managed DB
  envelope references the old ID, then removes the old key. Never overwrite a key under an existing
  ID.
- Changing Course slug, Homework slug, or Question ID requires explicit decrypt/re-encrypt before
  the rename candidate can validate. IDs with learner history are otherwise immutable.

### Threat model

Protected against:

- public repository readers, source archives, and source-only backups learning the answer key;
- ciphertext/path/identity tampering going undetected;
- copying a ciphertext envelope to another declared question context;
- ordinary staff/API/browser/log/audit exposure;
- accidental key reuse across question contexts and key rotation ambiguity.

Not protected against:

- compromise of the ECS runtime, root key, Secrets Manager access, or a capable administrator;
- learners discovering low-cardinality answers through the normal correctness oracle or collusion;
- malicious authorized source and key holders coordinating to publish a new valid answer;
- screenshots/clipboard capture after an authorized one-time reveal. The UI warns and audits but
  cannot control the administrator's endpoint.

## Data model and service boundary

### Additive source provenance

Use nullable source fields so all existing rows remain DB-managed:

- `Course`: source content UUID, source stable ID/path/commit/checksum.
- `Cohort`: source content UUID, source path/commit/checksum and source-managed publication state.
- `Module`: source content UUID/path/commit/checksum; uniqueness scoped to Cohort.
- `Unit`: source content UUID/path/commit/checksum, source Markdown, sanitized HTML, publication
  flag; uniqueness scoped to Module.
- `Homework`: source content UUID/path/commit/checksum, instructions Markdown/sanitized HTML;
  uniqueness scoped to Cohort.
- `Question`: source content UUID, stable `source_question_id`, source path/commit/checksum, and
  `answer_envelope` JSON. Existing `correct_answer` remains for legacy/DB-managed questions.

Add `CourseCurriculumImportRun` in `courses` with UUID, scalar source UUID/stable ID, repository/
branch snapshots, commit, schema/parser versions, state, manifest checksum, bounded redacted
diagnostics/counts, timestamps, and unique `(source, commit, parser_version)`. It stores no raw
Markdown, ciphertext, plaintext, credentials, or learner data.

No `courses` model imports `content`. `content.ContentSource` remains the generic configured
repository boundary; source/release identifiers cross into `courses` as validated scalar values.
Update `_docs/architecture/app-boundaries.md` to allow `content_sync` adapters to call `courses`
application services while preserving `content_sync -> domains`, never domains -> sync.

### Source-owned versus operational fields

- Source owns Course/Cohort identity and descriptive fields, format, dates, composition, Module/
  Unit bodies/order, Homework definition/form/due date, and Question definition/answer envelope.
- DB owns enrollments, campaigns, registration, Homework operational state after creation,
  submissions, learner Answers, correctness results, scoring/statistics, Project definitions and
  lifecycle, reviews, votes, leaderboards, complaints, certificates, and communications.
- Sync never overwrites DB-owned fields. Source-managed fields cannot be edited through admin/API.

### Import service

`content_sync.course_repositories` verifies/fetches/parses and emits an immutable command graph.
`courses.services.curriculum_import` is the only mutation boundary. In one transaction it:

1. locks the source/import and relevant Course/Cohort rows;
2. upserts Course by source content UUID, then checks slug collision/adoption;
3. upserts Cohort by source content UUID, then checks `(Course, identifier)`/legacy slug collision;
4. for `legacy`, stops after metadata and leaves curriculum rows untouched;
5. for `modules`, upserts Modules/Units/Homework/Questions by stable source identity;
6. resolves existing Project slugs and rebuilds flow through existing constraints;
7. validates protected changes against submissions/reviews/history;
8. marks removed source objects unpublished only when safe; referenced historical objects are
   retained and the candidate is rejected if removal would hide or corrupt learner history;
9. records redacted audit/provenance and marks the import active;
10. commits all changes together, then invalidates relevant caches after commit.

An exception rolls back every Course/Cohort/curriculum mutation. Re-running the same source commit
and parser version is a no-op with the same object IDs and learner foreign keys.

### Answer resolver

Add one `resolved_correct_answer(question)` service:

- legacy/DB-managed Question: return current `correct_answer` exactly;
- source-managed encrypted Question: derive/decrypt/validate transiently and return the existing
  canonical string/index form expected by answer checks;
- malformed/missing key: stable safe failure, no partial submission/scoring update.

Route `get_correct_answer*`, answer checking, answer reveal, statistics, and scoring through this
boundary. Do not fork scoring by curriculum format.

## Automatic publish and sync lifecycle

### Registration

Register one enabled `content.ContentSource` for each approved course repository:

- owner/name/branch and immutable stable ID;
- adapter type `course_repository_v1`;
- path contract rooted at `course.yaml`, `cohorts/**`, and explicitly referenced shared modules;
- file/byte/freshness limits;
- GitHub webhook and GitHub App secret references;
- `auto_activate: true` for the requested behavior.

Arbitrary repository URLs from YAML/API are never cloned. Source registration is a privileged,
audited management action.

### Trigger and authentication

1. GitHub sends a `push` webhook for the configured branch.
2. The endpoint reads the bounded raw body and reuses
   `content_sync.webhook_delivery.authenticate_and_fence_webhook_delivery` with
   `X-Hub-Signature-256` and `X-GitHub-Delivery`.
3. It requires event `push`, exact configured owner/repository/ref, full `after` SHA, and a
   non-deletion push. Signature is verified before JSON interpretation.
4. In one transaction it fences delivery/body digest and enqueues one DurableJob with only source
   UUID, commit SHA, delivery record UUID, and safe correlation ID. Response is `202`; no repository
   fetch or domain mutation occurs in the request.

Invalid signature/repository/branch/body is rejected with a stable 4xx code and no job. Exact
delivery replay returns the prior accepted result. Same delivery ID with a different body is a
conflict and never executes.

### Worker, idempotency, and ordering

- Job deduplication key is `course-source:<source-id>:<commit>:<parser-version>`; payload hash
  prevents key reuse with different inputs.
- Acquire a per-source lease. Fetch the exact commit using a least-privilege GitHub App installation
  token, verify repository identity and branch reachability, and store no token/archive outside
  project-local worker scratch lifetime.
- Validate the entire registered repository candidate. One invalid managed Cohort invalidates the
  candidate; the previous active projection remains unchanged.
- `CourseCurriculumImportRun` and stable source IDs make repeated webhook, manual re-sync,
  reconciliation, and worker retry idempotent.
- Serialize activation per source. If a newer commit is queued while one runs, set a durable
  follow-up and process the newest desired commit after completion. A late older job cannot replace
  a newer active source sequence.
- On successful activation with `course.yaml published: true` and Cohort `published: true`, create
  or update Course/Cohort and set their public visibility in the same transaction. The Course then
  appears on `/courses`, its family page, and `/courses/<course>/<cohort>` without a manual DB step.
- `published: false` creates/updates a draft/hidden source projection. Omission defaults fail closed
  to unpublished; automatic appearance therefore requires explicit publication intent.

### Failure, retry, and reconciliation

- Retry bounded transient network/GitHub/database lease errors with durable exponential backoff and
  jitter. Preserve attempt count and safe error code. Ambiguous activation is reconciled by import
  uniqueness and active commit before retry.
- Schema/path/ownership/crypto/history failures are terminal `invalid`, not retried. Record bounded
  file path, JSON pointer, and stable reason code without file bodies, envelopes, plaintext, or keys.
- Missing key ID is terminal and alerts operators; it never falls back to plaintext or another key.
- Keep the prior active commit and public rows on every failure. A broken push cannot blank a Course.
- A scheduled reconciliation compares configured branch HEAD with last successful/desired commit
  and enqueues a missed follow-up. It also reports freshness age, queue depth, duration, invalid
  candidates, retry exhaustion, and active commit.
- Studio exposes safe source/run status, counts, commit/path links, retry/resync, and diagnostics.
  Manual resync is authenticated, CSRF-protected, idempotent, revision-guarded, audited, and calls
  the same durable path.

## Public URLs and templates

- Legacy Cohort pages remain byte/behavior compatible for their Homework and Projects sections and
  contain no Module/Unit UI.
- Module Cohort page remains `/courses/<course>/<cohort>` and renders one ordered flow. A Module
  lists Unit links, then its existing Homework action/status/deadline row; UI copy says “Homework”,
  not internal “terminal homework”. Projects retain existing destinations in configured positions.
- Unit canonical route:
  `/courses/<course>/<cohort>/modules/<module>/<unit>`.
- Unit page uses existing Course/Cohort design primitives, safe rendered Markdown, breadcrumbs,
  Module context, previous/next Unit navigation, and a next action to the module Homework after the
  final Unit. No new submission logic lives on Unit pages.
- Existing Homework, Project, peer-review, result, dashboard, and compatibility routes do not
  change. Source metadata does not create a parallel URL tree.
- Unit/public Course responses use established anonymous cache classification; learner-sensitive
  Homework/submission/review and all management/decryption/sync pages remain private/no-store/
  non-indexable.
- A sync failure does not alter public output. Activation invalidates only affected Course/Cohort/
  Unit cache keys after commit.

## Admin and operator workflow

1. Register/enable an approved repository source and webhook secret through the management service.
2. Maintainer edits source YAML/Markdown. Source-managed fields display read-only with exact GitHub
   edit links; operational fields remain editable.
3. Admin encrypts answer plaintext through stdin-only CLI or protected one-time Studio helper and
   commits only envelopes.
4. Content-repository CI validates schema/paths/crypto envelopes against the target platform parser
   version using a test/decrypt availability check that has access only in the protected validation
   environment. Public fork CI must not receive the production key.
5. Merge to configured branch triggers sync. Studio shows queued/fetching/validating/active/invalid/
   failed state and active commit.
6. Authorized answer reveal is a separate confirmed POST. Bulk plaintext export does not exist.
7. Existing “fill/clear correct answers” actions reject source-managed encrypted questions; they
   remain available for DB-managed legacy questions.

## Migration and backfill

1. Add nullable provenance/content/envelope fields, Unit/instructions body fields, import-run model,
   constraints, and Unit route support. Existing rows remain unchanged and legacy.
2. Add the resolver while keeping plaintext `correct_answer` authoritative when no envelope exists;
   run all existing Homework/scoring characterization tests unchanged.
3. Add adapter/job/webhook/management surfaces with all course sources disabled.
4. Register LLM Zoomcamp as disabled and run a commit-pinned dry run. Compare Course/Cohort/Homework/
   Question counts, slugs, due dates, types, options, points, and existing learner FK checksums.
5. For an existing LLM 2026 row, use a reviewed adoption manifest mapping source content IDs to the
   exact existing Course/Cohort/Homework rows. Question adoption is allowed only with an explicit
   stable mapping and parity check; never infer solely by position/text. If no durable learner data
   exists, recreating local-only fixtures is allowed but is not the production migration strategy.
6. Merge/deploy website support first. Then merge the LLM Zoomcamp content PR and enable
   auto-activation. Confirm `/courses/llm-zoomcamp/2026`, Unit pages, Homework form/scoring, active
   commit, and safe management status.
7. Existing Cohorts with no metadata stay legacy. Adding explicit legacy metadata binds only after
   collision/adoption review and never imports/rewrites curriculum.

Rollback disables the source and points to/reapplies the last successful import commit without
deleting additive fields or history. A candidate may be rolled back before learner writes. Once
new submissions reference imported definitions, rollback reactivates the prior compatible source
graph or hides new unpublished objects; it never drops submissions/answers/scores. Destructive
migration reversal is refused when source-managed rows/history exist.

## Course-repository delivery expectations

The website process uses no PR, per `_docs/PROCESS.md`. The separate course repository follows its
own normal review policy and should use one reviewed PR linked to website #218 after website support
is deployed.

Recommended focused course-repository commits:

1. `Add source metadata for LLM Zoomcamp modules` — root `course.yaml` and top-level `module.yaml`
   files; no Cohort publication yet.
2. `Describe the LLM Zoomcamp 2026 curriculum` — `cohorts/2026/cohort.yaml` with `published: false`
   for platform dry-run.
3. `Structure and encrypt LLM Zoomcamp 2026 homework` — cohort-local `homework.yaml` files and
   narrative Markdown split, with no plaintext answers.
4. `Publish the LLM Zoomcamp 2026 source curriculum` — set `published: true` only after protected
   validation against the deployed parser/keyring succeeds.

The PR records the website release/parser schema version and successful dry-run commit SHA. Branch
protection requires content-schema/path validation and forbids plaintext-answer keys. Production
answer keys are never available to untrusted fork workflows. Merge commit/push is the publication
event; no manual Course/Cohort creation follows a valid auto-activated push.

## Ordered implementation slices and focused commits

Each slice is independently testable and should become one focused commit only after the required
engineer/tester/PM lifecycle gates:

1. **Normative contract:** update spec/app boundary, add versioned schemas and worked fixtures.
2. **Crypto boundary:** keyring parsing, envelope encrypt/decrypt/resolver, admin permission/audit,
   legacy scoring parity.
3. **Source provenance model:** additive fields/import-run model/migration/backward-fresh tests.
4. **Parser:** repository verification, YAML/Markdown parsing, strict validation, LLM fixture.
5. **Import service:** transactional upsert, legacy metadata, module materialization, protected
   history changes, idempotency/rollback.
6. **Durable sync:** signed webhook adapter, job registry/leases/retry/order/reconciliation,
   Studio diagnostics/resync.
7. **Public Units:** Unit route/view/template/navigation and module-flow links; desktop/mobile/a11y.
8. **Management/API:** source-managed read-only fields, answer reveal, import status, OpenAPI and
   permission/redaction parity.
9. **LLM content repository:** separate reviewed PR/commits above; activate only after platform pass.

## Acceptance criteria

### Source contract and ownership

- [ ] A strict versioned schema validates root Course, explicit legacy Cohort, module Cohort,
  shared Module/Unit, and cohort Homework files; unknown keys, aliases/tags, unsafe paths, limits,
  collisions, and malformed cross-references fail with bounded diagnostics.
- [ ] LLM Zoomcamp 2026 uses the exact documented source layout: shared Markdown remains canonical
  under top-level module folders, `module.yaml` orders it, `cohorts/2026/cohort.yaml` composes it,
  and cohort-local `homework.yaml` defines terminal Homework without copying lesson bodies.
- [ ] Two Cohorts referencing one shared Module source create isolated Cohort-owned DB rows with the
  same source content IDs/checksums and no duplicated Markdown file in the course repository.
- [ ] `format: legacy` metadata creates/updates metadata only, publishes no module flow, and leaves
  existing Homework/Project/learner behavior unchanged; Cohorts without metadata remain legacy.

### Encryption and scoring

- [ ] Every non-`any` source Question contains only a versioned AES-256-GCM/HKDF-SHA256 envelope;
  strict schema rejects plaintext answer fields.
- [ ] Derived key/context includes random salt plus Course slug, Homework slug, and Question ID;
  random nonce is 12 bytes; copying, relabelling, bit-flipping, wrong key ID, wrong context, malformed
  plaintext, or missing key fails closed.
- [ ] Multiple-choice, checkbox, free-form exact/contains/numeric/any and long-form characterization
  tests produce the same correctness and score as legacy plaintext Questions.
- [ ] Trusted runtime decrypts only transiently. Only `courses.homework_answer.decrypt` can return
  plaintext to a human; ordinary staff, learners, public/admin APIs, exports, logs, audits, metrics,
  errors, and screenshots expose neither plaintext nor key material.
- [ ] Key rotation supports overlapping key IDs, re-encryption, reference inventory, and safe old-key
  retirement; identifier rename requires re-encryption.

### Import and mixed-format behavior

- [ ] Exact-commit import creates/updates Course/Cohort/Module/Unit/Homework/Question by stable source
  identity, preserves PKs and learner FKs, does not overwrite DB-owned state, and is a no-op on exact
  replay.
- [ ] A source deletion/change that would invalidate submissions, answers, scores, reviews, or
  history is rejected atomically; safe unpublished objects are archived/unpublished without cascade.
- [ ] One Course concurrently serves legacy and module Cohorts without source/order/criteria/cache/
  permission leakage.
- [ ] Existing project submission, peer assignment, review, voting, scoring, result, and leaderboard
  characterization outputs remain unchanged; source integration is confined to import/lookup/render
  adapters.

### Automatic publication and resilience

- [ ] A valid signed push for a registered branch fences `X-GitHub-Delivery`, enqueues one durable
  exact-commit job, and returns promptly; invalid signature/repository/branch/body and delivery-ID
  conflict execute no job or mutation.
- [ ] A valid new `course.yaml` plus `published: true` Cohort atomically creates/updates Course and
  Cohort and makes it appear on `/courses`, the Course family page, and its canonical Cohort URL
  without manual DB work.
- [ ] Replay, concurrent delivery, manual re-sync, reconciliation, worker retry, and out-of-order
  commits are idempotent and latest-source ordered; an older late job cannot replace a newer active
  projection.
- [ ] Transient failures retry with bounded durable backoff. Validation/crypto/history failures are
  terminal with redacted diagnostics. Every failure preserves the prior active public projection.
- [ ] Missed webhooks are reconciled from configured branch HEAD, and freshness/queue/duration/
  invalid/retry-exhaustion/active-commit observability has owners and runbook actions.

### URLs, management, accessibility, and rollout

- [ ] Module flow renders Units then one Homework per Module and in-flow Projects in exact order;
  there is no second Projects section and no internal “terminal” wording.
- [ ] Unit canonical pages render sanitized commit-pinned Markdown with Course/Cohort/Module
  breadcrumbs, previous/next navigation, final Homework action, correct cache/indexing policy,
  JS-disabled usability, keyboard/focus semantics, and mobile/200%-400% reflow.
- [ ] Existing Homework/Project/review/result URLs and legacy Course page output remain unchanged.
- [ ] Source-managed fields are read-only with exact GitHub provenance/edit links; operational fields
  remain editable; answer-fill/clear actions reject encrypted source Questions.
- [ ] Forward/fresh/idempotent migration, reviewed adoption, source-disable/last-good rollback, and
  destructive-reverse refusal tests pass with unchanged learner counts/PKs/checksums.
- [ ] Website support is deployed before the linked LLM Zoomcamp content PR is published; protected
  validation passes, no plaintext/key is in either diff, and the merged content commit auto-appears.

## Focused test and evidence plan

| Layer | Tests |
| --- | --- |
| Schema/parser | golden root/module/cohort/homework files; explicit legacy; arbitrary identifier; unknown/missing fields; YAML bombs/aliases; path traversal/symlink/case collisions; limits; duplicate IDs/slugs/order; broken references; unsafe Markdown |
| Crypto | fixed test vectors; random salt/nonce; all answer payloads; context rename/copy; bit flip; invalid tag/base64/key ID; keyring boot validation; rotation; no plaintext serialization/log/audit |
| Model/migration | forward/back/fresh/idempotent; nullable legacy rows; constraints; adoption mapping; protected history; destructive reverse refusal; SQLite/PostgreSQL portability |
| Import service | new Course/Cohort; existing source adoption; exact replay; changed shared Unit; two Cohorts sharing source; legacy no-op; module flow; project reference; atomic invalid candidate; removal with/without history; DB-owned field preservation |
| Homework regression | all current submission-validation, answer-check, score, statistics, reveal, dashboard, leaderboard, and admin-action suites for plaintext plus encrypted adapters |
| Webhook/job | GitHub HMAC vector; raw body; delivery replay/conflict; repo/ref/SHA validation; enqueue-after-commit; transient retry; terminal invalid; lease expiry; concurrent/out-of-order pushes; reconciliation; redacted diagnostics |
| API/management | capability positive/negative; object scope; no existence leak; read-only source fields; reveal POST/no-store/CSRF/audit; resync idempotency/revision; OpenAPI excludes answer plaintext |
| Django render | exact legacy contract; module order; shared Units; Unit page/nav; final Homework action; Project lifecycle links; 404/canonical/cache/noindex; query-count bounds |
| Playwright | desktop/mobile legacy and module Course pages; Unit reader; Homework submission/scoring; project flow/review; JS disabled; keyboard; focus; axe; 200%/400%; mixed-Cohort navigation/cache isolation |
| Course repository | schema/path/content-ID checks; protected answer-envelope validation; no forbidden plaintext keys; exact LLM 2026 manifest count/order; dry-run against PR SHA |

The engineer generates the repository selective-verification plan for the exact base/head and runs
every graph-required component. The independent tester recomputes it, reruns focused Django plus
core browser suites, exercises tampering/permissions, captures and inspects synthetic desktop/mobile
screenshots under `.tmp/screenshots/`, and records exact commits/counts/checksums without secrets.

## Rollout risks

| Risk | Control |
| --- | --- |
| Public push creates malformed Course | strict candidate validation, explicit `published`, atomic last-good activation |
| Shared module edit drifts historical Cohort | source provenance, protected-field rules, completed/history review, rollback to prior commit |
| Correct answer leaked | ciphertext-only source, separate keyring, capability reveal, redaction tests, no bulk export |
| Runtime cannot decrypt during submission | boot/key-ID checks, candidate decrypt validation, last-good activation, alert and safe no-write failure |
| Slug/ID rename breaks ciphertext | context checksum/tag, immutable IDs with history, mandatory re-encryption workflow |
| Webhook lost/duplicated/reordered | delivery fence, durable dedupe, per-source lease/sequence, scheduled reconciliation |
| Source sync overwrites scored state | explicit source/DB field ownership; `initial_state` create-only |
| Course repo and website deploy out of order | platform-first rollout, schema version gate, `published: false` dry run, final publish commit |
| Parser/path attack | verified exact checkout, no symlinks/`..`/remote fetch, strict sizes/nodes/extensions, sanitized Markdown |
| Content PR fork receives production key | no production secret in public CI; protected admin validation/encryption only |

## Decisions required before engineering resumes

1. Accept that trusted web/worker runtime decrypts transiently for existing scoring, while only the
   dedicated admin capability may reveal plaintext to a human. Strict “no non-admin process can
   decrypt” would require redesigning synchronous answer validation and is outside surface-level
   preservation.
2. Confirm shared Unit body edits update every active source-managed Cohort referencing that module,
   subject to source provenance and protected Homework/history rules.
3. Confirm Project definitions remain DB/Studio-owned in this issue; source metadata only places an
   existing Project slug in flow.
4. Confirm registered repositories use explicit `published: true` plus source `auto_activate: true`
   for automatic appearance, with no separate human publication click after a valid push.

