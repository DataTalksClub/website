# Shared course content: complete implementation handoff

Status as of 2026-09-07: **plan ready; website implementation and real course migrations not done**.
This is the handoff entry point for a new implementation agent. It supersedes the
earlier LLM-only rollout scope and includes Machine Learning Zoomcamp explicitly.
This is an engineering plan, not a claim that the live website or GitHub course
paths have changed. Normative specifications must be reconciled in W0 before code.

## Start here

Implement the entire project in bounded slices, not just the repository rename or
operations script. Read this document, `AGENTS.md`, `_docs/PROCESS.md`, and the
specifications linked below. Reinspect current files before editing: this plan
records a point-in-time investigation, and proposed files/commands may not exist yet.
Track website slices beneath [website issue #320](https://github.com/DataTalksClub/website/issues/320);
recheck issue state and groom each child before implementation. Follow independent
engineer/tester/PM gates. Do not commit, publish source changes, or deploy merely
because a fixture passes.

### Actual delivery status

- Done externally: GitHub repository `DataTalksClub/zoomcamp-template` renamed to
  `DataTalksClub/zoomcamp-ops`, and its local origin updated. This did **not** rename
  any lesson or move any course content.
- Implemented locally in sibling `../zoomcamp-ops`: bootstrap/archive script, skill,
  v2 checker, fixtures, tests, and authoring docs. These changes were uncommitted and
  unpushed at handoff. Preserve and review them; do not assume another checkout has them.
- Independent tooling verification: 33 pytest tests and 13 standalone v1 assertions
  passed at the tested snapshot. One release-blocking checker gap remains (O0 below).
  Those results are not website verification and must be refreshed after relevant edits.
- Website product code, database migrations, routing, SEO, and source imports for this
  design: **not implemented**. This handoff adds documentation only here.
- Real LLM and ML course source migration: **not applied or pushed**.
  Existing `cohorts/2026/...` GitHub links therefore still show the old organization.
- No website code from this work was committed, pushed, or deployed.

### Repository ownership

| Checkout / repository | Work |
| --- | --- |
| `dtc-website` / `DataTalksClub/website` | Parser, DB models/importer, shared pages, cohort operations/context, SEO, compatibility, migration verification |
| `../zoomcamp-ops` / `DataTalksClub/zoomcamp-ops` | Safe source tooling, skill, checker, schema docs, templates and workflow references |
| `../llm-zoomcamp` / `DataTalksClub/llm-zoomcamp` | Move current teaching tree to numbered root folders; preserve old GitHub archives |
| `../machine-learning-zoomcamp` / `DataTalksClub/machine-learning-zoomcamp` | Same migration, including the concrete regression lesson below |

Resolve checkout locations before use; do not assume a sibling exists. Inventory other
registered course consumers for compatibility, but do not bulk-migrate or rename them.
No additional repository rename is required by this plan.

## O0 — Finish and verify the operations foundation

Owner: operations engineer, then independent tester. Can run alongside W0.

Read these existing implementation files in `zoomcamp-ops`:

- `scripts/bootstrap-cohort/bootstrap_cohort.py` and its README;
- `scripts/bootstrap-cohort/test_bootstrap_cohort.py`;
- `scripts/bootstrap-cohort/test_contract_integration.py`;
- `scripts/check-zoomcamp/check_zoomcamp.py`;
- `scripts/check-zoomcamp/test_shared_curriculum.py`;
- `docs/shared-curriculum-v2.md`;
- `skills/bootstrap-zoomcamp-cohort/SKILL.md`;
- `.github/workflows/self-check.yml` and reusable workflow/template callers.

Fix the known disagreement: the producer rejects self-paced assignments, but the
checker accepts a manually authored self-paced cohort with homework and a real deadline.
In this first slice, **self-paced requires an explicit `homework: []`**. Reject nonempty
lists consistently in checker and website parser, with negative regression tests.
Do not implement self-paced grading or nullable assessment deadlines to bypass the rule.
Require the explicit homework list in v2 manifests and reconcile documentation/checker
omission behavior. The appended source contract records the intended final rule.

Run from `zoomcamp-ops`:

```bash
uv run --with pytest --with pyyaml pytest scripts/bootstrap-cohort/test_bootstrap_cohort.py scripts/bootstrap-cohort/test_contract_integration.py scripts/check-zoomcamp/test_shared_curriculum.py --basetemp .tmp/cohort-handoff-tests -q
uv run --with pyyaml scripts/check-zoomcamp/test_check_zoomcamp.py
```

Independent acceptance must exercise preview/apply/replay for live (including a full
authored assignment), self-paced, and archive operations; unchanged root bytes; fresh
cohort/assignment IDs; preserved historical homework; complete archive links/assets;
collision/path/symlink refusal and failure rollback; and checker rejection of manually
authored self-paced homework. Read the skill as a fresh agent and forward-test it on a
disposable fixture. Refresh file digests and evidence after fixes. Prior report, if
available locally: `.tmp/course-content-ops-verification.md`; it is optional context,
not a dependency for understanding or reproducing this handoff.

The producer deliberately refuses v1 course inputs. Do not run it on a real v1 course
and describe that refusal as a completed migration. The consumer/source conversion
work below is still required. Review reusable workflow callers after the repository
rename; publish a tested operations commit before pinning consumers to it.

## Scope corrections and execution safeguards

These clarifications apply throughout the detailed tasks below:

1. Apply W7/W8 to **both LLM and Machine Learning Zoomcamp**, separately rehearsed
   and reviewed. The earlier detailed ticket uses LLM examples; they are not a scope
   exclusion for ML.
2. Existing course family slugs are preserved unless explicitly approved otherwise.
   `llm-zoomcamp` is required; do not guess ML's website slug from its GitHub repo name.
3. Current curriculum is the complete root module graph, numerically ordered.
   Per-cohort rows bind optional homework; they do not create divergent lesson lists
   or custom teaching order. No annual or self-paced copies of current lessons.
4. Self-paced phase one is reading, shared progress, and ungraded practice only.
   An approved future grading extension is outside this implementation.
5. Use synthetic/content-only databases with production-like schema for rehearsals.
   References below to a “production-like copy” do not authorize reading or copying
   real production learner data.
6. Source aliases need a concrete validated manifest representation in W0/W1 before
   W3/W8 depend on them. Do not invent an unrecognized YAML field during source moves.
   Freeze this extension across producer/checker/parser tests, or use an explicit
   reviewed migration mapping ingested into the DB alias table.
7. Optional module overview/group metadata is not permission to invent schema fields.
   Import README as a separately specified overview if supported; defer grouping
   beyond the frozen contract until its exact field format is agreed and tested.
8. W10 contains staging/release readiness gates. Deployment and real source publication
   remain separately controlled operations under the repository process. Do not report
   production completion until the actual source commits, consumer deployment and
   live checks are recorded. Never put a v2 source commit live before its consumer.

## W8-ML — Move the ML current tree as well

Dependencies: the same W1/W7 gates as W8, validated against an ML-specific fixture.
Owner: ML source engineer, coordinated with the website importer and independent tester.

The user specifically expects this current source path to move:

```text
cohorts/2026/02-regression/15-using-model.md
  -> 02-regression/15-using-model.md
```

This is a real file move within `DataTalksClub/machine-learning-zoomcamp`, not the
`zoomcamp-template` repository rename. Inventory the full module trees and all
references first. Move all current lesson manifests, code, notebooks, images and
supporting dependencies coherently; preserve numeric prefixes, slugs, stable IDs
and meaningful authored README prose. Resolve existing root-file collisions by
reviewed comparison, never overwrite them blindly.

Keep cohort-specific homework, deadlines, projects, leaderboard-related configuration
and cohort metadata under `cohorts/2026/`. Map homework explicitly. Historical cohorts
with different material remain complete GitHub-only archives and are not merged by
matching filenames. Inventory which cohorts actually need archives; do not invent
missing historical curricula. A current 2026 cohort can refer to root lessons without
keeping a second full lesson copy. Future curriculum replacement archives the outgoing
material before changing the root and reviews every affected live/self-paced delivery.

Update the root README, module indexes, cohort README, cross-module links, and
source manifests. Produce a reviewed old-to-new map, including this exact lesson.
GitHub does not provide configurable per-file redirects: old branch-tip blob URLs
may stop resolving after a move. Record the pre-move commit so historical file links
remain available at an immutable SHA; provide cohort index guidance and update known
links. Do not retain a complete current lesson copy solely to mimic redirects.
Website old URLs have their separate explicit alias policy below.

Acceptance: a fresh checkout of the published ML commit contains the root lesson;
root/cohort indexes and all local code/image/notebook references work; the checker
and website importer accept the exact commit; live/self-paced share the same DB
lesson identity; old homework/history remain attached to their original cohort.
Report the full source SHA and new GitHub path after publication, not merely the
local planned path. Repeat equivalent evidence for LLM before declaring both migrated.

## Implementation order and final completion checklist

O0 can run with W0. Execute W1, W2, W3, W4, W5, W6, W7 in dependency order;
W2 depends on W1's frozen shape (not independently of it). W8-LLM and W8-ML may
be rehearsed in parallel after their own gates; they must not share editable files.
W9 integrates real producer fixtures with the website. W10 gates cutover.
A coordinating agent can delegate bounded slices, with isolated worktrees and
explicit file ownership; workers must preserve other agents' edits.

- [ ] O0 checker/producer/skill verification passes without the known policy gap.
- [ ] W0-W6 website specs, implementation, focused tests and compatibility are accepted.
- [ ] W7 separate LLM/ML rehearsals preserve stable IDs, progress and cohort operations.
- [ ] W8-LLM and W8-ML current source trees are moved, validated, and published through gates.
- [ ] W9 live/self-paced bootstrap output is consumed without duplicate lesson rows.
- [ ] W10 independent browser/technical/PM acceptance and authorized deployment complete.
- [ ] Live evidence shows shared numbered URLs, correct cohort homework links for
      anonymous/single/multiple enrollment, GitHub-only archive notices, and clean canonicals.
- [ ] Final handoff records commits per repository, deployment/import status,
      exact checks, old/new path examples and rollback instructions.

The remaining sections contain detailed website models, interfaces, files, commands,
acceptance criteria and browser scenarios, followed by the source contract. They are
included here so another agent does not need this conversation or scratch research.

---

# Detailed website work packages

Status: detailed implementation plan. Website tasks below are pending; local operations tooling is implemented separately. Each website slice still requires its groomed issue and independent delivery gates.

All file paths and commands in this document are relative to the website
checkout (`/home/alexey/git/dtc-website`) unless a different repository is named.
New files and CLI entry points listed under a ticket must be implemented by that
ticket before its example verification commands can run. Operations commands
use the separate bootstrap README. Do not execute source conversion or database
commands against production while working through this plan.

Scope: the website side of the LLM Zoomcamp curriculum move, plus the contracts that
the source-repository and operations work must consume. The source repository remains
an external dependency; the cohort bootstrap script and reusable skill are owned by the
parallel operations work.

## Decisions that unblock engineering

These are the decisions to implement unless the owner changes the umbrella issue.

1. **Canonical public hierarchy.** Keep the existing family slug `llm-zoomcamp`.
   Current, shared teaching routes are:

   ```text
   /courses/llm-zoomcamp
   /courses/llm-zoomcamp/01-agentic-rag
   /courses/llm-zoomcamp/01-agentic-rag/01-lesson
   ```

   `01-lesson` is the route-shape fixture; the source's descriptive numbered slug,
   such as `01-retrieval-augmented-generation`, is used in production. The route has
   no `modules`, `lessons`, year, or cohort segment.

   Delivery and operations are explicitly namespaced:

   ```text
   /courses/llm-zoomcamp/cohorts/2026
   /courses/llm-zoomcamp/cohorts/2026/homework/<homework-slug>
   /courses/llm-zoomcamp/cohorts/2026/leaderboard
   /courses/llm-zoomcamp/cohorts/2026/dashboard
   /courses/llm-zoomcamp/cohorts/2026/projects
   /courses/llm-zoomcamp/cohorts/2026/calendar.ics
   ```

   Cohort identifiers are stable slug-like values (`2026`, `spring-2027`,
   `self-paced`); `year` is schedule/display metadata and never the identity.

2. **One current curriculum, not a website version viewer.** Add one database-owned
   current curriculum graph per `Course`: shared modules and lessons are stored once,
   while a cohort stores only its placement/order and optional terminal homework
   mapping. Do not add a public release/version selector or a historical lesson viewer.
   This deliberately supersedes the historical-release proposal in the research notes
   and follows the direct-sync direction in `open-decisions.md` §1. A previous import
   may remain as inactive database rows for rollback/audit, but no route may select it.

3. **Assessment ownership remains cohort-specific.** `Homework`, questions, projects,
   submissions, scores, peer review, leaderboard state, certificates, deadlines, and
   enrollments remain attached to one `Cohort`. A shared lesson links to a selected
   cohort's homework only through an explicit mapping by stable IDs. Never infer an
   assignment from matching numbers, titles, or slugs.

4. **Self-paced phase-one policy.** A v2 manifest with
   `delivery=self_paced` and `curriculum=current` is a first-class context for
   reading, shared lesson progress, and ungraded practice links. Delivery is separate
   from curriculum source: `delivery` has only `live` and `self_paced`; an archive is
   represented by `curriculum=github_archive`, not by an invented archive delivery
   value. Self-paced reading does not invent a deadline, leaderboard, peer-review
   availability, score, certificate, or annual schedule. Do not make
   `Homework.due_date` nullable or alter scoring in this slice. A self-paced cohort
   requires `homework: []` in this slice; the UI says
   practice is ungraded and offers the shared material rather than manufacturing a
   homework row. Grading/certificate policy remains an owner decision before those
   capabilities are implemented.

5. **Archives are GitHub-only.** A materially different older curriculum (including
   LLM Zoomcamp 2025) is declared with `curriculum=github_archive` and remains in
   `cohorts/<identifier>/` in GitHub with its code, assets, and links. The source
   contract contains only `archive.notice_path` (for example,
   `cohorts/2025/README.md`); the website importer derives and stores the exact
   GitHub blob URL for the Markdown notice from the validated repository URL, incoming full 40-character
   commit SHA, and that path. A manifest must never self-reference a future commit.
   The website may show an archive notice and that exact link on a cohort or mapped
   operational homework page, but it must not parse or render historical lesson
   bodies/assets as a website revision. A closed/archive cohort never redirects to a
   newer assignment.

6. **Explicit family identity.** The public family remains `llm-zoomcamp`; do not
   normalize every repository name into a family slug. Preserve the reviewed mapping in
   `courses/course_family_catalog.py`. The AI Dev Tools exception remains an explicit
   mapping only. Remove the live year-stripping fallback in `Cohort.save()` only after
   every legacy edition has a reviewed mapping and a preflight proves no unmapped row.

7. **Safe context selection.** On a shared module/lesson page, delivery context has
   this precedence: explicit URL context (`?cohort=<identifier>`), a valid remembered
   course preference, the user's sole enrollment, then a chooser. If the user has
   multiple enrollments, never silently choose the newest. An explicit cohort path
   always wins, even when it is closed or differs from the remembered preference.
   Context changes the delivery panel and links only; it never changes the shared lesson
   body or canonical path.

8. **Anonymous and account behavior.** Anonymous readers can read current lessons and
   public assignment statements. Reading or toggling a shared lesson never creates an
   `Enrollment`; authenticated submission/private history requires the explicitly
   selected cohort and an authorized enrollment. A context URL survives login through
   the existing safe local `?next=` mechanism. A copied context URL grants no enrollment.

## Evidence and current implementation boundary

The following repository facts drive the work. They are not optional cleanup items.

| Area | Current evidence | Required consequence |
| --- | --- | --- |
| URL routing | `courses/urls.py` requires a cohort for module/unit pages and uses two-segment paths; operation routes and legacy edition aliases are interleaved. | Add unambiguous shared paths and `/cohorts/<identifier>` operations. Keep explicit redirect aliases and update all reverse builders. |
| Resolver | `courses/views/url_utils.py` uses `cohort_url_kwargs()` and `get_cohort_or_404()` with a misleading `cohort_year` argument. | Introduce semantic `cohort_identifier` helpers; retain an adapter for old callback kwargs during the transition. |
| Curriculum models | `courses/models/curriculum.py` attaches `Module` to `Cohort`, `Unit` to `Module`, and `UnitReadState` to `Unit`; `Module` requires one terminal cohort homework. | Add separate shared graph and cohort placement/mapping models. Leave old rows and behavior intact for legacy/module cohorts. |
| Importer | `courses/services/curriculum_import.py` duplicates each parsed module/unit into every cohort. | Branch on root shared modules and upsert one shared row per course/content ID; upsert only placement/mapping rows per cohort. Keep `CourseCurriculumImportRun` idempotence and atomic transaction. |
| Parser | `content_sync/course_repository.py` accepts root `module.yaml` but treats it as another module scope; module README/group text is dropped; any `cohorts/<dir>` can become an implicit legacy cohort. | Dispatch v1/v2 explicitly; add root shared scope, numbered-slug validation, explicit `delivery`/`curriculum` fields, and archive `notice_path`. Never infer a website cohort from an arbitrary directory, and never treat an archive descendant as a lesson. |
| Source repository | `llm-zoomcamp` currently has root README stubs and current material under `cohorts/2026`; `cohorts/2025` is a materially different, unmanifested tree. | Move current 2026 material to numbered root folders only after the website checker and dry-run import pass. Keep 2025 opaque and link it by exact commit. |
| Assets | `courses/services/unit_assets.py` points relative images and code at mutable GitHub branch URLs. | Import current relative assets into managed storage with DB rows and stable paths. Archive assets are reached only through the exact GitHub archive link. |
| Assignment view | `courses/views/homework.py` can `get_or_create()` an enrollment while building an authenticated read context. | Change read rendering to a non-creating lookup; create/use enrollment only in the authorized mutation path. |
| Operations | Leaderboard, dashboard, enrollment, project, homework, and calendar queries are already cohort-scoped, but URLs are not explicitly namespaced. | Keep every query scoped to the path's cohort; only change route construction/compatibility wrappers. |
| SEO/cache | `content/public_views.py` courses sitemap lists family pages only; `core/middleware.py` classifies private routes by names and credentials. | Add clean shared module/lesson sitemap records, canonical/robots metadata, and no-store for context-query or operational/private responses. |
| Design | `core/content_page.html` and `courses/tests/test_content_page_shell.py` enforce the page shell. | New shared pages and context/notice panels must use the existing shell and desktop/mobile browser evidence. |

## Specification amendments required before implementation

Open decisions currently disagree with the owner direction and must be amended in a
small documentation ticket before code tickets are selected:

- `_docs/specs/open-decisions.md` §3 currently defers reusable curriculum; record the
  one-current-shared-graph decision, cohort placement/assessment ownership, and the
  self-paced phase-one boundary.
- `_docs/specs/open-decisions.md` §5 currently says no `cohorts/` segment, while
  `_docs/specs/02-url-link-seo-compatibility.md` and the owner require it. Replace the
  no-segment sentence with the exact canonical route table and explicit legacy alias
  policy.
- `_docs/specs/04-courses-and-cohorts.md` must add `shared` curriculum presentation,
  `live`/`self_paced` delivery, `current`/`github_archive` curriculum source,
  archive-only behavior, and preserve the existing `legacy`/`modules` contracts. It
  must explicitly say that no release viewer is part of this slice.
- `_docs/specs/09-migration-rollout-roadmap.md` must add the checker-before-source-move
  gate, shared graph backfill/read-state migration, and rollback/retention behavior.
- `_docs/architecture/database-only-content.md` must document shared curriculum rows
  and managed course assets as the read path; raw GitHub is an import input/archive
  link only.
- `_docs/compatibility/course-route-contracts.json` and the route registry must record
  the old two-segment, old `modules/` lesson, legacy edition-slug, and old-host paths
  as explicit aliases with owner/reason/status. No wildcard redirect is permitted.

## Target data contract

The first schema migration adds these models in `courses/models/shared_curriculum.py`
 and exports them from
`courses/models/__init__.py`. The migration must be additive and run against a
production-like copy before any source move.

**Source `content_id` (schema v2's canonical UUID) is never the Django primary
key.** Every model below keeps Django's normal auto-incrementing integer `id`
as its primary key and stores the source UUID in a separate, uniquely-indexed
field (`source_content_id` below) used only for import-time reconciliation. A
random UUID as a clustered/primary key fragments insert locality on every
write; an ordinary integer PK with the UUID as a secondary unique index gets
the same reconciliation guarantee (`get_or_create`/upsert by
`source_content_id`) without paying that cost on every import. "Primary key"
elsewhere in this document (e.g. "shared lesson primary keys are reused")
always means this Django `id`, never the source `content_id`.

### Current shared graph

`SharedCurriculum` is the one current graph for one `Course` (`OneToOneField`,
`PROTECT`/explicit archive transition). It stores source stable ID/content ID/path,
commit, checksum, parser version, and `updated_at`. It is not a version selector and
has no public historical route.

`SharedModule` stores `curriculum`, stable `source_content_id`, numbered `slug`,
`title`, `position`, optional module-overview Markdown and sanitized HTML, optional
summary/group metadata, publication/retirement state, and complete source provenance.
If the source has `README.md`, it may be imported into this separate overview field
as a module index/intro; it is never treated as a `SharedLesson`, and a missing lesson
cannot fall back to README content. Constraints:

- unique `(curriculum, slug)` and `(curriculum, position)`;
- unique `(curriculum, source_content_id)` when present;
- a published module belongs only to the current graph and has a numbered slug;
- retired source rows are retained when learner progress or an alias requires them but
  are absent from current navigation.

`SharedLesson` stores `module`, stable `source_content_id`, numbered `slug`, `title`,
optional summary, `position`, source Markdown, sanitized rendered HTML, video URL,
declared code-source metadata, publication/retirement state, and complete source
provenance. Constraints mirror `SharedModule` for `(module, slug)`, `(module,
position)`, and source content ID. Stable source IDs are the identity; positions and
titles may change only through an explicit import/alias check.

`CohortSharedModule` is the delivery placement: `cohort`, `shared_module`, delivery
position, and nullable `terminal_homework` (`PROTECT`). It is unique by cohort/module
and cohort/position. A validation query/service rejects a module whose curriculum's
course differs from the cohort's course and a homework from another cohort. A
self-paced placement may have no homework.

Do not add a new shared project-flow model in this slice. Existing v1 project flow stays intact for v1 deliveries. V2 current modules use root numeric order; cohort projects remain accessible through their existing operational pages. Interleaving projects into a v2 learning path requires an explicit source-contract extension before implementation, not an inferred project order.

`SharedLessonReadState` is `(user, shared_lesson, read_at)` with a unique constraint on
the pair. A data migration copies the earliest `read_at` for each stable source content
ID from old `UnitReadState` rows; it does not silently merge different source IDs.
Old `Unit`/`UnitReadState` rows remain during the rollback/audit window and are not used
by shared public reads after cutover.

### Assets and links

`SharedCurriculumAsset` stores the owning lesson (or module overview), repository
relative source path, safe stable public path, managed-storage key, content type, byte
size, checksum, and complete source provenance. The storage key includes source stable
ID, full commit SHA, and checksum, so an import cannot overwrite bytes currently being
served. The stable public path is a DB lookup, not a GitHub branch URL. Reuse the
existing media-store abstraction where possible; do not attach course rows to the
rejected generic `ContentRelease` graph solely to obtain asset storage.

The importer validates and stores relative image/code references from the same
snapshot. Absolute external HTTPS references remain subject to the existing sanitizer
and are never rewritten; missing/escaping relative references reject the import. The
asset endpoint reads the row and managed store only. It never fetches GitHub during a
request. Cleanup of an uncommitted object is an import-job concern and must not expose
its key in logs.

`CurriculumRouteAlias` stores an explicit old path, target kind, reason, source
commit and active flag. Its target is exactly one of a shared module, a shared
lesson, or a GitHub archive destination attached to a cohort. For the archive
case, store a validated repository-relative path under that cohort archive;
derive the HTTPS GitHub URL from the trusted repository identity and imported
commit. Use `blob/<sha>/<path>` for a Markdown notice/lesson file and
`tree/<sha>/<path>` for a directory. Never accept an arbitrary external redirect
URL. This permits old historical lesson links to reach matching archived files
without creating historical website lessons. Reject collisions, loops, missing
targets, retirement without replacement, and reusing an old path for different
content. Import does not infer aliases from titles or numeric positions.

### Cohort additions

Extend `Cohort` with the database equivalents of the v2 manifest discriminators:

- `curriculum_format` choice `shared` in addition to existing `legacy`/`modules`;
- `delivery_mode` choices only `live` and `self_paced`, defaulting existing rows to
  `live` without changing their schedule;
- `curriculum_source` choices `current` and `github_archive`, defaulting existing
  rows to `current` while their legacy/module format remains supported during the
  transition;
- `shared_curriculum` nullable FK to `SharedCurriculum`, used only for
  `curriculum_format=shared` and validated to belong to the cohort's course;
- `archive_notice_path` nullable repository-relative POSIX path plus generated
  `archive_url` and `archive_commit_sha` (full lowercase SHA). These three are
  required for `curriculum_source=github_archive`; the importer derives the latter
  two from the validated incoming repository URL and commit and the manifest's
  `archive.notice_path`. They are absent for `curriculum_source=current`.

`curriculum_source=github_archive` is orthogonal to delivery, so a historical cohort
may retain `delivery_mode=live`; it has no shared module/lesson placement. A source
`curriculum=current` maps to the shared graph for v2, while old v1 rows retain their
existing `legacy`/`modules` format until an explicit migration. Add model/service
validation for the allowed combinations; do not overload `delivery_mode` with an
`archive` value.

The adapter must use this one-way mapping (and expose it in its diagnostics):

| v2 manifest field | Website projection |
| --- | --- |
| `delivery` | `Cohort.delivery_mode` (`live` or `self_paced`) |
| `curriculum: current` | `Cohort.curriculum_source=current`, `curriculum_format=shared`, and the course's `SharedCurriculum` pointer |
| `curriculum: github_archive` | `Cohort.curriculum_source=github_archive`, no shared placement, and archive notice/derived URL fields |
| homework `{module: root-slug, source}` | one cohort-owned `Homework` plus the matching `CohortSharedModule.terminal_homework` binding |
| archive homework `{module: null, source}` | one cohort-owned operational `Homework` with no shared-module binding |
| `archive.notice_path` | `archive_notice_path`; importer derives/stores `archive_url` and `archive_commit_sha` |

There is no reverse inference from database year, folder name, title, or homework slug
back into a v2 mapping.

Do not rename or delete `slug`, `uuid`, legacy numeric IDs, homework/project foreign
keys, enrollment rows, submissions, scores, or certificates. Do not change
`Homework.due_date` in this work. Keep the reviewed family mapping explicit and remove
`Cohort.save()`'s regex fallback in a separately audited step only after preflight.

## Interfaces and invariants

### Context service

Add `courses/services/course_context.py` with a pure, testable result such as:

```python
DeliveryContext(
    course: Course,
    cohort: Cohort | None,
    enrollment: Enrollment | None,
    source: Literal["explicit", "remembered", "sole_enrollment", "chooser", "invalid"],
    explicit: bool,
    has_multiple_enrollments: bool,
    invalid_identifier: str | None,
)
```

`resolve_delivery_context(request, course, explicit_identifier=None)` must:

- parse exactly one bounded ASCII `cohort` query value; duplicate, empty, malformed,
  or unknown explicit values never fall back;
- validate a remembered session preference against the course and visible/allowed
  cohort before using it; session writes are keyed by course slug and never by email;
- use a sole enrollment only when there is exactly one eligible cohort;
- return chooser state for zero or multiple choices; never select newest implicitly;
- preserve the selected context in same-tab shared links, previous/next, module/homework
  links, and login `next` values;
- set `request.private_response_required` for query/session/authenticated context that
  varies the representation, so `ResponsePolicyMiddleware` cannot share it. A clean,
  context-free anonymous lesson can remain public-cache eligible.

An explicit `/cohorts/<identifier>/...` operation resolves directly from the path and
never consults this default resolver. Missing cohort or missing mapping is a bounded
404/chooser error, not a newest-cohort fallback.

### URL builders and aliases

Update `courses/views/url_utils.py` with semantic builders:

- `course_family_url(course)`;
- `shared_module_url(module)`;
- `shared_lesson_url(lesson, *, cohort=None)`;
- `cohort_url(cohort, route_name="cohort", **kwargs)`;
- `legacy_course_alias_url(...)` for compatibility tests only.

`cohort_url_kwargs()` may remain temporarily for copied callbacks, but its adapter must
map one semantic identifier and all generated URLs must use the canonical
`cohorts/<identifier>` path. Mechanical callers include
`courses/views/{course,course_aliases,course_calendar,course_enrollment,course_leaderboard,
dashboard,homework,homework_statistics,homework_submissions,module,project,project_eval,
project_eval_actions,project_results,project_statistics,project_submissions,unit}.py`,
`courses/services/member_home.py`, wrapped/deadline/confirmation services, and all
templates using `url` tags.

Canonical route names should remain available to copied services (`course`, `homework`,
`leaderboard`, `dashboard`, `project`, `module`, `unit`, etc.) but reverse to the new
paths with `cohort_identifier`. Give redirect-only aliases distinct names. A route
converter must not treat `year` as a numeric-only value.

### Import command

Extend `CourseRepositorySource`, `ModuleSource`, `CohortSource`, and
`CurriculumImportCommand` in `courses/services/curriculum_source.py` and
`courses/services/curriculum_import.py` with:

- explicit module scope (`shared` or `cohort`), optional module-overview metadata,
  stable source IDs, and explicit v2 cohort fields (`delivery`, `curriculum`,
  `archive.notice_path`, and a list of `{module, source}` homework bindings);
- root shared module import exactly once per source content ID;
- cohort placement and assignment binding import by stable IDs;
- `curriculum=github_archive` suppression before descendant discovery, with only
  `archive.notice_path` and explicitly mapped `module: null` homework exposed to the
  projection;
- source-scoped soft-retirement for removed shared rows; preserve read/assessment
  evidence and fail closed on a submitted homework/module change;
- the existing `CourseCurriculumImportRun` state/count/diagnostic contract, source
  commit identity, atomic transaction, and replay behavior.

The importer may update DB-owned current content in place after parser validation. It
must not render from a checkout or call GitHub from a view. A failed import leaves all
previous current rows, mappings, and assets visible. A successful import invalidates
the course/module/lesson public cache keys and records counts using user IDs only in
logs (never email addresses).

### Assignment and auth invariant

Change `courses/views/homework.py` and its context helpers so GET/HEAD display uses
`Enrollment.objects.filter(...).first()` and never `get_or_create()`. Submission,
enrollment toggle, project mutations, score breakdown, and private history continue to
require the path's exact cohort and authenticated permission. Add explicit tests for an
authenticated non-enrolled reader, a user enrolled in two cohorts, and a copied URL
whose cohort differs from the remembered preference.

## Staged implementation tickets

The tickets below are intentionally bounded so each can be assigned to one engineer and
independently tested. They are ordered by dependency; a later ticket must not move the
source repository ahead of the checker gate.

### W0 — Amend the contracts and freeze the route map

**Dependencies:** none. **Owner:** website PM/spec maintainer.

**Files:** `_docs/specs/open-decisions.md`,
`_docs/specs/02-url-link-seo-compatibility.md`, `_docs/specs/04-courses-and-cohorts.md`,
`_docs/specs/09-migration-rollout-roadmap.md`,
`_docs/architecture/database-only-content.md`,
`_docs/compatibility/course-route-contracts.json`,
`_docs/compatibility/` route/redirect manifest, and a focused contract test under
`content/tests/` or `courses/tests/`.

**Deliverables:** record the decisions above; classify every current course route and
alias; define canonical/redirect/no-store/noindex expectations; document that GitHub
archives are opaque website links; define exact archive URL validation; record the
route kwarg compatibility window. No implementation code or source move belongs here.

**Acceptance/tests:** a contract test fails for an unclassified route, a wildcard
redirect, an archive URL containing a branch, or a canonical shared URL containing a
cohort query. Review the manifest against `courses/urls.py` and the copied-host route
inventory.

**Command:**

```text
uv run --frozen pytest content/tests/test_route_contracts.py content/tests/test_canonical_routes.py -q
```

### W1 — Build the parser/layout checker before changing GitHub

**Dependencies:** W0. **Owner:** website/content-sync engineer.

**Files:** `content_sync/course_repository.py`,
`content_sync/course_repository_ingest.py`,
`content_sync/course_repository_registration.py`,
`content_sync/course_repository_sources.json`,
`courses/services/curriculum_source.py`,
`content_sync/tests/test_course_repository.py`,
`content_sync/tests/test_course_repository_transport_parity.py`, new pure layout/checker
module (for example `content_sync/course_repository_layout.py`), its tests, and a new
fixture under `content_sync/tests/fixtures/course_repository/llm_zoomcamp_shared/`.

**Contract:** dispatch on the root `course.yaml:schema_version`. Keep schema 1 on its
existing parser and projection unchanged; schema 2 accepts only numbered root module
directories (`NN-kebab/module.yaml`) and numbered lesson Markdown listed by the module
manifest. `README.md` is a GitHub module index and may be exposed as a separate,
optional module overview/intro, but is never a lesson or a fallback for a missing
lesson. Validate explicitly declared relative assets/code and module-local paths.

For schema 2, discover cohort manifests only at `cohorts/<id>/cohort.yaml`. A current
cohort has `delivery: live|self_paced`, `curriculum: current`, and a list of explicit
homework bindings `{module: <root-module-slug>, source: <full repo-relative path>}`;
`homework: []` is valid. Classify an archive cohort first: its
`curriculum: github_archive` record contains only `archive.notice_path` plus optional
explicit homework bindings whose `module` is `null`; the complete descendant tree is
opaque GitHub content. Archive module/unit IDs are ignored, while explicitly mapped
archive homework/question IDs register as cohort-owned operational data. A current
mapping's `module` must identify a root module, while its homework `source` belongs inside `cohorts/<id>/`; and every mapped source must resolve under its
declaring cohort. Existing legacy/module layouts remain accepted for other sources
during the transition; v1 is never silently reinterpreted as v2.

Add one versioned, test-only known-output fixture (for example
`content_sync/tests/fixtures/course_repository/llm_zoomcamp_shared/expected-v2.json`)
whose schema is the parser's normalized output: course identity, one list of root
shared modules/lessons, explicit cohort records (`identifier`, `delivery`,
`curriculum`, archive notice), and explicit homework bindings. The checker, bootstrap
contract tests, and website importer tests must all consume this same fixture/output
schema; do not maintain three independent sample formats. It is test data only and
must never be a runtime content fallback.

The fixture contract is intentionally concrete so later consumers do not invent a
second shape:

```json
{
  "schema_version": 2,
  "course": {"content_id": "11111111-1111-4111-8111-111111111111", "slug": "llm-zoomcamp"},
  "modules": [{
    "scope": "shared",
    "content_id": "22222222-2222-4222-8222-222222222222",
    "slug": "01-agentic-rag",
    "lessons": [{"content_id": "33333333-3333-4333-8333-333333333333", "slug": "01-lesson", "path": "01-lesson.md"}]
  }],
  "cohorts": [
    {"identifier": "2026", "delivery": "live", "curriculum": "current",
     "homework": [{"module": "01-agentic-rag", "source": "cohorts/2026/homework/01-agentic-rag/homework.yaml"}]},
    {"identifier": "self-paced", "delivery": "self_paced", "curriculum": "current", "homework": []},
    {"identifier": "2025", "delivery": "live", "curriculum": "github_archive",
     "archive": {"notice_path": "cohorts/2025/README.md"},
     "homework": [{"module": null, "source": "cohorts/2025/01-old-module/homework.yaml"}]}
  ]
}
```

The real fixture uses UUIDs/checksums and the parser's typed output, but retains these
keys and semantics. `archive` never contains a commit supplied by the source; the
import report adds the validated incoming commit and derived URL.

**Checker interface:** add a no-network command such as
`uv run --frozen python scripts/verify_course_repository_curriculum.py --checkout
<path> --commit <full-sha> --json .tmp/course-content-research/layout-report.json`.
It must run `git archive` for the specified commit, inspect tar metadata and parser
output without importing/executing notebooks or code, and report counts/paths/checksums
without file contents or PII. It fails on root stubs, unnumbered module/lesson slugs,
duplicate stable IDs, unsafe/missing references, ambiguous aliases, archive branch-tip
URLs, malformed manifests, archive module mappings, out-of-cohort homework paths,
mixed live manifest versions, and parser/import contract drift. It must prove that a
complete archive subtree contributes zero module/unit records and that an explicitly
mapped archive homework contributes only a cohort homework record.

**Admission bounds:** preserve the shared compressed archive ceiling (`MAX_ARCHIVE_BYTES`
200,000,000), source decompressed ceilings (`max_files`, `max_total_bytes`,
`max_file_bytes`), YAML bounds, and path/symlink checks. Measure both before admitting
expanded current assets; the present `llm-zoomcamp` archive is about 28.5 MB and must
remain below the registered source's 100 MB/5,000-file limits after the move. Do not
raise limits merely to admit duplicated current material. Make `ContentSource.path_allowlist`
effective or explicitly document/implement the shared admission function so push and
pull transports cannot disagree.

**Acceptance/tests:** the known-output fixture yields one `shared` module graph, one
live current cohort, one self-paced current cohort, and one `github_archive` cohort;
the same root module source ID is represented once even when both current cohorts
reference it; archive module/unit IDs are absent while a mapped `module: null`
homework ID is present; 2025-like files do not create a website lesson; archive and
checkout transport tests apply identical bounds; `--help` and dry-run output are
deterministic.

**Commands:**

```text
uv run --frozen pytest content_sync/tests/test_course_repository.py content_sync/tests/test_course_repository_transport_parity.py content_sync/tests/test_course_repository_registration.py -q
uv run --frozen python scripts/verify_course_repository_curriculum.py --help
```

### W2 — Add the shared graph schema and safe backfill service

**Dependencies:** W0 (contract), W1 (stable parser shape). **Owner:** website courses
model engineer.

**Files:** new `courses/models/shared_curriculum.py`,
`courses/models/cohort.py`, `courses/models/__init__.py`,
`courses/migrations/0005_shared_current_curriculum.py` (name may follow the generated
migration sequence), new `courses/services/migrate_shared_curriculum.py`,
`courses/tests/test_shared_curriculum_models.py`, and migration/preflight tests.

**Deliverables:** create the models and constraints in the data contract; add
`CurriculumFormat.SHARED`, `Cohort.delivery_mode` (`live|self_paced`),
`Cohort.curriculum_source` (`current|github_archive`), the shared curriculum relation,
and archive notice/derived URL fields. Add an explicit `--dry-run`/`--apply` backfill
service that reads the existing LLM 2026 cohort-owned Module/Unit rows by source
content ID, creates one shared graph, creates placements/mappings, copies earliest read
timestamps, and leaves all old rows, homework, projects, submissions, scores,
enrollments, and certificates untouched. It must stop with a bounded conflict report
when two rows with one stable ID have different content/provenance; no fuzzy title or
year matching. A v2 archive cohort creates no shared placement; only an explicitly
mapped archive homework is eligible for projection, with `module=null`.

**Acceptance/tests:** database constraints reject duplicate positions/source IDs, invalid discriminator combinations, and missing required archive fields. Model/service validation rejects cross-course placements and cross-cohort homework bindings before writes; tests must exercise those service boundaries, since ordinary Django check constraints cannot join foreign-key tables.
Backfill dry-run is repeatable, apply is idempotent, and a failed conflict changes no
rows. Existing rows retain IDs/history and are not silently relabeled as archive or
self-paced. Empty databases still have no public content; runtime never reads a
fixture or source checkout.

**Commands:**

```text
DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test courses.tests.test_shared_curriculum_models courses.tests.test_curriculum_models courses.tests.test_curriculum_source_provenance --noinput
uv run --frozen pytest test_support/tests/test_migrations.py -q
```

### W3 — Import one shared graph and managed current assets

**Dependencies:** W1 parser contract, W2 schema/backfill. **Owner:** website content-sync
engineer.

**Files:** `courses/services/curriculum_import.py`,
`courses/services/curriculum_source.py`, `courses/models/shared_curriculum.py`,
`courses/services/unit_assets.py` (or its shared replacement),
`courses/services/unit_links.py`, `content_sync/course_repository_ingest.py`,
`courses/tests/test_curriculum_import_service.py`,
`courses/tests/test_shared_curriculum_import.py`, asset-store tests, and import fixtures.

**Deliverables:** branch the existing importer for root shared modules. Consume the
parser's known-output schema and upsert one `SharedModule`/`SharedLesson` per source
stable ID, including optional module overview/group metadata, sanitized HTML, video/code
metadata and managed assets. Upsert per-cohort placement, terminal-homework mapping
by stable IDs; retain existing project operational records. For `curriculum=github_archive`, persist the cohort's
`archive_notice_path` and derive `archive_url` from the validated repository URL,
incoming full commit SHA, and notice path; do not create shared module/unit rows. An
explicit archive homework binding with `module: null` may create/update only its
cohort-owned homework/questions. Keep legacy/module cohort projection unchanged.
Source-scoped removals retire unreferenced shared rows instead of deleting rows needed
by read state/aliases; submitted cohort assessments retain their existing protection
checks. Keep import runs replayable by source/commit/parser, atomic, and failure-safe.

**Acceptance/tests:** two current cohorts reference the same database lesson/module
primary key; editing current Markdown updates the one shared row and not an old/archive
cohort; a changed source slug with unchanged ID requires an explicit alias; a missing
assignment mapping never selects another cohort's homework; a complete archive fixture
creates zero module/unit rows while an explicitly mapped `module: null` homework is
registered; the archive URL contains the incoming full SHA and validated notice path;
failed asset upload/import does not expose a public row; no request reads GitHub/raw
branch URLs. Current relative images resolve to the managed stable path, external HTTPS
links remain external, and historical lesson files are not imported.

**Commands:**

```text
uv run --frozen pytest courses/tests/test_curriculum_import_service.py courses/tests/test_shared_curriculum_import.py courses/tests/test_unit_markdown_links.py courses/tests/test_unit_pages.py -q
uv run --frozen pytest content_sync/tests/test_course_repository_transport_parity.py -q
```

### W4 — Implement shared views, context, and namespaced operations

**Dependencies:** W2 schema; W3 import interface. **Owner:** website route/courses
engineer.

**Files:** `courses/urls.py`, `courses/views/url_utils.py`, new
`courses/services/course_context.py`, new shared view module(s),
`courses/views/course.py`, `courses/views/module.py`, `courses/views/unit.py`,
`courses/views/homework.py`, `courses/views/course_page_context.py`,
`courses/views/course_aliases.py`, and every reverse-builder/callsite listed in the URL
interface section. Add focused route/context tests under `courses/tests/`.

**Canonical routes:** add shared family/module/lesson views and `/cohorts/<identifier>`
operation routes. Existing route names used by copied services reverse to the new
canonical destinations. Add explicit redirect-only routes for:

- old `/courses/<family>/<identifier>` cohort pages and all old operation children;
- old `/courses/<family>/<identifier>/modules/<module>/<lesson>` pages;
- legacy edition slugs (`/courses/llm-zoomcamp-2026/...` and recorded root aliases);
- known `courses.datatalks.club` HTML compatibility paths, subject to the existing
  host/API migration contract.

GET/HEAD aliases redirect once with the raw query preserved. Existing mutation routes keep direct compatibility adapters with the same path-cohort authorization until their clients are migrated; never redirect a submission or replace a working POST endpoint with 405 as a side effect of moving HTML routes. Routes that were already read-only retain their 405/Allow behavior. Unknown/malformed identifiers are
real 404s. Redirect targets are generated from reviewed mappings, never year stripping.

**Context/auth behavior:** shared module footer and lesson panel show selected cohort,
mapped homework title/deadline/status and fully qualified operation URL when available;
without context they show an explicit chooser. Preserve query context in next/previous,
module, homework and login links. A selected 2025 archive operation always remains in
2025; a selected 2026 operation never leaks into it. Anonymous GET never creates an
enrollment. Private mutation views continue exact cohort authorization.

**Acceptance/tests:** cover zero/one/multiple enrollment, two browser tabs with different
contexts, direct wrong-cohort links, invalid explicit context, missing mapping, closed
archive cohort, login return with query, anonymous read, authenticated non-enrolled read,
and submission denial/allowance. Existing scoring/project/leaderboard tests retain their
cohort isolation and legacy IDs.

**Commands:**

```text
DJANGO_SETTINGS_MODULE=website.settings.test uv run --frozen python manage.py test courses.tests.test_course_cohort_split courses.tests.test_course_url_trailing_slash courses.tests.test_course_family_navigation courses.tests.test_shared_course_routes courses.tests.test_course_context courses.tests.test_homework courses.tests.test_course_leaderboard --noinput
```

### W5 — Render the shared pages, archive notice, SEO and cache contract

**Dependencies:** W4 routes/context, W3 stored HTML/assets. **Owner:** website frontend/
SEO engineer.

**Files:** `courses/templates/courses/course_family.html`,
`courses/templates/courses/course.html`, existing or new
`courses/templates/courses/module.html`/`unit.html` plus shared variants,
`courses/templates/courses/_module_rail.html`, new context/chooser/archive partials,
`courses/registration.py` only if renderer helpers need a safe seam,
`core/middleware.py`, `content/public_views.py`, `content/public_routes.py`,
`content/tests/test_courses_sitemap_contract.py`, route/SEO tests, and
`courses/tests/test_content_page_shell.py` updates.

**Deliverables:** use `core/content_page.html` and the existing design system. Shared
module pages show overview/grouped numbered lessons once; shared lessons show title,
video, body, managed images/code links, read control, context panel and next/previous
links. Archive cohorts/homework show a clear “older curriculum” notice and exact GitHub
archive link, never old lesson body. Self-paced UI says “practice/progress” and omits
deadline/competitive claims.

Clean shared paths self-canonical and are eligible for the anonymous public course/detail
cache only when no context or credential varies them. Context query/session,
authenticated state, operations, homework, leaderboard, dashboard, submissions,
enrollment, and archive/private state set `private, no-store` and `X-Robots-Tag` where
the route policy requires. Development remains `noindex, nofollow`.

The courses sitemap contains `/courses`, visible family pages, published current shared
module and lesson paths, only. Cohort landing pages remain outside the initial sitemap. It excludes query-context URLs, old redirects, homework/leaderboard/
dashboard/private children, and GitHub archives. Canonical, Open Graph, breadcrumb and
internal links never emit a cohort-specific lesson path.

**Acceptance/tests:** page shell test passes; canonical/query/robots/cache tests cover
GET/HEAD/POST, credential cookies, session context and `?cohort`; sitemap has no query,
archive, private or redirect locations; managed images load without GitHub requests;
desktop 1440px and mobile 390px browser screenshots show no overflow, duplicate rail,
missing context, or error page.

**Commands:**

```text
uv run --frozen pytest courses/tests/test_content_page_shell.py content/tests/test_courses_sitemap_contract.py content/tests/test_public_routes_and_seo.py courses/tests/test_shared_course_seo.py -q
uv run --frozen python scripts/ci.py test-playwright-core
```

### W6 — Compatibility, migration audit, and consumer parity

**Dependencies:** W4/W5; W0 route manifest. **Owner:** website compatibility/data engineer.

**Files:** `_docs/compatibility/course-route-contracts.json`, route registry and
redirect tests, `courses/views/course_aliases.py`, `courses/views/url_utils.py`, copied
API/management serializers if they embed course URLs, deadline/reminder/member-home/
confirmation builders, certificate/calendar URL helpers, and migration/preflight
reports.

**Deliverables:** mechanically inventory `reverse("course"/"module"/"unit"/operation)`
and template URL tags; update generated links while preserving API field names and
legacy numeric/UUID IDs. Produce a deterministic old-to-new path report and one-hop
redirect tests. Preserve ICS UID suffixes and query strings. Confirm external
`courses.datatalks.club` compatibility routes remain available until their declared
cutover; do not redirect API requests through HTML Lambda behavior.

**Acceptance/tests:** all known legacy route examples resolve to one reviewed final or
real 404; no generated page contains the old two-segment canonical; all operation forms
post to the selected cohort; old calendar/event UIDs and assessment IDs remain stable;
existing mutation adapters preserve behavior and do not redirect. Run the route inventory checker and copied course/API
characterization suite.

**Commands:**

```text
uv run --frozen pytest courses/tests/test_course_url_trailing_slash.py courses/tests/test_course_links.py content/tests/test_route_contracts.py -q
rg -n 'reverse\(("|\x27)(course|module|unit|homework|leaderboard|dashboard)' courses scripts content_sync
```

### W7 — Dry-run/backfill the website before source movement

**Dependencies:** W1, W2, W3, W4, W6. **Owner:** website release/data operator with
independent checker.

**Files:** `scripts/verify_course_repository_curriculum.py`,
`courses/services/migrate_shared_curriculum.py`, import fixtures/reports under `.tmp/`,
and runbook documentation under `_docs/runbooks/`.

**Deliverables:** against a production-like database copy, run schema migration, shared
graph dry-run, old read-state reconciliation, and exact current-source parse/import in
one report. First inventory **every** current-linked cohort for `llm-zoomcamp`, including
live and self-paced rows: identifier, delivery, curriculum source, mapped homework,
enrollment count, submitted-assessment count, stable module/lesson IDs and route
aliases. For each row, record an operator-reviewed decision to remain
`curriculum=current` (shared graph) or become `curriculum=github_archive`; an ongoing
self-paced cohort cannot be archived or have its assignment prerequisites changed by
silence. A new self-paced delivery gets a new identifier; never reuse an identifier to
erase old history. Compare counts and checksums: module/lesson identities, homework
bindings, asset manifest, route aliases, legacy IDs, submissions/enrollments, and cache
invalidation intents. Apply only after an independent reviewer confirms no conflicting
stable IDs, no submitted assessment would be changed, no 2025 body is imported, and
the generated archive URL points to a full commit. Keep a rollback procedure that
restores route readers/old rows without deleting new rows. Distinguish a no-copy new
current delivery from the separate archive operation in the report and approval.

**Acceptance/tests:** replay is idempotent; every current-linked live/self-paced cohort
has an explicit keep/archive decision; a forced parser/import/asset failure leaves the
pre-run current graph visible; old and new route smoke requests agree on content or
the recorded redirect; shared lesson primary keys are reused by live and self-paced
placements; archive conversion preserves cohort/homework/submission/enrollment IDs and
creates no module/unit rows; a new self-paced cohort has no copied lessons and no
fabricated deadlines; no private data appears in the report.

**Commands:**

```text
uv run --frozen python scripts/verify_course_repository_curriculum.py --checkout .tmp/course-checkouts/llm-zoomcamp --commit <full-sha> --dry-run
uv run --frozen python manage.py migrate --noinput
uv run --frozen python manage.py migrate_shared_curriculum --course llm-zoomcamp --dry-run
uv run --frozen pytest courses/tests/test_shared_curriculum_import.py content_sync/tests/test_course_repository_transport_parity.py -q
```

### W8 — Move the LLM source tree, preserving 2025 archive content

**Dependencies:** W7 independent pass and green W1 checker. **Owner:** LLM Zoomcamp
repository owner, coordinated with website importer.

**Source changes (in `/home/alexey/git/llm-zoomcamp`, not this repository):**

- replace root `01-agentic-rag` … `07-project-example` README stubs with the current
  module manifests, module overviews, numbered lessons, code, and images currently
  under `cohorts/2026/<module>/`; move rather than copy current material so the source
  archive does not multiply it;
- preserve each existing module/lesson stable content ID and descriptive numbered slug;
  a rename adds an explicit alias in the manifest before the move;
- update `cohorts/2026/cohort.yaml` to schema 2 with
  `delivery: live`, `curriculum: current`, and explicit root-module-to-homework
  bindings; add any approved self-paced cohort manifest with
  `delivery: self_paced`, `curriculum: current`, the same root module graph and
  `homework: []`, never a copied lesson tree;
- preserve every historical file under `cohorts/2025/` intact and available for
  GitHub navigation; add only the v2 cohort manifest/README notice needed to describe
  that archive. Its manifest uses `delivery: live`, `curriculum: github_archive`, and
  only `archive.notice_path` plus optional explicit `module: null` homework bindings.
  Historical module manifests/lessons/assets may remain in that subtree as opaque
  GitHub content; they must not be discovered or projected by the website parser. Do
  not put a future commit SHA in the manifest; derive the final URL after the commit
  is created;
- run the checker against the exact commit and record the bounded archive/file counts.

The website importer consumes the commit only after the checker report, parser replay,
asset manifest and route smoke pass. It must never execute source notebooks/scripts or
read source files during a request.

**Acceptance/tests:** a fresh GitHub checkout has working root numbered links and
working 2025 archive links; `git diff --find-renames` shows current content moved, not
duplicated; the website displays one shared current graph and a 2025 archive notice;
the exact 2025 archive URL resolves at the recorded full SHA.

**Commands:**

```text
git -C /home/alexey/git/llm-zoomcamp archive --format=tar <full-sha> | wc -c
uv run --frozen python scripts/verify_course_repository_curriculum.py --checkout /home/alexey/git/llm-zoomcamp --commit <full-sha> --json .tmp/course-content-research/llm-zoomcamp-layout.json
uv run --frozen pytest content_sync/tests/test_course_repository.py courses/tests/test_shared_curriculum_import.py -q
```

### W9 — Connect the cohort bootstrap script and reusable operations skill

**Dependencies:** W2 data contract, W4 route/context contract, W7 dry-run, and the
parallel operations deliverable. **Owner:** operations agent (parent scope), with a
website consumer review.

**Website-facing contract:** the script/skill writes cohort source files only. The website importer subsequently creates or updates a `Cohort` with an
explicit course-family mapping, identifier, `delivery: live|self_paced`,
`curriculum: current|github_archive`, shared curriculum pointer when current, archive
notice metadata when applicable, and stable cohort-to-module-to-homework IDs. It must
not duplicate shared modules/lessons, copy learner state, infer identity from year
stripping, or overwrite existing assessment history. `self_paced` creation leaves due
dates/competitive state unset and is clearly labeled. Archive source creation stores the
validated notice path; website import derives and stores the exact GitHub commit URL, creates no lesson graph, and
may bind only explicitly mapped `module: null` operational homework. A no-copy new
self-paced delivery and archiving an old delivery are separate explicit operations.

**Acceptance:** website route smoke can consume a script-created live, self-paced and
archive fixture whose normalized output matches the single W1 known-output fixture;
repeated bootstrap is idempotent; a conflicting stable ID or assignment mapping fails
before mutation; the script's redacted report uses user IDs only.

### W10 — Cutover and independent verification gates

**Dependencies:** W8 and W9; all prior tests green. **Owner:** engineer, then independent
tester and PM per `_docs/PROCESS.md`.

**Engineer gate:** freeze base/head, generate the versioned selective verification plan,
run migrations/django checks and focused Django tests, record import/checker reports,
and leave the worktree uncommitted. No production conversion/deployment occurs in this
plan.

**Tester gate:** independently recompute the plan, run focused Django plus
`uv run --frozen python scripts/ci.py test-playwright-core` (or `uv run --frozen python scripts/ci.py test-playwright` if the changed harness/template
classification requires it), inspect desktop/mobile screenshots under `.tmp/screenshots/`,
and test anonymous, multi-enrollment, wrong-cohort, login-return, closed/archive,
missing-mapping and mutation compatibility and read-only method-denial behavior. Required screenshots cannot be pending.

**PM gate:** check every component exactly once, including route/canonical copy, empty
and error states, permissions, archive notice, responsive navigation and absence of
historical on-site lesson rendering. Only after both gates may the orchestrator merge/
push under the process rules.

## Dependency graph

```text
W0 contract/spec amendment
 └── W1 parser + bounded archive/layout checker
      └── W3 shared importer/assets ─┐
W1 → W2 additive schema + backfill ───────┼── W4 views/context/routes
                                    │       └── W5 templates/SEO/cache
                                    └────── W6 compatibility/consumer parity
                                             └── W7 dry-run + migration audit
                                                  └── W8 source move + archive commit
W2 + W4 + W7 ──────────────────────────────── W9 ops bootstrap/skill integration
W8 + W9 + all evidence ───────────────────── W10 tester/PM/cutover gates
```

No source-repository move (W8) is allowed while W1 or W7 is failing. W9 may be developed
in parallel after its contract is frozen, but its fixture cannot be accepted until W4
consumes it.

## Failure, security, and observability requirements

- Parser/import errors expose stable bounded diagnostic codes and source paths only;
  never Markdown, notebook contents, answer keys, credentials, or email addresses.
- GitHub webhooks and local pulls use the same tar reader, path/symlink policy, archive
  ceiling, file count, total bytes, YAML bounds, and parser. A source-specific limit may
  narrow but never widen the default.
- Source archive links are derived only after validating an HTTPS GitHub repository,
  full lowercase incoming commit SHA, cohort-relative `notice_path`, and a GitHub blob URL for the Markdown notice (or tree URL for an archive directory)
  that contains that exact SHA/path. Branch names (`main`, `master`) and
  self-referential future SHAs are rejected for archive identity. Archive content is
  never imported into current shared rows.
- Public requests query only published/current database rows and managed assets. There
  is no request-time GitHub fetch, filesystem Markdown fallback, checked-in JSON content
  fallback, notebook execution, or source-code import.
- Context/query/session/authenticated variants set `private_response_required` and
  `private, no-store`; public clean shared pages carry the same canonical/robots/asset
  representation on cache hit and miss. Cache keys never include arbitrary cookies,
  enrollment state, or email.
- Every mutation logs course/cohort/assignment stable IDs, request/correlation ID and
  user ID where allowed; never log email addresses or learner names. Import counts,
  checksum, commit and replay status are persisted in `CourseCurriculumImportRun`.
- Import success invalidates shared lesson/module/family and affected operations caches;
  failure leaves old rows and cache entries untouched. Asset cleanup is retryable and
  does not make an uncommitted public path resolvable.

## Required verification matrix

| Component | Minimum evidence |
| --- | --- |
| Models/migrations | `uv run --frozen python scripts/ci.py migrations-check`; `uv run --frozen python scripts/ci.py test-migrations`; model constraints and backfill dry-run/idempotence tests |
| Parser/transport | parser fixtures, push/pull parity, archive bounds, numbered paths, archive-only non-import, no execution |
| Import | shared primary-key reuse, stable-ID/alias behavior, protected assessment/read state, atomic failure, asset checksum/path |
| Routes/compatibility | canonical shared and `/cohorts/` paths, one-hop old aliases, legacy edition map, direct mutation compatibility/read-only 405, exact query preservation |
| Context/auth | anonymous read/no enrollment, sole vs multiple enrollment, explicit precedence, tab isolation, login return, wrong/missing/closed cohort |
| Operations | homework/project/leaderboard/dashboard/calendar exact cohort; no cross-cohort state or URL generation |
| SEO/cache/sitemap | canonical, metadata, robots, query no-store, credential no-store, sitemap inclusion/exclusion, public cache TTL class |
| UI/accessibility | content page shell, grouped module overview, single rail, read toggle, archive/self-paced copy, desktop/mobile screenshots |
| Source gate | root material moved once, 2025 remains readable, full SHA archive links, archive/file limits and checker report |

## Remaining policy and settled initial defaults

1. Whether self-paced learners may submit/grading/peer review and whether they can earn
   certificates. Until decided, the site offers reading, shared read progress and
   clearly ungraded practice only.
2. Sitemap initial policy: include course/current module/current lesson pages only. Keep cohort operation and archive destinations discoverable through links; adding cohort landings to the sitemap is a later editorial decision and does not block W5.
3. Retain retired shared lesson rows and managed assets through the rollout. Automated deletion/retention cleanup requires a later reviewed policy; it is not part of this implementation and must not create a public historical viewer.
4. Context cache decision: use `private, no-store` for context variants. A future caching optimization is not part of this implementation and must not block it.
5. The operations script authors the source manifest; the website importer is the sole database writer. Use `self-paced` for the initial synthetic fixture; choose real delivery identifiers explicitly during rollout.


---

# Appendix: frozen source contract (schema v2)

Schema v2 makes the repository root the single source for the current teaching
graph. It is an opt-in contract; a repository whose root `course.yaml` says
`schema_version: 1` remains on the v1 parser/checker until its migration is
reviewed.

## Source layout

```text
course.yaml
SITE.md
NN-module/module.yaml
NN-module/NN-lesson.md
cohorts/README.md
cohorts/<id>/cohort.yaml
cohorts/<id>/README.md
cohorts/<id>/homework/NN-module/homework.yaml
cohorts/<id>/homework/NN-module/homework.md
```

`NN-module` and `NN-lesson.md` are numbered kebab-case names. Lesson files are
siblings of `module.yaml`; `README.md` is a GitHub index and `images/` and
`code/` are module-local assets. A module manifest keeps the v1-compatible
`units: [{content_id, title, path}]` representation in the first v2 slice.
The module and every unit have a canonical, quoted UUID `content_id` which is
never reused or silently rebound.

## Course and cohort manifests

The v2 course manifest keeps the existing course identity fields:

```yaml
schema_version: 2
content_id: "7736c1e6-5d66-4286-8180-b1eef3f83a84"
slug: llm-zoomcamp
title: LLM Zoomcamp
outcome: Build and operate production-style LLM applications.
repository_url: https://github.com/DataTalksClub/llm-zoomcamp
docs_url: https://datatalks.club/docs/courses/llm-zoomcamp/
faq_url: https://datatalks.club/faq/llm-zoomcamp.html
hashtag: llmzoomcamp
published: true
```

Every cohort manifest has an explicit delivery and curriculum discriminator:

```yaml
schema_version: 2
content_id: "94444444-4444-4444-8444-444444444444"
identifier: "2027"                 # native YAML string; equals the directory
course: llm-zoomcamp
delivery: live                     # live | self_paced
published: false
start_date: "2027-06-07"            # optional/null for an unpublished draft
end_date: "2027-10-11"
curriculum: current                # current | github_archive
homework:
  - module: 01-agentic-rag
    source: cohorts/2027/homework/01-agentic-rag/homework.yaml
```

`curriculum` is a scalar discriminator. `current` means every numbered root
module is discovered and shared; `github_archive` opts the cohort out of
website lesson ingestion. Live and self-paced cohorts therefore share one
module and lesson graph. `homework` is an explicit list of `{module, source}` mappings;
The list is required: `[]` is valid for a draft or a cohort without assignments.
Self-paced phase one requires exactly `homework: []`; nonempty homework is rejected. New v2 homework uses the full
repository-relative path shown above. The checker validates that the YAML and
Markdown pair exists under the declaring cohort; it never discovers arbitrary
homework manifests by walking the tree. Existing archive homework may retain
the v1 direct path `cohorts/<id>/NN-module/homework.yaml` while it is being
converted. No mapping carries a second `content_id`: identity belongs to the
homework manifest itself.

Published live cohorts must have quoted ISO dates. Unpublished live drafts may
leave either date null. Self-paced practice has no generated deadline or
`due_at: null` grading convention in this first slice. If a live assignment is
mapped, its child homework manifest has a real `due_at`.

## GitHub-only historical archives

A materially different curriculum is kept complete for GitHub readers under
its cohort directory, but it is not a website lesson source:

```yaml
schema_version: 2
content_id: "95555555-5555-4555-8555-555555555555"
identifier: "2025"
course: llm-zoomcamp
delivery: live
published: true
start_date: "2025-05-05"
end_date: "2025-07-14"
curriculum: github_archive
archive:
  notice_path: cohorts/2025/README.md
homework:
  - module: null
    source: cohorts/2025/01-old-module/homework.yaml
```

`archive.notice_path` is the only archive metadata in the source manifest. It
must point to an existing Markdown notice inside that cohort. The website
derives the immutable GitHub URL from the validated source repository, the
incoming commit SHA and this path during import; a manifest cannot name its own
future commit. All archive descendants, including duplicate module/unit IDs,
are opaque and never become current module or lesson rows. Explicit `module: null`
homework mappings are still parsed and can remain operational for historical
submissions. An archive with no operational homework uses `homework: []`.

## Safety and checker

Run the checker with the same `uv`-backed command used in CI:

```bash
uv run --with pyyaml scripts/check-zoomcamp/check_zoomcamp.py PATH
```

The checker dispatches on the root schema before discovery. V2 rejects
duplicate YAML keys, implicit numeric identifiers, unknown fields, unbounded
values, traversal/absolute/URL-encoded paths, symlinks, missing module or
lesson pairs, duplicate numeric prefixes, missing archive notices, mixed
current/archive sources, and unreferenced current homework. Archive module
files are deliberately not loaded or ID-registered; only an explicitly mapped
archive homework manifest is admitted. Diagnostics contain repository paths and
YAML pointers, not lesson prose, answer material, tokens, registration data or
email addresses.

The existing v1 checker and manifests remain unchanged. A v2 failure is
atomic at import time: the website keeps the last successful projection and
does not fall back to another cohort, a README projection, or the newest
curriculum. Consumer projection and shared routes must be deployed and tested
before source repositories opt into schema v2.


## Implementation status (2026-09-08)

Landed and verified in `dtc-website` (all suites green, `makemigrations --check`
clean):

- **W0** route map frozen (`_docs/compatibility/shared-curriculum-route-aliases.json`),
  spec 02/04/09 and `open-decisions.md` amended, fail-closed contract tests.
- **W1** schema-2 parser with explicit v1/v2 dispatch, no-network layout gate,
  `scripts/verify_course_repository_curriculum.py`, versioned known-output fixture.
- **W2** shared-graph models + migration `0005`, idempotent
  `migrate_shared_curriculum` dry-run/apply backfill by stable ID.
- **W3** shared importer: one graph per course, explicit homework bindings,
  commit-derived archive URL, content-addressed managed assets, atomic retire.
- **W4/W5** canonical shared + `cohorts/<identifier>` routes, delivery-context
  service, no-creating-reads homework fix, templates, sitemap, cache isolation.
- **W6** kwarg window closed: every generated URL reverses the canonical
  namespace; legacy two-segment shapes remain inbound-only; middleware privacy
  classes and the adoption inventories cover the canonical names.
- **W7 tooling** `shared_curriculum_inventory` command: bounded per-cohort
  rollout inventory (identifier, delivery, source, mapped homework, counts,
  stable IDs, aliases) plus reviewed keep-current/become-archive decision
  validation, including the never-archive-self-paced and
  acknowledge-submitted-work invariants.

Rehearsal evidence: the release gate refuses the current llm-zoomcamp tree
(`layout_report_requires_schema_2` at `course.yaml`, HEAD `c16eb587…`) — the
checker-before-source-move gate is live.

Still open (needs owner/production input): the operator's per-cohort decisions
on production-like data (W7), the checker-gated source move (W8), ops
integration sign-off (W9), and independent acceptance gates (W10).

## Implementation status (2026-09-08)

Landed and verified in `dtc-website` (all suites green, `makemigrations --check`
clean):

- **W0** route map frozen (`_docs/compatibility/shared-curriculum-route-aliases.json`),
  spec 02/04/09 and `open-decisions.md` amended, fail-closed contract tests.
- **W1** schema-2 parser with explicit v1/v2 dispatch, no-network layout gate,
  `scripts/verify_course_repository_curriculum.py`, versioned known-output fixture.
- **W2** shared-graph models + migration `0005`, idempotent
  `migrate_shared_curriculum` dry-run/apply backfill by stable ID.
- **W3** shared importer: one graph per course, explicit homework bindings,
  commit-derived archive URL, content-addressed managed assets, atomic retire.
- **W4/W5** canonical shared + `cohorts/<identifier>` routes, delivery-context
  service, no-creating-reads homework fix, templates, sitemap, cache isolation.
- **W6** kwarg window closed: every generated URL reverses the canonical
  namespace; legacy two-segment shapes remain inbound-only; middleware privacy
  classes and the adoption inventories cover the canonical names.
- **W7 tooling** `shared_curriculum_inventory` command: bounded per-cohort
  rollout inventory (identifier, delivery, source, mapped homework, counts,
  stable IDs, aliases) plus reviewed keep-current/become-archive decision
  validation, including the never-archive-self-paced and
  acknowledge-submitted-work invariants.

Rehearsal evidence: the release gate refuses the current llm-zoomcamp tree
(`layout_report_requires_schema_2` at `course.yaml`, HEAD `c16eb587…`) — the
checker-before-source-move gate is live.

Still open (needs owner/production input): the operator's per-cohort decisions
on production-like data (W7), the checker-gated source move (W8), ops
integration sign-off (W9), and independent acceptance gates (W10).

### W8 rehearsal (2026-09-08, scratch copy in `.tmp/w8-rehearsal-v2/`)

The exact planned schema-2 transformation was applied to a scratch copy of
`llm-zoomcamp` (HEAD `c16eb587…`), committed as
`a7227d6aba0a77ddd1b2152007144838074ab5b3` (never pushed), and passed both
gates plus a full database import:

- ops `check-zoomcamp` v2 checker: 0 errors, 0 warnings;
- website `verify_course_repository_curriculum.py`: ok, schema 2 — 7 shared
  modules / 72 lessons, cohorts `2024`+`2025` as `github_archive`,
  `2026` live/current with 7 homework bindings, `self-paced`
  self_paced/current;
- `import_course_repository_curriculum` into a throwaway DB: 7 shared
  modules, 72 shared lessons, 7 homework (14 questions), 7 placements,
  2 archive cohorts with importer-derived archive URLs.

Stable identity is preserved: every existing v1 module/unit/homework/cohort
content ID carries over verbatim; new cohorts mint deterministic UUIDv5 IDs
(`self-paced` `dd9f78af-da18-5356-b722-6cfcb6c2edbc`, `2024`
`5cf52496-0ce7-5452-9767-45cdf8b647a5`, `2025`
`0bf1dad5-2052-533b-b382-7b2c0ef84d17`).

The first checker run caught 41 findings, all resolved in the rehearsal by
five mechanical fix classes the real W8 commit must apply:

1. drop `description_path` from `course.yaml` and `overview_path` from
   module manifests (ops checker rejects them; the website auto-reads
   `SITE.md`/module `README.md` without the keys);
2. quote homework `due_at` timestamps (v2 YAML requires strings);
3. move the non-numbered `07-project-example/content-processing-summary.md`
   to `docs/`;
4. strip trailing slashes on in-module directory links (`code/` → `code`);
5. rewrite links that resolve outside the module — cross-cohort archive
   links, `../../../project.md`, moved `homework.md`, and the moved summary —
   to their GitHub main-branch URLs; single-`..` links between sibling root
   modules are kept (the checker admits them).

The transform script and fix pass live in `.tmp/w8-rehearsal-v2/` for review.

### W8 rehearsal (2026-09-08, scratch copy in `.tmp/w8-rehearsal-v2/`)

The exact planned schema-2 transformation was applied to a scratch copy of
`llm-zoomcamp` (HEAD `c16eb587…`), committed as
`a7227d6aba0a77ddd1b2152007144838074ab5b3` (never pushed), and passed both
gates plus a full database import:

- ops `check-zoomcamp` v2 checker: 0 errors, 0 warnings;
- website `verify_course_repository_curriculum.py`: ok, schema 2 — 7 shared
  modules / 72 lessons, cohorts `2024`+`2025` as `github_archive`,
  `2026` live/current with 7 homework bindings, `self-paced`
  self_paced/current;
- `import_course_repository_curriculum` into a throwaway DB: 7 shared
  modules, 72 shared lessons, 7 homework (14 questions), 7 placements,
  2 archive cohorts with importer-derived archive URLs.

Stable identity is preserved: every existing v1 module/unit/homework/cohort
content ID carries over verbatim; new cohorts mint deterministic UUIDv5 IDs
(`self-paced` `dd9f78af-da18-5356-b722-6cfcb6c2edbc`, `2024`
`5cf52496-0ce7-5452-9767-45cdf8b647a5`, `2025`
`0bf1dad5-2052-533b-b382-7b2c0ef84d17`).

The first checker run caught 41 findings, all resolved in the rehearsal by
five mechanical fix classes the real W8 commit must apply:

1. drop `description_path` from `course.yaml` and `overview_path` from
   module manifests (ops checker rejects them; the website auto-reads
   `SITE.md`/module `README.md` without the keys);
2. quote homework `due_at` timestamps (v2 YAML requires strings);
3. move the non-numbered `07-project-example/content-processing-summary.md`
   to `docs/`;
4. strip trailing slashes on in-module directory links (`code/` → `code`);
5. rewrite links that resolve outside the module — cross-cohort archive
   links, `../../../project.md`, moved `homework.md`, and the moved summary —
   to their GitHub main-branch URLs; single-`..` links between sibling root
   modules are kept (the checker admits them).

The transform script and fix pass live in `.tmp/w8-rehearsal-v2/` for review.

### W7 production-like dry run + W9/W10 gates (2026-09-08, later session)

Executed against a production-like scratch database built from the real
source content (`.tmp/w7-e2e/db.sqlite3`; real llm-zoomcamp v1 tree at
`c16eb587…` imported through the standard pipeline: 1 course, cohort 2026 in
modules format, 7 cohort-owned modules, 72 units, 7 homeworks, 14 questions,
plus one seeded learner with enrollment and read state):

- **Backfill dry run** (`migrate_shared_curriculum --course llm-zoomcamp
  --dry-run`): 0 conflicts; would create 7 shared modules, 72 shared
  lessons, 7 placements, and copy 1 read state (`backfill-dry-run.json`).
- **Apply + replay**: shared rows created with old `Module`/`UnitReadState`
  rows retained; the read marker was copied; a replay apply reported 0 new
  rows — idempotent on real data (`backfill-apply.json`, `verify_state`).
- **Inventory** (`shared_curriculum_inventory`): pending report surfaces
  identifier/delivery/source/placements/homework/counts; a decisions file
  (`2026: keep-current`) validates to coverage "reviewed". The real operator
  pass against the production database remains the human gate.
- **Cutover simulation**: imported the checker-green v2 rehearsal commit
  (`a7227d6a…`) over the same database. Every acceptance invariant held:
  shared-module primary keys reused across the cutover (no row churn), live
  and self-paced cohorts reference the same `SharedCurriculum` row, 2026 →
  shared/live/current, self-paced → shared/self_paced/current, 2024/2025 →
  github_archive with importer-derived URLs, learner read state preserved.
- **W9 ops integration** (`.tmp/w9-ops/`, scratch copy of the checker-green
  tree): live 2027 bootstrap planned (content-free JSON plan), applied, and
  replayed byte-identically; self-paced bootstrap applied with the producer
  enforcing `homework: []`; the archive operation refused pre-write on the
  already-archived 2025 cohort (`missing_dependency`, exit 1 — no history
  overwrite); the fully operated tree still passes the v2 checker with
  0 errors, 0 warnings.
- **W10 browser evidence** (`.tmp/w7-e2e/screenshots/`): the shared pages
  served from the imported database in a real browser — family, module, and
  lesson at desktop (1280×800) and mobile (390×844), plus the
  `?cohort=2026` (terminal homework link + live dates) and `?cohort=self-paced`
  (ungraded/no-deadlines copy) delivery panels — with zero failed network
  requests.
- **Gap found and fixed by the browser pass**: managed lesson assets had no
  serving route. Added `/course-assets/lessons/<lesson-id>/<checksum>/<file>`
  (`courses/views/shared_course_assets.py` + route in `website/urls.py`):
  exact database-row lookup, managed store only, immutable public caching;
  4 view tests green. Also fixed a delivery-panel copy duplication
  ("self-paced self-paced") the mobile screenshot exposed; shared routes +
  page shell suites re-ran green (24 OK), and the final 95-test regression
  batch over every suite touched today is green with `makemigrations
  --check` clean.

### W7 production-like dry run + W9/W10 gates (2026-09-08, later session)

Executed against a production-like scratch database built from the real
source content (`.tmp/w7-e2e/db.sqlite3`; real llm-zoomcamp v1 tree at
`c16eb587…` imported through the standard pipeline: 1 course, cohort 2026 in
modules format, 7 cohort-owned modules, 72 units, 7 homeworks, 14 questions,
plus one seeded learner with enrollment and read state):

- **Backfill dry run** (`migrate_shared_curriculum --course llm-zoomcamp
  --dry-run`): 0 conflicts; would create 7 shared modules, 72 shared
  lessons, 7 placements, and copy 1 read state (`backfill-dry-run.json`).
- **Apply + replay**: shared rows created with old `Module`/`UnitReadState`
  rows retained; the read marker was copied; a replay apply reported 0 new
  rows — idempotent on real data (`backfill-apply.json`, `verify_state`).
- **Inventory** (`shared_curriculum_inventory`): pending report surfaces
  identifier/delivery/source/placements/homework/counts; a decisions file
  (`2026: keep-current`) validates to coverage "reviewed". The real operator
  pass against the production database remains the human gate.
- **Cutover simulation**: imported the checker-green v2 rehearsal commit
  (`a7227d6a…`) over the same database. Every acceptance invariant held:
  shared-module primary keys reused across the cutover (no row churn), live
  and self-paced cohorts reference the same `SharedCurriculum` row, 2026 →
  shared/live/current, self-paced → shared/self_paced/current, 2024/2025 →
  github_archive with importer-derived URLs, learner read state preserved.
- **W9 ops integration** (`.tmp/w9-ops/`, scratch copy of the checker-green
  tree): live 2027 bootstrap planned (content-free JSON plan), applied, and
  replayed byte-identically; self-paced bootstrap applied with the producer
  enforcing `homework: []`; the archive operation refused pre-write on the
  already-archived 2025 cohort (`missing_dependency`, exit 1 — no history
  overwrite); the fully operated tree still passes the v2 checker with
  0 errors, 0 warnings.
- **W10 browser evidence** (`.tmp/w7-e2e/screenshots/`): the shared pages
  served from the imported database in a real browser — family, module, and
  lesson at desktop (1280×800) and mobile (390×844), plus the
  `?cohort=2026` (terminal homework link + live dates) and `?cohort=self-paced`
  (ungraded/no-deadlines copy) delivery panels — with zero failed network
  requests.
- **Gap found and fixed by the browser pass**: managed lesson assets had no
  serving route. Added `/course-assets/lessons/<lesson-id>/<checksum>/<file>`
  (`courses/views/shared_course_assets.py` + route in `website/urls.py`):
  exact database-row lookup, managed store only, immutable public caching;
  4 view tests green. Also fixed a delivery-panel copy duplication
  ("self-paced self-paced") the mobile screenshot exposed; shared routes +
  page shell suites re-ran green (24 OK), and the final 95-test regression
  batch over every suite touched today is green with `makemigrations
  --check` clean.
