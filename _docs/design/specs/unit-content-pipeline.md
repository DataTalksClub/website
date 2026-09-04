# Unit content pipeline — how lesson content reaches the website, and how to unify it

**Analysed at** `main` **`2c97886`**, working tree carrying the uncommitted
`SITE.md` parser change. Course checkouts: `llm-zoomcamp@cc92b21`,
`machine-learning-zoomcamp@2074193`, `ai-dev-tools-zoomcamp@bc67657`. All three moved
during this analysis — the `SITE.md` commits landed mid-session — so re-verify counts
before acting.

**Scope:** the `modules` curriculum format only. `Cohort.curriculum_format == "legacy"`
(`courses/models/curriculum.py:42`, `:105`, `:222`) is out of scope.

**Adjacent, not overlapping:** `_docs/design/specs/data-migration-architecture.md` owns
*how data is loaded* (importer scripts, CMP-versus-repo precedence, homework binding).
This document owns *how the course repositories are shaped and read*. Where they touch —
homework slug binding, family description ownership — this document defers to it.

**Owner decision, already taken:** *"let's unify. it should be in cohorts"*. Section 4
records the consequence; it does not reargue the choice.

---

> **Superseded in part, 2026-09-03.** Stages (1)–(3) below described three separate
> readers. There is now one course path: `content_sync/course_repository_ingest.py`,
> reached by the signed push webhook and by `scripts/prod/sync_course_repositories.py`, both
> reading a `git archive` tar of the whole commit. The snapshot manifest
> (`scripts/build_course_modules_manifest.py`), the local importer
> (`courses/services/local_course_modules.py`) and the `PRODUCTION_PREP_COURSE_REPOSITORIES`
> list are gone; registered `ContentSource` rows say which repositories exist. The parse
> and projection stages below still hold. See
> `_docs/runbooks/course-content-push-and-pull.md`.


## 1. The end-to-end path

From a Markdown file in a course repository to rendered HTML at
`/courses/<family>/<year>/modules/<module>/<unit>`.

```
 (1) CHECKOUT        Makefile:426-448  production-prep-course-sources
     git clone/reset --hard the three repos from PRODUCTION_PREP_COURSE_REMOTE
     (today $HOME/git, because the 2026 curricula are unpushed) onto main.

 (2) MANIFEST        scripts/build_course_modules_manifest.py
     _manifest_paths()      :102-120  select course.yaml, module.yaml, cohort.yaml,
                                      homework.yaml at their legal placements only
     _referenced_paths()    :156-177  add the .md each manifest names, plus lesson
                                      frontmatter `code:` attachments
     _source_record()       :204-246  refuse a dirty tree, a non-main branch, or a
                                      commit not reachable on public GitHub unless
                                      --allow-unpublished-commit gives a reason;
                                      SHA-256 per file + snapshot_sha256 over the set
     -> one JSON manifest.  Images are NOT selected.  Nothing outside the selection
        ever enters the snapshot.

 (3) SNAPSHOT        courses/services/local_course_modules.py
     _validate_source_record() :411   re-check repo identity, branch, snapshot digest
     _read_snapshot()          :356   read the selected files as bytes

 (4) PARSE           content_sync/course_repository.py   (pure, no Django)
     _validate_manifest_placement() :633-649  reject a manifest in an illegal place
     _parse_course()                :708-765  course.yaml -> CourseSource
     _parse_site_description()      :767-796  SITE.md -> description (README never read)
     _parse_module()                :798-888  module.yaml -> ModuleSource + UnitSource[]
       module slug  <- parent directory name           :809-813
       unit slug    <- markdown filename stem          :842-846
       unit title   <- module.yaml /units/N/title      :874-876
       unit order   <- list order in /units            :822
       unit body    <- the .md, frontmatter stripped   :861-869
     _parse_lesson_frontmatter()    :481-545  video_url + code[] out of the body
     _parse_homework()              :969-1217 homework.yaml + its homework.md
     _parse_cohort()                :1219-1374 cohort.yaml /flow orders modules+projects

 (5) IMPORT          courses/services/curriculum_import.py  (one transaction)
     _upsert_course()   :277-355   Course row; description only if SITE.md existed :339
     _upsert_cohort()   :375-424   Cohort row, curriculum_format from /format :419
     _import_modules_cohort() :434  CurriculumFlowItem rebuilt from /flow
     _upsert_module()   :656-692   Module.position = flow order, slug, terminal_homework
     _upsert_unit()     :705-740   Unit.position/slug/title/content_markdown,
                                   rendered_html, video_url, code_sources, provenance

 (6) REQUEST         courses/views/unit.py:94-148
     _unit_content()              :74-82   drop the body's leading title heading
     rewrite_unit_markdown_links() unit_links.py:82   repo paths -> site routes or
                                                       upstream GitHub URLs
     rewrite_unit_image_sources()  unit_assets.py:181  relative images ->
                                                       raw.githubusercontent.com
     render_markdown()             registration.py:243 mistune(escape=False) + bleach
     -> courses/templates/courses/unit.html
```

### Where each piece of a unit comes from

| Piece | Source | Code |
| --- | --- | --- |
| Title | `module.yaml` `/units/N/title` | `course_repository.py:874-876` |
| Slug (URL) | the Markdown **filename stem** | `course_repository.py:842-846` |
| Ordering | list position in `module.yaml` `/units` | `curriculum_import.py:701-703` |
| Body | the `.md` file, frontmatter removed | `course_repository.py:861-869` |
| Video | lesson frontmatter `video_url` | `course_repository.py:511-516` |
| Code links | lesson frontmatter `code[]` | `course_repository.py:518-542` |
| Homework binding | `cohort.yaml` `/flow/N/module/homework` | `course_repository.py:1318-1334` |
| Images | **not imported** — resolved at render to `raw.githubusercontent.com` | `unit_assets.py:130-153` |
| Module slug (URL) | the module **directory name** | `course_repository.py:809-813` |
| Cohort year | `cohort.yaml` `/year` (an int, distinct from `/identifier`) | `course_repository.py:1366` |

**Verified.** Two facts follow and both matter for section 5:

- Public URL segments come from **directory and file names**, never from a manifest
  field. `module.yaml` may state `slug:`, but it is validated to *equal* the path-derived
  slug, never to override it (`course_repository.py:814-817`, `:847-850`).
- Images are never snapshotted. They resolve from `Unit.source_path`'s directory at
  render time. **Moving a Markdown file without moving its images beside it silently
  404s every picture.**

---

## 2. Divergence table

Twenty differences, with hard counts. `V` = verified by measurement against the three
checkouts; `I` = inferred.

| # | Divergence | llm-zoomcamp | machine-learning-zoomcamp | ai-dev-tools-zoomcamp | What the importer does |
| --- | --- | --- | --- | --- | --- |
| 1 | `module.yaml` location | `cohorts/2026/<mod>/` ×7 | `<mod>/` at root ×9 | `<mod>/` at root ×4 | **Accepts both by design.** `course_repository.py:641-643`, `:657-674`; mirrored in `build_course_modules_manifest.py:111-115`. V |
| 2 | Module-slug uniqueness domain | scoped to `cohorts/2026` | scoped to `"shared"` | scoped to `"shared"` | **Silently different namespace.** `course_repository.py:571-576`. Two cohorts cannot both own a root module of the same slug; under `cohorts/` they can. V |
| 3 | Unit file placement in the module dir | `lessons/NN-name.md` 72/72 | flat beside `module.yaml` 105/105 | single `lesson.md` 4/4 | Absorbed — `/units/N/path` is a free manifest-relative reference, `course_repository.py:834-841`. V |
| 4 | Lessons per module | 72 / 7 = 10.3 | 105 / 9 = 11.7 | 4 / 4 = **1.0** | Nothing. AI Dev Tools has no lesson granularity: its module page lists exactly one lesson. V |
| 5 | Lesson frontmatter | 55/72 blocks, 55 `video_url`, 6 `code` entries | 0/105 | 0/4 | Absorbed — a body with no `---` opener yields empty metadata, `course_repository.py:490-492`. V |
| 6 | Body opening heading level | `# ` 72/72 | `## ` 103/105, `# ` 2/105 | `# ` 4/4 | **Special-cased.** `courses/views/unit.py:64` matches `#{1,2}` *and* requires the heading text to equal the unit title. V |
| 7 | Heading actually duplicated on the page | 0/72 | **0/105** | **4/4** | Body says `# Module N — Title`; manifest title omits `Module N — `, so equality fails and both headings render. V |
| 8 | Numeric title prefixes (`1.1 …`) | 0/72 | **102/105** | 0/4 | **Special-cased for display only** — `courses/templatetags/curriculum_titles.py:26`. Applied in the rail and module list, **not** to the unit `<h1>` (`unit.html:140`), so ML shows `1.1 Introduction…` as the heading and `Introduction…` in the rail. V |
| 9 | Relative image references | 0 | **102** | 0 | Rewritten to `raw.githubusercontent.com` at the **branch tip**, `unit_assets.py:130-153`. **3 of the 102 targets do not exist in the repository** and will 404. V |
| 10 | Raw HTML `<img>` vs Markdown images | — | 95 raw `<img>`, 4 Markdown, 3 same-dir | — | Requires `escape=False` (`registration.py:234`) plus the bleach allowlist (`:110-163`) and the `alt` backfill (`unit_assets.py:174-178`). Only ML exercises this path. V |
| 11 | Relative `.md` links | 148 | 199 | 2 | Rewritten to canonical unit/module/homework routes, `unit_links.py:156-173`. V |
| 12 | Relative non-`.md` links | 45 | 219 | 8 | **272 total.** Rewritten to upstream GitHub blob/tree URLs, `unit_links.py:175-188` (landed in `2c97886`). V |
| 13 | Links escaping the module directory | 19 (incl. `cohorts/2024/…`, `cohorts/2025/…`) | 103 — all the single form `](../)` labelled "Machine Learning Zoomcamp course" | 9 (incl. `../cohorts/2026/<hw>/homework.md`, `../05-agent-capabilities/`) | Resolved relative to the unit's own directory. **These are the links that change meaning when a file moves depth.** V |
| 14 | How a lesson links its homework | `../homework.md` ×1 | `homework.md` ×18 | `../cohorts/2026/<x>/homework.md` ×2 | **Three shapes, two mechanisms.** LLM/AIDT hit `homework_by_instructions` (`unit_links.py:167`); ML hits the **module-directory fallback heuristic** (`:168-171`) because its root `<mod>/homework.md` is a *different file* from the cohort's real instructions. V |
| 15 | Homework directory name vs module directory name | identical (`cohorts/2026/<mod>/` holds both) | same names, different trees (`<mod>/` vs `cohorts/2026/<mod>/`) | **different names** — module `01-ai-native-workflow`, homework `cohorts/2026/01-overview` | Nothing — the parser only checks the cohort segment, `course_repository.py:1315-1317`, `:1332-1334`. V |
| 16 | Homework slug scheme | `homework-01…07` | `hw01…hw10` (no `hw07`) | `hw01…hw04` | Owned by `data-migration-architecture.md` §3.5/§7.2. Not restated here. V |
| 17 | Module directories with no `module.yaml` | 0 | `11-kserve` (and no `07-*` at all) | `05-agent-capabilities` | Silently omitted from the curriculum — no diagnostic, no page. V |
| 18 | Module `README.md` | 7 | 10 | 5 | **Never imported.** `module.yaml` has no overview field (`course_repository.py:803`), so module pages have no prose. 22 files ignored. V |
| 19 | `course.yaml` slug vs published family slug | `llm-zoomcamp` = family | `ml-zoomcamp` = family | `ai-dev-tools-zoomcamp` ≠ family `ai-dev-tools` | **Special-cased** — `canonical_family_slug()` at `curriculum_import.py:283`, cohort prefix rewrite at `:357-373`. V |
| 20 | Course description source | `SITE.md`, 128 B | `SITE.md`, 162 B | `SITE.md`, 141 B | Uniform **as of an hour ago**. All three previously carried `description_path: README.md`. See the correction below. V |

### Corrections to premises in the brief

Three premises this work was handed are **wrong at `2c97886`**. Recording them so they are
not repeated.

1. **"`course.yaml` carries `description_path`."** No longer true. All three repos dropped
   it in their newest commit and publish `SITE.md` instead; the parser's allowed-key set
   (`course_repository.py:715-741`, uncommitted) no longer accepts `description_path` at
   all. Had those repo commits *not* landed, the in-flight parser would have rejected
   every one of them with `unknown_key /description_path`.

2. **"`_LEADING_H1_RE` strips `#` but not `##`, so 103 of 105 ML units render a duplicate
   heading."** **Refuted.** No such symbol exists. `_LEADING_TITLE_HEADING_RE`
   (`courses/views/unit.py:64`) matches `#{1,2}` and additionally requires title equality.
   Measured: **72/72 LLM and 105/105 ML units are deduped correctly; 0 duplicate.** The
   103 figure was the count of ML bodies opening with `## `, which is real, but the
   consequence was not. The defect that *does* exist is elsewhere — **4/4 AI Dev Tools
   units duplicate**, because their bodies open `# Module 1 — AI-Native Developer Workflow`
   while the manifest title is `AI-Native Developer Workflow`.

3. **"`unit_links.py:97` only rewrites `.md` targets, breaking 272 relative links."** The
   **272 is exactly right** (45 + 219 + 8, measured). The consequence is **fixed** as of
   `2c97886` — `unit_links.py:175-188` now sends every other repository-relative target to
   the upstream GitHub blob/tree URL. Cited line 97 is now the unit queryset.

### Divergences the importer absorbs — special case versus heuristic

A **special case** is deliberate, named, and testable. A **heuristic** guesses, and is a
latent bug. Separating them:

**Deliberate special cases (maintenance cost, not risk):**

- Dual `module.yaml` placement — #1, `course_repository.py:641-643`.
- `#`-or-`##` leading heading with title equality — #6, `unit.py:64`.
- Ordinal title prefix stripped for display — #8, `curriculum_titles.py:26`.
- Repository slug normalised to the family slug — #19, `curriculum_import.py:283`.
- `SITE.md` present or absent — #20, `course_repository.py:767-796`.

**Heuristics that paper over a divergence (latent bugs):**

- **#14, the `homework.md` module-directory fallback** (`unit_links.py:168-171`). It fires
  only for ML, and only because ML keeps a *stub* `<mod>/homework.md` (≈600 B, a list of
  past-cohort links) distinct from the real 2026 instructions at
  `cohorts/2026/<mod>/homework.md` (≈4–8 kB). The reader clicks "Homework" in a lesson,
  the link resolves through a directory-name guess, and lands on the right page by
  coincidence of naming. Under the move (section 5) the guess is no longer needed and
  should be **deleted with the move, not before**.
- **#2, module-slug scoping** (`course_repository.py:571-576`). `("shared",)` is a
  sentinel tuple that cannot collide with a real `("cohorts", "<year>")` scope, so two
  cohorts sharing a root module are permitted while two cohorts owning same-named modules
  under `cohorts/` are also permitted. The rule is correct but undocumented, and it
  changes silently for 13 modules the moment they move.
- **#9, branch-tip image resolution** (`unit_assets.py:9-10`). Images resolve to the
  branch, not the pinned commit, so a renamed upstream file goes stale without any import.
  3 of 102 ML image targets already do not exist.
- **#17, missing `module.yaml`** — two module directories are dropped in silence.

**Extracted correctly and then discarded (not divergence, plain loss):**

- `unit_code_links` is computed and passed to the template (`unit.py:143`) and **rendered
  by no template** — `grep -rn code_links courses/templates/` returns nothing. 6 declared
  code attachments, all in llm-zoomcamp, never reach a reader. V
- `Unit.rendered_html` is written at import (`curriculum_import.py:726`) and the view
  re-renders from `content_markdown` on every request (`unit.py:119-124`). The column is
  dead weight. V
- 22 module `README.md` files are never read (#18).

---

## 3. Live gap introduced by the in-flight `SITE.md` change

**Blocking, and not caused by this work.** The parser now reads `SITE.md`
(`course_repository.py:782`), but the manifest builder still selects
`course.yaml:/description_path` and has no knowledge of `SITE.md`
(`build_course_modules_manifest.py:162-165`). `SITE.md` is therefore **not in the
snapshot**, the parser sees it as absent, and `_upsert_course` leaves the stored
description unchanged (`curriculum_import.py:339-343`).

The net effect is benign today — descriptions stay curated, which is the desired end state
— but the three `SITE.md` files the course repos just gained are **inert**. Whoever owns
the `SITE.md` change must add `SITE.md` to `_referenced_paths()` for `course.yaml`, or the
feature does not exist. Flagging, not fixing: that file is another agent's.

---

## 4. The unified convention (decided)

**`cohorts/<year>/<module>/module.yaml`** — the layout `llm-zoomcamp` already has.
`machine-learning-zoomcamp` (9 modules) and `ai-dev-tools-zoomcamp` (4 modules) move.

The trade-off is not reargued: per-cohort curriculum versioning becomes expressible, and
the accepted cost is that a module reused across years is duplicated in the repository
rather than referenced once. Two consequences to record:

- **`llm-zoomcamp/cohorts/README.md` documents the opposite convention** and must be
  rewritten as part of the migration. Verbatim today: *"Shared lesson content lives in the
  top-level module directories… The same top-level module may be referenced by more than
  one Cohort. The platform projects the shared Unit source into each Cohort without
  duplicating the Markdown in this repository."* Every sentence of that becomes false.
- **The migration runbook's recommendation is reversed.**
  The 2026-09-02 session handoff (since deleted) recommended reverting `c04db93` on the
  grounds that it contradicts that README. Under the owner's decision `c04db93` is
  **correct** and ships; the README is what is wrong. That runbook line needs correcting.

**Unify the module directory's *location*, not its *interior*.** The owner's decision is
about where `module.yaml` lives. Forcing ML's flat `<mod>/NN-name.md` into LLM's
`<mod>/lessons/NN-name.md` would additionally break 315 intra-module links and all 102
image references, for zero product benefit and zero public-URL change. Leave the interior
as each repository has it. Interior shape can be normalised later, independently, if the
product ever needs it.

---

## 5. Migration plan

Starting state: llm-zoomcamp's 5 and machine-learning-zoomcamp's 2 pending commits are
being pushed to `main` by another agent. This plan assumes they land.

### 5.1 Public URLs do not move — verified

`/courses/<family>/<year>/modules/<module>[/<unit>]` is built from `course.slug`,
`cohort.identifier`, `module.slug`, `unit.slug` (`courses/urls.py:44-54`,
`courses/views/unit.py:21-31`). `module.slug` is the module **directory name** and
`unit.slug` is the Markdown **filename stem**. A `git mv 01-intro cohorts/2026/01-intro`
changes neither. **No public path moves.**

`_docs/compatibility/generated-path-baseline.jsonl` (2,937 rows) contains **no**
`/courses/<family>/<year>/modules/…` row — the only `/courses/` row is the static
`/courses/2021-winter-ml-zoomcamp.html`, and the only row containing "modules" is
`/docs/courses/zoomcamp-logistics/modules/`. The module curriculum surfaces are new and
uncovered by the compatibility contract. **Nothing in this proposal touches a baseline
row.** V

### 5.2 machine-learning-zoomcamp — what moves

`git mv <mod> cohorts/2026/<mod>` for the 9 modules carrying a `module.yaml`
(`01-intro`, `02-regression`, `03-classification`, `04-evaluation`, `05-deployment`,
`06-trees`, `08-deep-learning`, `09-serverless`, `10-kubernetes`). **362 tracked files**:
135 Markdown, 105 of them units, 105 images, and the rest notebooks, scripts and data that
lessons link to. `11-kserve` has no `module.yaml` and stays. V

**Not a pure `git mv`.** Three things break and must be repaired in the same commit:

1. **Nine `homework.md` collisions.** `cohorts/2026/<mod>/homework.md` already exists for
   all nine and is the real 2026 instruction set (3,979–7,774 B). The incoming
   `<mod>/homework.md` is a 522–705 B stub listing past-cohort homework. They are
   different files at the same destination path. **Resolution:** drop the stub. The 18
   `[Homework](homework.md)` links in unit bodies then resolve to the destination
   `cohorts/2026/<mod>/homework.md`, which *is* `Homework.instructions_source_path`, so
   they bind through the direct path match (`unit_links.py:167`) instead of the
   directory-name heuristic. The stub's past-cohort links are already reachable from the
   cohort folders. **This deletes heuristic #14's only caller.**
2. **103 `](../)` back-links**, one in each of 103 unit files, labelled "Machine Learning
   Zoomcamp course". Today `../` resolves to the repository root and renders as the repo
   tree URL. At the new depth it resolves to `cohorts/2026` — a wrong, and silently wrong,
   destination. **Resolution:** rewrite to `../../../` (mechanical, one `sed` over 103
   files) or to the absolute course URL. This is the depth hazard the coordinator
   predicted, and it is the single largest content edit in the migration.
3. **102 image references** are all inside the module directory (`<mod>/images/…`, plus 3
   same-directory). They move with `git mv` and keep working **only because the whole
   directory moves as a unit**. If the Markdown is moved without `images/`, all 102 404
   silently — the manifest never snapshots images, so no checksum, no test and no import
   diagnostic would notice. 3 of the 102 targets are already dead upstream and stay dead;
   they are pre-existing and out of scope. V

`cohorts/2026/cohort.yaml` `/flow/N/module/source` changes from `01-intro/module.yaml` to
`cohorts/2026/01-intro/module.yaml`, ×9.

### 5.3 ai-dev-tools-zoomcamp — what moves

`git mv <mod> cohorts/2026/<mod>` for 4 modules — **28 tracked files**, of which 19 are
`01-ai-native-workflow/weekly-feedback/`, a complete Python package the lesson links to.
`05-agent-capabilities` has no `module.yaml` and stays. No collisions: the cohort
homework directories are named differently.

**Not a pure `git mv`.** Two repairs:

1. **9 escaping links.** `../cohorts/2026/01-overview/homework.md` and
   `../cohorts/2026/02-development/homework.md` become `../01-overview/homework.md` and
   `../02-development/homework.md`; `../cohorts/2025/…` (4 refs) becomes `../../2025/…`;
   `../03-deployment/` and `../05-agent-capabilities/` become `../../../05-agent-capabilities/`
   for the module that did not move.
2. **Decide the homework directory names.** Module `01-ai-native-workflow` binds homework
   at `cohorts/2026/01-overview/`. After the move both are siblings under
   `cohorts/2026/`, and the mismatch becomes visible rather than merely odd. Renaming the
   homework directory is **safe** — homework slug comes from `homework.yaml:/slug`
   (`course_repository.py:1004`), never from the path — but it is a content decision, and
   the `cohort.yaml` `/flow/N/module/homework` reference must be updated with it.

**Separately, fix divergence #7 in this repo.** All 4 unit bodies open
`# Module N — <Title>` against a manifest title without the prefix. Either add the prefix
to the four `module.yaml` titles or drop it from the four bodies; the latter is better,
since the module page already states the module. Four one-line edits remove the only
duplicate-heading defect in the product.

### 5.4 llm-zoomcamp — what changes

No file moves. Two edits:

1. **Rewrite `cohorts/README.md`** to describe the cohorts-scoped convention. It currently
   asserts the opposite and would mislead the next author. V
2. Nothing else. Its 19 escaping links already assume the cohorts layout, including four
   genuine cross-cohort references into `cohorts/2024/` and `cohorts/2025/`.

### 5.5 Importer changes

**Ideally none.** Both placements are already accepted (`course_repository.py:641-643`,
`build_course_modules_manifest.py:111-115`), so the repos can move under an unchanged
importer, one repository at a time. That is the whole reason the transition is safe.

Optional follow-ups, each independently shippable **after** all three repos have moved:

- Narrow `_validate_manifest_placement` and `_module_manifest_paths` to the
  `cohorts/<id>/<mod>/module.yaml` form only, and drop the `("shared",)` scope branch at
  `course_repository.py:571-576`. This turns a silent dual-mode into one rule. Do it last;
  doing it first bricks two repositories.
- Delete the `homework.md` module-directory fallback (`unit_links.py:168-171`) once
  ML's stubs are gone. Its only caller disappears with §5.2 item 1.
- Add `SITE.md` to the manifest builder's selection (section 3) — different owner.

**The year in the path must not become a second source of truth.** Under
`cohorts/<id>/<mod>/`, the path segment is already validated against
`cohort.yaml:/identifier` (`course_repository.py:1269-1271`) and cross-checked for modules
(`:1315-1317`). `Cohort.year` is a separate integer field read from `cohort.yaml:/year`
(`:1366`); `courses/migration_family_identity.py` uses `year` only to detect duplicate
cohorts within a family (`:167`), and derives nothing from a path. **Recommendation: leave
this exactly as it is.** Deriving `year` from the directory name would create a second
derivation that can disagree with the first — the identifier is a slug, the year is an
int, and they are permitted to differ. I

### 5.6 Ordering, and what breaks mid-flight

Both layouts coexist across repositories throughout. Nothing has to be atomic *across*
repositories; each repository's move must be atomic *within itself*.

| Step | Action | Owner | Breaks if skipped |
| --- | --- | --- | --- |
| 0 | Push llm-zoomcamp ×5, ml ×2, ai-dev-tools ×1 to `main` | owner (in flight) | Everything downstream — production ingests by webhook and cannot see unpushed commits. Until this lands, `PRODUCTION_PREP_UNPUBLISHED_COMMIT_REASON` (`Makefile:433-435`) is the only thing keeping the manifest builder from refusing. |
| 1 | Add `SITE.md` to the manifest builder selection | the `SITE.md` agent | The three new `SITE.md` files stay inert (section 3) |
| 2 | llm-zoomcamp: rewrite `cohorts/README.md` | content | Next author follows a false convention |
| 3 | ai-dev-tools: `git mv` ×4 + 9 link repairs + homework dir decision, **one commit** | content | Split across commits, `main` carries broken links between them |
| 4 | ai-dev-tools: fix the 4 duplicate headings | content | 4/4 units keep showing the title twice |
| 5 | ml-zoomcamp: `git mv` ×9 + drop 9 stubs + 103 `](../)` rewrites, **one commit** | content | A split commit leaves 102 images or 103 links broken on `main` |
| 6 | Regenerate the manifest, rebuild the dataset, verify 7/9/4 modules and 72/105/4 units | engineering | Stale snapshot digests; `source_snapshot_checksum_mismatch` |
| 7 | Narrow the parser to the single placement + delete the `homework.md` fallback | engineering | Nothing — pure cleanup, and **must** be last |

**What is broken between steps:** between 0 and 6, the manifest pins whichever commit each
checkout is on, so a repo mid-move imports its old shape. That is safe — it is the shape
that works today. The dangerous window is **inside** steps 3 and 5: a commit that moves
Markdown without its `images/` sibling, or that moves files without repairing depth-relative
links, produces pages that render with no error anywhere in the pipeline. Nothing in the
manifest, the parser, the importer or the test suite checks that an image URL resolves.
That is why steps 3 and 5 are each specified as one commit.

---

## 6. Open questions for the owner

1. **The stub `<mod>/homework.md` files in machine-learning-zoomcamp.** Delete, or preserve
   the past-cohort index under another name?
   *Recommended default: delete.* They collide with the real instructions at the
   destination, and the past-cohort links they hold are already reachable from
   `cohorts/2021…2025/`.

2. **`ai-dev-tools-zoomcamp` homework directory `01-overview` versus module
   `01-ai-native-workflow`.**
   *Recommended default: rename the homework directory to match the module.* Homework slugs
   come from `homework.yaml`, so this changes no URL and no binding.

3. **`11-kserve` (ML) and `05-agent-capabilities` (AI Dev Tools) have no `module.yaml`.**
   Intentionally out of the 2026 curriculum, or an omission?
   *Recommended default: intentional, leave at repo root.* If intentional, consider a
   builder diagnostic listing module-shaped directories without a manifest, so the next
   omission is loud (#17).

4. **Module `README.md` — 22 files, never imported.** Should the module page carry an
   overview?
   *Recommended default: yes, as a separate groomed issue.* It needs a `module.yaml`
   `overview_path` field, not a filename convention, so a module can opt out.

5. **`unit_code_links` renders nowhere.** Bug or deliberate?
   *Recommended default: bug — render it.* The data is parsed, persisted and passed to the
   template; only the template markup is missing. 6 attachments today, all llm-zoomcamp.

6. **The unit `<h1>` does not apply `unit_display_title`** (`unit.html:140`), so 102 ML
   units head with `1.1 Introduction to Machine Learning` while their rail row reads
   `Introduction to Machine Learning`.
   *Recommended default: apply the filter to the `<h1>` too.* One-line change; the stored
   title is unaffected.

7. **When to narrow the parser to a single placement** (step 7).
   *Recommended default: one release after all three repos have moved and a dataset rebuild
   has verified 7/9/4 modules and 72/105/4 units.* Narrowing early converts a working
   repository into an import failure.
