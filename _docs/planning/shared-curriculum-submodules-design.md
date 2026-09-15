# Shared curriculum sub-modules: design proposal

Status as of 2026-09-15: **design proposed, awaiting owner review. Not implemented, no
issue filed, no code written, no migrations exist.** Covers both the website/DB side
and, in "Course-repository representation", the `module.yaml` / `cohort.yaml` /
`zoomcamp-ops` checker side.

## Motivation

The owner wants a content model that can express a real structure already present in
LLM Zoomcamp's course material: a module split into multiple parts (e.g. module 1's
`README.md` has `## Part 1: RAG` and `## Part 2: Agents`), while most modules stay flat
with no sub-structure. Today this split exists only as informal prose headers in the
README — there is no machine-readable representation of it anywhere in the pipeline.

## What's actually in the source today (verified, not assumed)

Cloned `DataTalksClub/llm-zoomcamp` and inspected the real files, then cross-checked
against the local dev database:

- Module 1 (`01-agentic-rag/`): `README.md` has `## Part 1: RAG` (lessons 1-10),
  `## Part 2: Agents` (lessons 11-16), and `## Optional`. Module 3
  (`03-orchestration/`) has five parts; module 4 has two.
- `module.yaml` — the machine-readable schema-2 file the importer actually reads — has
  **no sub-module concept at all**: a flat `units:` list (`content_id`/`title`/`path`
  each), confirmed for all 7 llm-zoomcamp modules.
- `content_sync/course_repository_v2.py:269` (`_parse_module`) parses that flat list.
  The whole README is imported verbatim into `SharedModule.overview_markdown`
  (~line 300); the `## Part N` headers are never parsed into structure.
- `courses/services/curriculum_import.py:646-728` (`_import_shared_modules`/
  `_upsert_shared_lessons`) writes a flat `SharedModule -> SharedLesson` graph.
- Confirmed live in the local dev DB: llm-zoomcamp module 1 has exactly 16 flat
  `SharedLesson` rows, matching `module.yaml` 1:1.

**Conclusion: this is not an importer bug flattening real structure. There is no
structured second level in the source schema today** — "sub-modules" currently exist
only as informal prose in READMEs, not as data anywhere in the pipeline.

Also found while surveying the homework relationship:
`content_sync/course_repository_v2.py:557` (`_parse_homework_bindings`) explicitly
rejects a second homework binding for the same module (`duplicate_homework_module`),
and `CohortSharedModule` has a `UniqueConstraint(cohort, shared_module)` — **today's
schema already enforces zero-or-one homework per module.**


## What the owner actually wants from homework (second clarification)

The first pass read the owner as "sub-modules rarely have homework; treat that as a
rare edge case." The owner corrected this: **both shapes are first-class.**

- A small module: its homework belongs to the module itself. Simple, common.
- A large module split into parts: it can make *more* sense for a part to have its own
  homework than for one combined homework to cover every part.

In the owner's words: "I want a clear separation between [homework belonging to] the
module [itself] and units [that] can be just [grouped under it]."

## Additional verified facts that shape the homework model

Checked in the live dev DB and the `.tmp/w8-rehearsal-v2/llm-zoomcamp` clone
(commit `a7227d6`, 2026-09-08):

- **A `CohortSharedModule` row exists only because a homework binding exists.**
  `courses/services/curriculum_import.py:866-924` (`_import_shared_placements`)
  iterates `source.homework_bindings` and creates one placement per binding; a module
  with no homework gets no placement row, and a self-paced cohort gets zero rows. The
  placement's `position` is the binding's index in the cohort's `homework:` list, not
  the module's position. Despite the model docstring calling the homework "optional,"
  a placement *is* a homework anchor in practice.
- **The 2026 cohort binds exactly one homework per module (7 placements, 7 modules).**
  `cohorts/2026/cohort.yaml` has seven `{module, source}` pairs; the 2024 and 2025
  cohorts are archives with zero placements.
- **The real Homework 1 spans both parts of module 1.** `cohorts/2026/homework/
  01-agentic-rag/homework.md` opens with "we build a RAG system from scratch and then
  make it agentic — the same path as the module"; Q1–Q5 are Part 1 (search, RAG,
  chunking), Q6 "Turning it into an agent" is Part 2. So the current pedagogy for the
  one split module that exists is *one homework closing the whole module*, and any
  model that cannot express that loses real data.
- **Part 2 is deliberately separable.** Lesson 12 is titled "Quick RAG Revision
  (Optional) — also a standalone workshop entry point." A future delivery that runs
  Part 2 on its own (a workshop-style cohort) with its own homework is realistic, not
  hypothetical.
- **Homework is cohort-owned; the shared graph is not.** `Homework.course` is the
  cohort. The same `SharedModule` row serves every cohort, so "which node the
  homework closes" is necessarily a *per-cohort* decision — 2026 can close module 1
  with one homework while 2027 closes each part separately, against the same shared
  rows.
- **Progress and flow already count homeworks, not modules.**
  `courses/course_page_content.py:118` (`submission_progress`) counts one unit per
  homework/project; `courses/services/curriculum_flow.py:140-189` (`_shared_flow`)
  emits one `ModuleFlowItem` per placement. `courses/views/dashboard_homeworks.py` is
  keyed purely on `Homework` rows and never reads placements.
- **The first-pass constraint text had a real flaw.** Relaxing to a plain
  `UniqueConstraint(cohort, shared_module, sub_module)` does *not* enforce
  one-module-level-homework-per-module on PostgreSQL: NULLs compare distinct, so two
  rows `(cohort, module, NULL)` would both be accepted.

## Design reasoning

### 1. Is homework "attached to the leaf"?

Tempting reframing: a homework always closes the smallest unit a learner finishes
before moving on, so it should attach to the leaf — the module when it is flat, the
sub-module when it is split. That would remove the "nullable FK on both, pick one"
shape entirely (every module would carry an implicit default sub-module and every
homework and lesson would point at a sub-module).

**It fails the real data.** Homework 1 in 2026 closes *both* parts of module 1. Under a
strict leaf rule it would have to be anchored to Part 2 (the last leaf), which
misstates what it is: a module-level assignment that happens to come after Part 2's
lessons. The alternative — forbidding a module-level homework once a module is split —
would force the owner to split Homework 1 to gain any sub-module structure at all,
which is the opposite of "both shapes are first-class." It also inflates the common
flat case with an artificial hidden row per module that every renderer and URL must
learn to skip. Rejected.

The valuable half of the reframing survives as the **anchor rule**: every homework has
exactly one anchor node, and that anchor is either the module or one of its
sub-modules. "Closes" is what the anchor means for rendering: the homework appears
after the anchor's last lesson. That is enough for the rail, the flow, and progress —
nothing needs to know about leaves.

### 2. One polymorphic node type (self-referential) vs. a separate sub-module table?

Once homework-per-node is first-class, a self-referential `SharedModule.parent` looks
attractive: one table, one FK from the placement, and the existing
`UniqueConstraint(cohort, shared_module)` already means "one homework per node." Nothing
new to design.

**Verdict: the flat, separate `SharedSubModule` table still wins, and for a stronger
reason than the first pass gave.** The cost of a self-referential tree is not the
depth logic; it is that "root only" becomes an invariant every existing call site must
remember and none currently does:

- `SharedModule.objects.filter(curriculum__course=...)` appears in the module page
  (`courses/views/shared_course.py:103`), the family syllabus count
  (`courses/views/course_page_context.py:349`, which would print "9 modules" for
  llm-zoomcamp instead of 7), the sitemap (`content/public_views.py:1296`), the
  inventory, and the two-segment router. Each needs `parent__isnull=True` and a test
  guarding it, or silently regresses.
- `SharedModule`'s constraints are root-shaped: `(curriculum, slug)` and
  `(curriculum, position)` unique, plus the slug-vs-cohort-identifier collision check.
  Sub-modules number from 1 inside their parent and never own a URL segment, so every
  one of those constraints is wrong for a child row.
- `SharedLesson.module` drives the `/courses/<family>/<module>/<lesson>` URL. If lessons
  pointed at the child node, every URL reverse, the sibling/prev/next query, and the
  rail would need a parent hop; if they kept pointing at the root, the child node would
  be a grouping with no owned rows, i.e. exactly the separate table by another name.
- `overview_markdown`, `published`, `retired_at`, `summary`, and source provenance are
  module-level facts that a part does not have on its own.

A separate `SharedSubModule` table keeps every existing root query correct by
construction and gives the grouping exactly the fields it has (position, slug, title,
content id). The cost is one nullable FK on `SharedLesson` and one on
`CohortSharedModule`, each validated to agree with its required `module` FK. That is
the whole price, and it is paid once, in two `clean()` methods.

### 3. What the real llm-zoomcamp module 1 wants

One homework, today. Homework 1 is written as a single arc ("RAG, then make it
agentic") and its questions cover both parts in order. Splitting it would be a content
decision the owner has not made, and the website must not force it as a side effect of
introducing structure.

But the split is the reason the model must allow a sub-module anchor, not merely
tolerate it: Part 2 already advertises a standalone entry point, and a workshop-style
delivery of "Agents" alone would naturally carry its own homework. Because homework is
cohort-owned, that future cohort can anchor to `part-2-agents` while 2026 keeps its
module-level anchor, with no change to the shared rows. The model has to let two
cohorts choose differently against the same tree, which rules out putting the anchor
anywhere in the shared graph.

### 4. Constraints and what the renderers must know

**Invariant: one homework per anchor node per cohort, and each homework is counted
once.** On `CohortSharedModule`:

- `UniqueConstraint(fields=("cohort", "shared_module"), condition=Q(sub_module__isnull=True))`
  — one module-level homework per module per cohort (the existing rule, now
  conditional).
- `UniqueConstraint(fields=("cohort", "sub_module"), condition=Q(sub_module__isnull=False))`
  — one homework per sub-module per cohort.
- `UniqueConstraint(fields=("terminal_homework",), condition=Q(terminal_homework__isnull=False))`
  — a homework is anchored at most once. Today only the source-level
  `duplicate_homework_source` check guarantees this; making it a DB invariant is what
  lets progress math assume "one placement = one homework = one progress unit."
- `clean()`: `sub_module.module_id == shared_module_id` when `sub_module` is set. The
  existing same-course / same-cohort / self-paced checks are unchanged.
- The existing `(cohort, position)` constraint stays; two placements on one module
  simply occupy two positions.

The schema deliberately does **not** forbid a module-level and a sub-module-level
anchor coexisting on the same module in one cohort. Both have a well-defined
rendering ("after that node's last lesson"), a cross-row ban would need a trigger or
import-time check anyway, and the content checker is the right place for any stricter
editorial rule.

Renderers need one concept, `placement.anchor` (`sub_module or shared_module`):

- **Module page and rail** (`courses/views/shared_course.py:115-137`,
  `_shared_module_rail.html`): when the module has sub-modules, group lessons by
  sub-module in order; after each group emit the placement anchored to that
  sub-module, if any; after the last group emit the module-anchored placement, if any.
  Flat modules render exactly as today. Lesson URLs do not change — sub-modules get
  headings with fragment anchors on the module page, not their own routes.
- **Cohort flow** (`_shared_flow`): unchanged loop, one `ModuleFlowItem` per placement,
  ordered by placement position. `ModuleFlowItem` gains an optional `sub_module` so
  the cohort page can title the entry "Module 1 · Part 2: Agents." A module with two
  anchored homeworks yields two flow entries, which is correct: the learner hands in
  two things.
- **Progress** (`submission_progress`): no change. It counts homework/project rows,
  and the `terminal_homework` uniqueness above guarantees no homework appears twice.
  "N of M" is and remains N-of-M assignments, not modules.
- **Dashboard** (`dashboard_homeworks.py`): no change; it never reads placements.
- **Homework page crumb** (`courses/views/homework_context.py:76`): the shared
  equivalent resolves `homework.shared_module_placements` to its anchor and links the
  module page (with the sub-module fragment when anchored to a part).
- **Inventory** (`shared_curriculum_inventory.py:114-128`): add `sub_module_slug` to
  each placement row so the reconciliation report shows the anchor.

### 5. Refined design

The first-pass model shape survives scrutiny; what changes is its status and its
precision. Homework-per-sub-module is not an optional Phase E — it is the core of the
placement model and ships in the first schema slice with the constraints above.

- **`SharedSubModule`** — required FK to `SharedModule` (`related_name="sub_modules"`),
  `position`, `slug`, `title`, `summary`, source provenance. Unique on
  `(module, position)`, `(module, slug)`, and `(module, source_content_id)`. No URL of
  its own, no `overview_markdown`, no independent publish/retire state. One level only.
- **`SharedLesson.sub_module`** — nullable FK, validated to belong to `module`.
  Import rule: within one module either every lesson has a sub-module or none does,
  and each sub-module's lessons are contiguous in module position order matching
  sub-module order. Both are import-time checks (`curriculum_source_validators`), not
  DB constraints; they are what make "after the anchor's last lesson" unambiguous.
- **`CohortSharedModule.sub_module`** — nullable FK, the anchor selector, with the
  three constraints and the `clean()` rule from section 4. Reword the model docstring
  and the spec's `CohortSharedModule` bullet to say what the importer already does: a
  placement is one cohort's homework anchor on one node of a shared module.
- **Source schema (content repo).** `module.yaml` gains an optional `sub_modules:`
  list, each with `content_id`/`slug`/`title`/optional `summary` and its own
  `units:`; a module has either top-level `units:` or `sub_modules:`, never both (the
  all-or-nothing rule expressed as schema). `cohort.yaml` homework bindings gain an
  optional `sub_module: <slug>`; the expected manifest path becomes
  `cohorts/<id>/homework/<module>/<sub_module>/homework.yaml` when set, and
  `duplicate_homework_module` becomes a duplicate-anchor check. Nothing in the real
  llm-zoomcamp repo needs to change for 2026: its seven bindings stay module-level.
  The full repository-side design — field rules, why folders stay flat, the checker
  rules, and why there is no version bump — is the "Course-repository
  representation" section below.

## Blast radius (files that would need updating once sub-modules render)

- `courses/models/shared_curriculum.py` — models and constraints.
- `courses/curriculum_source_validators.py` — all-or-nothing and contiguity rules.
- `courses/services/curriculum_import.py:646-728, 866-924` — lesson and placement
  ingestion.
- `content_sync/course_repository_v2.py:269,557` — source parser and binding check.
- `courses/services/curriculum_source.py:61` — `ModuleSource`/`UnitSource`/
  `HomeworkBindingSource` dataclasses.
- `courses/views/shared_course.py:103,233`, `courses/templates/courses/
  shared_module.html:127-142`, `_shared_module_rail.html` — grouped lesson list and
  per-anchor homework link.
- `courses/services/curriculum_flow.py` — `ModuleFlowItem.sub_module`.
- `courses/views/homework_context.py` — anchor-aware crumb for shared cohorts.
- `courses/services/shared_curriculum_inventory.py` — anchor in placement rows.
- `courses/views/course_page_context.py:349`, `content/public_views.py:1296` — no
  change needed (root-only by construction); listed to record that this was checked.
- `_docs/specs/04-courses-and-cohorts.md:135` — placement definition.

## Proposed phasing

- **Phase A** — additive models: `SharedSubModule`, nullable `SharedLesson.sub_module`,
  nullable `CohortSharedModule.sub_module`, the three constraints from section 4, the
  `clean()` rules, admin. Zero renderer changes; ships with zero sub-modules in real
  data. The homework-anchor constraints are in this phase, not deferred.
- **Phase B** — importer support for optional `sub_modules:` in `module.yaml` and
  optional `sub_module:` in cohort homework bindings, plus the all-or-nothing and
  contiguity validators. Website side only; absent keys mean today's behaviour.
- **Phase C** — module page and rail group lessons by sub-module and place each
  homework after its anchor; `ModuleFlowItem.sub_module`; anchor-aware crumb and
  inventory.
- **Phase D** — the course-repository side, designed in the next section: checker
  rules and fixtures in `zoomcamp-ops` first, then the checker pin used by
  llm-zoomcamp's CI, then content authoring: module 1 (two parts), module 3 (five),
  module 4 (two) gain `sub_modules:`; 2026 bindings stay module-level, so Homework 1
  keeps closing the whole module.

## Course-repository representation

This is the Phase D design: how a split module is written in the course repository
itself, what the `zoomcamp-ops` checker must reject, and how the change rolls out
without breaking the repositories that never use it. Everything below was checked
against the sibling checkouts `../llm-zoomcamp` (commit `69ea63a`, 2026-09-15) and
`../zoomcamp-ops` (commit `35f8e41`, 2026-09-15), the website parser
`content_sync/course_repository_v2.py`, and the frozen schema-2 contract in
`_docs/planning/shared-course-content-implementation.md` (appendix) /
`zoomcamp-ops/docs/shared-curriculum-v2.md`. Note that the implementation handoff,
item 7 of "Scope corrections", already anticipated this: "defer grouping beyond the
frozen contract until its exact field format is agreed and tested." This section is
that field format.

### What the repositories look like today (facts the design has to respect)

- **Folders are flat and the tooling requires it.** Module 1 is sixteen `NN-kebab.md`
  files beside `module.yaml`, `README.md`, one shared `images/` directory (every
  lesson links `images/NN-...png` relatively) and one shared `code/` directory
  (`rag_helper.py` is used by both the RAG notebooks and `agents.ipynb`). Both
  consumers reject a unit path containing `/`: the checker's `_check_root_module`
  ("lesson path must be a direct NN-kebab.md sibling of module.yaml",
  `numbered_lesson_required`) and the parser's `_parse_module` (same code). Lesson
  frontmatter declares `prev_url`/`next_url` as bare sibling filenames
  (`11-agents-intro.md` has `prev_url: 10-rag-next-steps.md`), and prose cross-links
  do the same (`10-rag-next-steps.md:26` links `11-agents-intro.md`).
- **The author already expresses parts with flat numbering.** Module 4's files are
  `01`–`06` (search evaluation) and then `11`–`15` (RAG and agent evaluation): the
  numbering gap *is* the part boundary. Module 1 is `01`–`10` / `11`–`16`. The README
  `## Part N:` headings sit on top of that numbering; nothing is foldered.
- **Both consumers are strict about unknown keys.** The checker's `_keys` reports
  `v2_schema` "unknown key" for anything outside `{schema_version, content_id, title,
  units}` on a module and `{content_id, title, path}` on a unit; the parser's
  `_strict_mapping` fails with `unknown_key` (`content_sync/course_repository.py:310`)
  and a parser failure aborts the whole course import. A homework binding must be
  exactly `{module, source}` in both (`homework_mapping_missing` /
  `homework_mapping_invalid`), and `source` must equal
  `cohorts/<id>/homework/<module>/homework.yaml`.
- **Identity conventions.** Module slug = directory name; unit slug = file stem
  (`_slug(PurePosixPath(source_path).stem)`); every `content_id` is registered in one
  repository-wide namespace (`_register_id` / `_register_content_id`). A sub-module
  has no file, so its slug cannot be derived and must be written down.
- **The checker resolves modules before cohorts** (`SharedCurriculumChecker.run`:
  `_discover_root_modules` → `_check_lesson_navigation` → `_discover_v2_cohorts`), so a
  cohort binding can be validated against a module's declared sub-modules with no
  reordering.

### 1. `module.yaml`: nested `sub_modules:`, exclusive with `units:`

A module declares **either** a flat `units:` list (today's shape, unchanged) **or** a
`sub_modules:` list whose entries each carry their own `units:`. Never both, never
neither. Module 1 as it would actually be written:

```yaml
schema_version: 2
content_id: d9ca5cb3-b94c-4281-be7d-a2462559f02b
title: "Module 1: Agentic RAG"
sub_modules:
  - content_id: "5f0c1d9e-6a4b-4c3d-8e2f-100000000001"   # new UUID, minted once
    slug: rag
    title: "Part 1: RAG"
    summary: "Build a working RAG pipeline from scratch with keyword search."
    units:
      - content_id: 1e8059d3-1c63-47f6-b0a1-9b21c96ca1c6
        title: "Introduction"
        path: 01-intro.md
      - content_id: 74f9c194-1417-44a4-bb92-5147da22df0e
        title: "Environment"
        path: 02-environment.md
      # ... 03-rag.md through 09-data-ingestion.md, entries unchanged ...
      - content_id: babd94b3-e834-4431-85f0-ef6433c720f9
        title: "Wrap-up of Part 1"
        path: 10-rag-next-steps.md
  - content_id: "5f0c1d9e-6a4b-4c3d-8e2f-100000000002"
    slug: agents
    title: "Part 2: Agents"
    summary: "Put the LLM in charge of the search decisions."
    units:
      - content_id: 7e94f6bc-7015-4021-a77c-8e6f35fd2fcf
        title: "Agents"
        path: 11-agents-intro.md
      - content_id: a71b2571-5cbd-4554-8b8c-d86657eac1db
        title: "Quick RAG Revision (Optional)"
        path: 12-rag-revision.md
      # ... 13-function-calling.md through 16-other-frameworks.md, unchanged ...
```

Field rules for a `sub_modules[]` entry:

| key | required | rule |
| --- | --- | --- |
| `content_id` | yes | quoted UUID, registered in the same repository-wide namespace as modules and units; never reused |
| `slug` | yes | `[a-z0-9]+(-[a-z0-9]+)*`; unique among the module's sub-modules; must not equal any unit slug (file stem) of the same module. Carries no ordinal: position is list order, and the slug is the identity `cohort.yaml` binds to |
| `title` | yes | non-empty, ≤ 200 chars. May carry the ordinal exactly as module titles already do (`"Module 1: Agentic RAG"` → `"Part 1: RAG"`); the website renders it verbatim |
| `summary` | no | plain text, ≤ 500 chars, the one-paragraph lead the README puts under each `## Part` heading. Optional because the README is GitHub-facing decoration and stays the informal copy |
| `units` | yes | non-empty list of exactly today's unit mappings, validated by exactly today's unit rules |

Order of parts is the order of the `sub_modules:` list; order of lessons within the
module is the flattened order (part 1's units, then part 2's). That flattened list is
what every existing consumer keeps seeing: `prev_url`/`next_url` derivation, the
orphan check, `duplicate_number_prefix`, `UnitSource` positions and lesson URLs are
all computed over it and are byte-for-byte what they are today. **A module with zero
sub-modules changes nothing**: the four flat llm-zoomcamp modules, every ML and DE
module, and every fixture stay untouched, and `units:` remains required whenever
`sub_modules:` is absent.

Why nested rather than a per-unit `sub_module: <slug>` attribute on a flat `units:`
list (the main alternative considered): the nested shape makes the two invariants the
DB design needs — all-or-nothing membership and contiguity of each part's lessons —
**unrepresentable rather than validated**. A flat list with a per-unit key can express
"units 1–5 tagged, unit 6 untagged, 7–10 tagged", which then needs two more checker
rules and two more parser rules, and it puts sixteen repeated `sub_module:` lines in
the file where one forgotten line is the likeliest authoring mistake. The nested shape
also reads like the README (`## Part 1: RAG` followed by its list), which is what the
reviewer compares it against. Its only cost is a one-time re-indent of the existing
unit entries in three files. A third option, a `sub_modules:` block that *references*
unit paths already listed in `units:`, states every path twice and was rejected on the
contract's own "one fact, one place" rule (the same reason schema 2 retired restating
`slug`/`title` on units).

### 2. Folders stay flat; `module.yaml` is the only place the grouping lives

**Recommendation: do not move any file.** Sub-folders such as
`01-agentic-rag/part-1-rag/03-rag.md` were considered and rejected on the evidence
above:

- They break every relative `images/...` link in sixteen lessons (or force splitting a
  single `images/` directory that is currently keyed by lesson prefix), split the one
  `code/` directory that both parts share, and turn every in-part `prev_url`/`next_url`
  and prose cross-link into `../part-1-rag/...`. That is a mass edit with no content
  value, in files whose whole point is to be stable.
- They change lesson identity: unit slugs derive from the file stem, so slugs would
  survive only if the parser learned to ignore directories, at which point the folder
  carries no information the YAML does not.
- They require relaxing `numbered_lesson_required` in both consumers — the rule that
  keeps the v2 layout auditable — for a benefit the flat numbering already provides.
- Folder plus YAML would be *two* statements of membership that can drift, and the
  checker would need a rule that they agree. One statement, in `module.yaml`, cannot
  drift from itself.

The sustainable authoring workflow is therefore what the author already does: number
lessons in ranges (module 4's `06` → `11` gap), keep `## Part N` headings in the README
for GitHub readers, and add the `sub_modules:` grouping in `module.yaml` once. Adding a
lesson to a part means adding a numbered file and one unit entry inside that part's
list, exactly the motion used today. The README headings stay unchecked, as the
contract already treats the README as decoration; a future rule that README `## Part`
headings match sub-module titles is possible but is not proposed.

### 3. `cohort.yaml`: an optional `sub_module:` key on a homework binding

A binding is `{module, source}` (module anchor) or `{module, source, sub_module}`
(sub-module anchor). `module` stays required in both so the binding reads and resolves
without searching, and so that "module not found" and "part not found" are distinct
diagnostics. The anchor is `(module, sub_module or None)` — exactly the DB design's
one-anchor rule: module or one sub-module, never both, never neither.

The homework directory mirrors the anchor tree:

| anchor | `source` must equal |
| --- | --- |
| module | `cohorts/<id>/homework/<module>/homework.yaml` (today's rule, unchanged) |
| sub-module | `cohorts/<id>/homework/<module>/<sub_module>/homework.yaml` |

`homework.md` stays co-located next to its `homework.yaml` in both cases. The nested
directory keeps a module's assignments under one folder and lets a module-level and a
part-level homework coexist on disk when a cohort uses both anchors.

The 2026 manifest needs **no change** — all seven bindings stay module anchors, so
Homework 1 keeps closing the whole module. A later cohort that closes each part
separately would write:

```yaml
homework:
  - module: 01-agentic-rag
    sub_module: rag
    source: cohorts/2027/homework/01-agentic-rag/rag/homework.yaml
  - module: 01-agentic-rag
    sub_module: agents
    source: cohorts/2027/homework/01-agentic-rag/agents/homework.yaml
  - module: 02-vector-search
    source: cohorts/2027/homework/02-vector-search/homework.yaml
```

and a workshop-style delivery of Part 2 alone would carry just the `agents` binding.
Homework `slug`s stay cohort-unique as today (`homework-01-agents` is the natural
name); nothing about `homework.yaml` itself changes.

Rejected alternatives: a path-like `module: 01-agentic-rag/agents` overloads a key
that both consumers compare for exact equality (the failure would surface as a
misleading `curriculum_source_mismatch`), and a nested `anchor: {module, sub_module}`
block adds structure for a two-key fact.

### 4. What the `zoomcamp-ops` v2 checker must validate

Two new rule codes in the `RULES` table, in the existing `LAYOUT` class, plus reuse of
existing codes. Shape errors keep using `v2_schema`; semantic errors get named codes
so an allowance can target them, as for every other v2 rule.

`module.yaml` (`_check_root_module`):

1. `v2_schema` — exactly one of `units` / `sub_modules` is present (the `_keys`
   allowed set gains `sub_modules`; the required set drops `units` and the exclusivity
   is checked explicitly: "module declares both units and sub_modules" / "module
   declares neither").
2. `v2_schema` — `sub_modules` is a non-empty list of mappings; each entry's key set is
   exactly `{content_id, slug, title, units}` plus optional `summary`; `title` and
   `summary` are non-empty strings within their limits; each entry's `units` is a
   non-empty list.
3. `sub_module_invalid` (new) — fewer than two sub-modules ("a module with one part is
   a flat module; use units"); a `slug` that is not kebab-case, repeats another
   sub-module's slug, or equals a unit slug (file stem) of the same module.
4. `content_id` of each sub-module goes through `_register_id`, so cross-repository
   duplicates fail with the existing rule and no new code.
5. Every unit inside every sub-module runs through the **unchanged** per-unit block:
   exact keys, `_register_id`, direct-sibling `NN-kebab.md` path
   (`numbered_lesson_required`), file exists, `duplicate_number_prefix` computed over
   the flattened module-wide list (which is also what rejects a lesson claimed by two
   parts), and `current_lessons` appended in flattened order so
   `lesson_navigation_mismatch` keeps deriving the same sequence. The orphan check
   (`curriculum_source_mismatch`, "lesson exists but is absent from module.yaml
   units") runs over the flattened `declared_paths`.
6. `self.root_modules[slug]` grows from a manifest path to a record that also holds
   the ordered sub-module slugs, for use by the cohort pass.

`cohort.yaml` (`_check_homework_mappings`):

7. `homework_mapping_missing` — a binding's key set is `{module, source}` or
   `{module, source, sub_module}` (message updated from "needs exactly module and
   source").
8. `homework_anchor_invalid` (new) — `sub_module` is not a string; names a module that
   declares no `sub_modules` ("01-agentic-rag is a flat module"); or names a slug that
   is not one of that module's declared sub-modules.
9. `homework_anchor_invalid` — duplicate anchor: `(module, None)` mapped twice (today's
   "module is mapped more than once", now scoped to the module anchor) or
   `(module, sub_module)` mapped twice. A module anchor and a part anchor on the same
   module are **not** a duplicate.
10. `homework_mapping_missing` — `source` equals the path the anchor prescribes (table
    in section 3). The containment (`_inside`), `endswith("/homework.yaml")`,
    co-located `homework.md`, `_check_homework_manifest` and the `homework_unreferenced`
    walk are unchanged and keep working because the walk is by filename, not depth.
11. `archive_module_reference` — a `github_archive` cohort may not carry `sub_module`
    (same rule as `module` must be null there).
12. **Optional editorial rule, pending open question 1:** `mixed_homework_anchors` —
    a module with both a module-level and a part-level anchor in one cohort. The
    schema permits it; if the owner wants it forbidden, this rule is where it lives,
    and the existing `allow:` config gives a per-repository escape hatch. Not enabled
    unless the owner says so.

Fixtures and tests (`scripts/check-zoomcamp/fixtures/shared-current-v2/`,
`test_shared_curriculum.py`): add a third root module that is split into two parts
rather than rewriting `01-agentic-rag` (which every existing test relies on), bind a
part anchor in the `2027` cohort fixture, keep the valid fixture green, and add one
mutation test per rule above in the existing `tmp_path`-copy style. The website
parser fixture `content_sync/tests/fixtures/course_repository/llm_zoomcamp_shared/`
gets the same split module and binding so producer, checker and parser are frozen
on one representation — the discipline the handoff's item 6 requires for any new
field. The `templates/module-README.md` and the `process-course-video` skill's
authoring notes need a short paragraph on where a new lesson's unit entry goes in a
split module; no other `zoomcamp-ops` script reads `units:`.

Website parser parity (Phase B, listed here because it is the same rule set):
`_parse_module` accepts `sub_modules` with the exclusivity rule, flattens to the same
`units` tuple it builds today, emits a new `SubModuleSource` tuple on `ModuleSource`
(content id, slug, title, summary, and the unit index range), and `UnitSource` gains a
`sub_module_slug: str | None`; `_parse_homework_bindings` accepts the optional
`sub_module`, validates it against `module_by_slug[module].sub_modules`, and
`HomeworkBindingSource` gains `sub_module: str | None`. `duplicate_homework_module`
becomes a duplicate-anchor check with the same name space as rule 9.

### 5. No version bump: an additive schema-2 extension, gated by strictness

**Stay at `schema_version: 2`.** The reasoning, in order of weight:

- **Nothing that exists changes meaning.** A manifest without `sub_modules` or
  `sub_module` is byte-for-byte a valid schema-2 manifest with identical semantics,
  and every existing key keeps its meaning in a manifest that does use them. That is
  the definition of an additive extension; a version bump is for the case where an
  existing key's meaning changes, which is not this case.
- **The premise "a schema-2 parser simply ignores the key" is false, and that is a
  good thing.** Both consumers refuse unknown keys (`unknown_key`, `v2_schema`). So an
  unaware consumer never silently flattens a split module into an ungrouped one, and
  never silently binds a part homework to the whole module: it fails loudly at the
  first `sub_modules:` it meets. The strictness is the safety property. Do not add a
  lenient "ignore unknown keys" mode to gain false coexistence.
- **A bump would be strictly worse for coexistence.** `schema_version` is per file and
  `v2_mixed_version` forbids mixing versions within a repository, so "schema 3" would
  force every `course.yaml`, `cohort.yaml`, `module.yaml` and `homework.yaml` in a
  repository to change in one commit — for a feature three modules use — and would
  require a new dispatch branch in `checker_for_repo` and the parser for no semantic
  gain. It would also *not* let an old consumer read the new repository, which is the
  only thing a bump could have bought.
- **Coexistence is per module, gating is per repository.** Because the feature is
  opt-in per `module.yaml`, old and new content coexist trivially inside one
  repository and across repositories: llm-zoomcamp modules 2, 5, 6, 7 and the whole ML
  and DE trees are untouched. The gate is: the moment one module in a repository
  opts in, that repository requires consumers that understand the extension. That is
  precisely the rollout rule already written in
  `zoomcamp-ops/docs/shared-curriculum-rollout.md`: "Source publication must not
  precede the consumer that understands it."

Rollout order, following that rule:

1. `zoomcamp-ops`: fixtures and the rules in section 4, tests green, spec doc
   (`shared-curriculum-v2.md`) gains the `sub_modules` / `sub_module` sections.
2. Website: Phase B parser and importer, with the mirrored fixture; deploy; run the
   dry-run import against the `zoomcamp-ops` split fixture.
3. Update the reviewed commit pin of the reusable checker workflow that llm-zoomcamp's
   CI calls (the rollout doc requires a pinned reference, so a new pin is a reviewed
   change, not a drift).
4. llm-zoomcamp: author `sub_modules:` for modules 1, 3 and 4 in three separate,
   individually revertible commits; CI green; merge; import. `cohorts/2026/cohort.yaml`
   is not touched.

Rollback at any step is a revert of that step's commit; consumers keep accepting both
shapes indefinitely, so there is no window in which reverting content requires
reverting code.

## Open questions for the owner

Resolved by this revision (no answer needed unless the owner disagrees):

- Model shape: separate one-level `SharedSubModule` table, not a self-referential
  tree — see section 2 for why.
- Homework attachment: a per-cohort anchor on the placement (`module` or one
  `sub_module`), both first-class, shipped in Phase A — not an optional Phase E.
- llm-zoomcamp 2026: Homework 1 stays one module-level assignment; the structure
  migration must not split it.

Resolved by the course-repository section (no answer needed unless the owner
disagrees):

- Repository representation: nested `sub_modules:` in `module.yaml`, exclusive with
  `units:`; each part has `content_id`, `slug`, `title`, optional `summary`, and its
  own `units:`. A module with no parts needs no change.
- Folders stay flat. No lesson, image or code file moves; the grouping lives only in
  `module.yaml`, matching how the author already numbers parts in ranges.
- Homework binding: optional `sub_module: <slug>` beside the required `module`, with
  the manifest at `cohorts/<id>/homework/<module>/<sub_module>/homework.yaml`.
- No schema version bump. Additive schema-2 extension; both consumers' strict
  unknown-key rejection is the rollout guard, and the order is checker → website →
  checker pin → content.

Still genuinely open:

1. **May a module carry both a module-level and a sub-module-level homework in the
   same cohort?** The schema allows it (each has a clear rendering). If the owner wants
   it forbidden as an editorial rule, that is checker rule 12 (`mixed_homework_anchors`)
   in section 4 of the repository representation — not the DB.
2. **Phase D timing** — whether to author `sub_modules:` for llm-zoomcamp modules 1, 3
   and 4 as soon as the checker and website accept it, or leave the website capable
   but unused. The rollout order is fixed either way.
3. **Sub-module pages** — this design gives parts headings and fragment anchors on the
   module page, not their own URLs. Confirm that is enough; a standalone
   "Part 2: Agents" workshop entry point might eventually want a linkable page. The
   repository representation does not preclude it: the sub-module `slug` is already a
   stable identity, so a route could be added later without touching content.
4. **Sub-module `slug` and `title` conventions** — the proposal is ordinal-free slugs
   (`rag`, `agents`) and README-matching titles (`"Part 1: RAG"`), mirroring how module
   titles already carry "Module 1:". Confirm, or choose ordinal-free titles and let the
   website print "Part N" from position.
