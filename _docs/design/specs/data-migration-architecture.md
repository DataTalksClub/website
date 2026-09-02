# Data migration architecture — refactor plan

Status: **proposed, Stage 1 (design) only.** Nothing below is implemented.

A **refactor**, not a greenfield design. Every capability already exists; it is spread
across eight entry points and three app homes, with names that do not say what they do.
The plan renames, relocates and decomposes what exists, then adds one adapter on top.

## 1. The problem, stated structurally

> "local data is confusing. let's create a proper migration script that's more modular
> that we can use to import different data" — owner

It is confusing for a reason that is structural, not cosmetic: **a reader cannot tell from
a filename whether a script touches real production data or invents fake data.**
`scripts/prepare_local_data.py` and `scripts/seed_local_courses.py` read as the same kind
of thing. One is the multi-source production migration orchestrator; the other invents
`Practice assignment for …`. They are currently neighbours.

Three consequences follow, and all three are observed defects rather than hypotheticals:

1. `scripts/prepare_local_data.py` is **misnamed** — it reads as "data for local dev" when
   it is the migration orchestrator (migrations, event identity manifest, CMP snapshot,
   course catalog, course modules, Luma, Eventbrite, current-event registration mapping).
2. The sanitized and full-fidelity CMP paths are **different programs**, so one works and
   the other is switched off (`scripts/load_rds_export.py` `main()` returns 2).
3. The placeholder seeders **run after** the real import and fill the gaps it left, so the
   site shows invented content that reads as real. This is what the owner saw.

## 2. The organizing decision

> "I want all prod data scripts to be in one place e.g. /scripts/prod" — owner

**Split by what data a module touches, not by what it does.**

| | Touches | Home |
| --- | --- | --- |
| **Production pipeline** | CMP RDS export, Luma, Eventbrite, course repositories, `zoomcamp-scoring`, public projection sources | `scripts/prod/` |
| **Scaffolding** | nothing real; invents rows | stays where it is, outside `scripts/prod/` |

This makes the confusion structural rather than a matter of taste, and it does most of the
"placeholder generators must not overwrite real data" work by layout: a person migrating
production data has no reason to open the seeders, and the seeders are not on the prod
pipeline's import graph.

### Where the code lives, and why it is not two places

The pull toward "importable, tested app code" (`events/importers.py`, with real tests in
`events/tests/test_importers.py`) and the pull toward `scripts/prod/` are **not in
conflict here**, because `scripts/` in this repository is *already* an importable, tested
Python package: `scripts/__init__.py` exists, `scripts/historical_import/` is a
sub-package, and `scripts/tests/` holds real tests including
`scripts/tests/test_prepare_local_data.py`.

So `scripts/prod/` is an importable package with tests under `scripts/tests/`, not a
folder of monoliths. Concretely:

- **Adapters that already live in app code and have real tests stay there.**
  `events/importers.py` is not moved. `scripts/prod/` *registers* it.
  Moving 904 lines of tested code to satisfy a folder name would be pure risk for zero
  discoverability gain, and the owner's goal is discoverability.
- **Adapters that live in standalone scripts move in**, and gain tests as they move.
- `scripts/prod/` therefore holds: the orchestrator, the registry, the modes/PII boundary,
  the normalization and loading layer, and the adapters that had no better home.

## 3. Evidence gathered for this plan

Verified in this worktree on 2026-09-02. Several repeatedly-stated facts are wrong;
corrections are marked ⚠.

### 3.1 Source provenance

| File | Verdict |
| --- | --- |
| `/data/tmp/rds-export/rds-prod-20260902-012536.db` | **Genuine production export** |
| `/home/alexey/git/course-management-platform/db/db.sqlite3` | **CMP dev database**, not an export |
| `.tmp/cmp-import-prep.sqlite3`, `.tmp/cmp-import-scratch.sqlite3` | **Not CMP extracts** — website databases |

The test is the DDL. `parquet_to_sqlite.py:182-193` (`create_table_from_arrow`) emits bare
columns — no primary key, no foreign key, no index. The production export matches exactly:

```
CREATE TABLE "courses_homework" ("id" INTEGER, "slug" TEXT, ..., "instructions_url" TEXT)
```

with **0 indexes**, and `django_migrations` spanning 2024-01-17 → 2026-08-31 across **35
distinct days** with `+00` offsets — genuine Postgres history carried as data.

The CMP dev database has `INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT`,
`REFERENCES … DEFERRABLE INITIALLY DEFERRED`, **113 indexes**, and `django_migrations`
confined to **2 days** with no offsets. That is `manage.py migrate` run locally.

The two `.tmp/cmp-import-*.sqlite3` files have 100 tables and the *website's* own migration
history through `courses.0052`. They are outputs of an earlier production-prep run; using
either as a source would launder a previous import's mistakes back in.

**Design consequence:** the adapter asserts provenance rather than accepting a path (§6.4).

### 3.2 ⚠ The three post-pin tables are NOT empty

Stated as empty in every brief. In the **production** export they are not:

| Table | Added by | CMP dev | **Production** |
| --- | --- | --- | --- |
| `courses_emailcampaign` | `0043` | 0 | **1** |
| `courses_systemprojectevaluation` | `0041` | 0 | **1** |
| `courses_systemevaluationcriteriaresponse` | `0041` | 0 | **11** |

This is load-bearing. `review_import/workflow.py:_validate_source_schema` skips *empty*
unknown tables and fails closed on non-empty ones. "All three are empty, so the fix is
cheap" was measured against the **dev** database. Against production the escape hatch does
not apply and the import genuinely fails closed. The resolution must be a real schema
adoption (§6.4), not the existing row-count skip.

### 3.3 ⚠ Production content counts

Measured on `rds-prod-20260902-012536.db` (664,806 rows total, 38 tables — both confirmed):

| | Brief said | **Measured** |
| --- | --- | --- |
| `courses_course` | 20 | **21** |
| `courses_project` | 45 | **52** |
| `courses_question` | 616 | 616 ✓ |
| `courses_homework` | 128 | 128 ✓ |

`courses_courseregistration` has 27,656 rows and **does not exist in the CMP dev database
at all** — confirming that any mapping derived from the dev copy is incomplete.

### 3.4 Where the placeholder generators live

⚠ **The "placeholder generator scripts" are not the cause.** `add_data.py`,
`add_more_test_data.py`, `seed_local_questions.py`,
`generate_production_like_leaderboard_data.py` and `build_synthetic_design_review_db.py`
contribute **zero rows** to the site. Deleting all of them changes nothing the owner sees.

The junk arrives through the **live** path this refactor restructures:

```
scripts/prepare_local_data.py:290   catalog = _json_management_command("seed_local_courses")
  -> courses/services/local_course_seed.py:276   "Practice assignment for {title}. "
  -> courses/services/local_course_seed.py:362   "Production-like generated project: {title}"
  -> names from scripts/production_like_course_specs.json:42-50
```

So this is a **product change to the seed service inside the import path**, not a cleanup
beside it. It lands within this work (§6.5).

⚠ **`production_like_course_specs.json` cannot simply be edited.** It is not an invented
fixture: it is a verbatim copy of CMP's *own* file at
`98a235283904b4ef9ad29e196298540756cf1bcc`, and its SHA-256
`34077cd4…1019a7` is verified in **three** independent places —
`local_course_seed.py:73` (`CATALOG_SOURCE_SHA256`, checked before writing rows),
`scripts/build_public_projection.py:2918` (checked before projecting), and
`_docs/adoption/course-platform/copied-files.tsv:757`. Verified: the file on disk matches
that digest exactly. The pin guarantees the seed and the public projection describe the
*same* catalogue because both read the same pinned upstream file. Editing it breaks the
projection's provenance chain (`source_path` at `build_public_projection.py:2459`, `:3143`)
and the adoption ledger simultaneously.

That also explains `Project Attempt N` (below): it is in this file because it was in CMP's
file, because it reflects real production copy.

| String | Source |
| --- | --- |
| `Practice assignment for …` | `courses/services/local_course_seed.py:276` (`homework_description`) |
| `Production-like generated project: …` | `courses/services/local_course_seed.py:362` (`_seed_projects`) |
| `Project Attempt N` titles | `scripts/production_like_course_specs.json:42-50` (pinned upstream copy) |
| both strings, duplicated but unreachable | `scripts/generate_production_like_leaderboard_data.py:228`, `:412` |

⚠ **`Project Attempt N` is a false positive.** It is genuine DataTalksClub production copy:
28 of 52 projects in the production export carry it, and `content/faq_projection.json:310`
holds the public FAQ entry *"Project: What is Project Attempt #1 and Project Attempt #2
exactly?"*. The real seed marker is `Production-like generated` — 32/32 in the current dev
database, 0/52 in production. Any audit grepping for `Project Attempt` will keep
mis-reporting this.

**Why the existing guard fails.** `_seed_homeworks` and `_seed_projects` already early-return
when the cohort has rows (`local_course_seed.py:331`, `:353`). The guard is
**slug-namespaced**: the seeder writes `homework-01-<slugified-title>` while CMP writes
`hw1`, so the two sets never collide and the seeder adds rows *beside* the real ones rather
than overwriting. That is the observed 100 homework rows (80 placeholder + 20 real).

### 3.5 The curriculum-format split, measured

`Cohort.curriculum_format ∈ {legacy, modules}`. CMP has no `courses_module`/`courses_unit`.
The join is `courses_module.terminal_homework_id → courses_homework.id`, resolved today by
**homework slug**. The slug namespaces disagree, per cohort:

| Cohort | CMP slugs (production) | Repo-derived slugs | Match |
| --- | --- | --- | --- |
| `ml-zoomcamp-2026` | `hw01`…`hw10` (no `hw07`) | `hw01`…`hw10` (no `hw07`) | **exact** |
| `ai-dev-tools-2026` | `hw1`…`hw4` | `hw01`…`hw04` | **no** — zero-padding |
| `llm-zoomcamp-2026` | `hw1`…`hw5`, `dlt` | `homework-01`…`homework-07` | **no** — scheme |

Both mismatch directions occur, as predicted: CMP's `dlt` workshop has no repo module;
the repo's `homework-06`/`homework-07` have no CMP homework.

**⚠ Latent bug found.** `courses/services/local_cmp_content_import.py:50` rewrites CMP slugs
matching `^hw(\d+)$` to `homework-0N` for *all three* module cohorts. Correct for
`llm-zoomcamp-2026`; wrong for the other two. `ml-zoomcamp-2026`'s `hw01` currently matches
its repo module exactly, and the rewrite would turn it into `homework-01` and **break a
binding that works**. It has not fired only because the CMP import has never run against
the live dev database. A single regex at this seam is the ad-hoc handling that produced the
duplicate family repaired by migration `0052`.

**The explicit hook already exists and is unused.** `homework_slug_overrides` is accepted
and validated at `local_course_modules.py:435-443` as `{repo_source_path: homework_slug}`,
but `scripts/build_course_modules_manifest.py` never emits it — all three sources carry
`None`. The design uses this hook instead of a regex (§7.2).

### 3.6 Precedence hazard

`courses/services/curriculum_import.py:337-339` assigns `course.title`,
`course.description` and `course.outcome` from the repository source **unconditionally**,
and runs *after* the catalog seed. That is how three families came to hold raw README
markup where they had curated text.

### 3.7 The adapter pattern already exists — honest evaluation

`events/importers.py` (904 lines) is built the right way and is the correct starting point:

```
derive_luma(path, *, expected_checksum, mapping_bridge, source_missing,
            enforce_pinned_reconciliation, allow_partial_mapping) -> DerivedSource   :336
derive_eventbrite(...)                     -> DerivedSource                          :535
_source_registry() / registered_source_options() / resolve_registered_source_reference()
derive_registered_source(reference, *, expected_provider, ...)                       :838
```

It already carries what every other import path lacks: `ProtectedSourceError(code)` which
never embeds source values, checksum verification (`_aggregate_checksum`,
`_directory_checksum`), pinned-reconciliation guards, and path safety (`_safe_path`,
`_validate_archive_member`).

**What generalizes:** the *envelope*. One `derive_*` per upstream format; a registry keyed
by an opaque reference resolved through a digest so paths never reach the UI; a provenance
header (`provider`, `adapter_version`, `schema_version`, `whole_source_checksum`);
error codes without values. All of that is exactly what a CMP or course-repo adapter needs,
and the error-code discipline is the natural PII posture.

**What does not generalize:** the *payload*. `DerivedSource` is registration-aggregate
vocabulary — `manifest_event_total`, `quarantined_event_total`,
`candidates: tuple[AggregateCandidate, …]` keyed on `external_event_identifier`,
`status_totals`, `state_totals`. A CMP adapter yielding courses, homework, questions and
projects cannot fill those fields honestly, and forcing it to would produce exactly the
kind of abstraction nobody can read.

**So:** keep and generalize the envelope; make the payload a generic entity graph.
`DerivedSource` becomes one payload type beside `SourceGraph`, both carrying a shared
`SourceProvenance` header. `events/importers.py` is **not rewritten** — it gains the shared
header and registers itself. This is "generalise a working, tested example", not "invent an
abstraction", which is the whole point of the finding.

### 3.8 The event lane is split across three homes

`events/importers.py` (904) · `events/services.py` (1782) ·
`scripts/build_event_description_bridge.py` (1058) · `content/event_description_bridge.py`
(695) · `scripts/prepare_event_registration_sources.py` (193) ·
`events/current_registration.py` (161) · two `events/management/commands/…`.

One source, three homes (`events/`, `content/`, `scripts/`). That split is probably more of
what the owner means by "confusing" than the raw file count. `scripts/prod/` resolves the
*entry point* half of it: one registered entry per source. Consolidating
`content/event_description_bridge.py` into `events/` is a related follow-up, listed out of
scope (§11) because it is a different lane's code.

## 4. Current surface inventory

**Reconciled with `_docs/design/specs/script-inventory.md`.** That audit landed after the
first draft of this plan and overturned its deletion list; the corrections are folded in
below and in §10.

⚠ **Nothing under `scripts/` is deletable in a cleanup commit.**
`_docs/adoption/course-platform/copied-files.tsv` pins 14 files under `scripts/`, and
`core/tests/test_course_platform_adoption.py:239` asserts the ledger has exactly **768**
rows while `:262` asserts `destination.is_file()` for every one of them. Verified
independently: `add_data.py` (row 747), `add_more_test_data.py` (748),
`generate_production_like_leaderboard_data.py` (753), `load_rds_export.py` (755) and
`production_like_course_specs.json` (757) are all pinned, and the file has 769 lines
(768 rows + header). The `retired_adoption_destinations` escape hatch
(`scripts/verify_course_platform_adoption.py:74-84`) covers only `courses/migrations/` and
`courses/models/course.py`, so no `scripts/` path qualifies.

Deleting any of them needs a four-part change in one commit — drop the `copied-files.tsv`
row, drop any matching `integration-patched-files.tsv` row, extend
`retired_adoption_destinations` to `scripts/`, and lower the 768 constant. That is a
**groomed issue of its own**, not a step in this refactor. This plan therefore **moves and
renames, and deletes nothing under `scripts/`.**

| Path | Purpose | Disposition |
| --- | --- | --- |
| `scripts/prepare_local_data.py` (474) | multi-source orchestrator | **Rename + decompose** → `scripts/prod/run.py` + adapters |
| `courses/services/local_cmp_content_import.py` (315) | CMP snapshot → local DB | **Becomes the CMP adapter**; regex replaced by override table (§7.2) |
| `review_import/workflow.py` (1658) | sanitizing CMP reader | **Keep, registered.** Extraction is a later change (§10) |
| `review_import/manifest.py` | versioned allowlist + sensitive set | **Keep and promote** — becomes the PII boundary (§6.2) |
| `scripts/build_local_review_db.py` (19) | thin CLI over the above | Becomes `scripts/prod/` entry point |
| `scripts/build_course_modules_manifest.py` (345) | course repos → manifest | **Move to `scripts/prod/`** |
| `courses/services/local_course_modules.py` | manifest → curriculum importer | Keep in app code; registered |
| `courses/services/curriculum_import.py` | transactional curriculum importer | **Keep, untouched** — held by another lane |
| `events/importers.py` (904) | Luma + Eventbrite adapters | **Keep in place**, registered. Tested; do not move |
| `scripts/prepare_event_registration_sources.py` (193) | Luma/Eventbrite CLI | Becomes `scripts/prod/` entry point over `events/importers.py` |
| `scripts/import_historical_zoomcamp_data.py` (194) + `scripts/historical_import/` | pre-2024 scoring/certificates | **Move to `scripts/prod/`**; already a package |
| `scripts/build_public_projection.py` (3213) | pinned repos → projection | Move to `scripts/prod/` (touches real upstream). Internals out of scope |
| `scripts/sync_course_platform.py` (873), `prepare_course_platform_source.py` (95) | upstream sync | Move to `scripts/prod/`; internals out of scope |
| `scripts/load_rds_export.py` (842) | CMP full copy, `main()` returns 2 | ⚠ **Keep.** `main()` is disabled, but `courses/tests/test_load_rds_export_script.py:9-19` imports nine internals and `review_import/tests/test_workflow.py:36,2411-2417` patches four more and calls `main()`. Also ledger-pinned (row 755). If the CMP adapter absorbs its internals, those test imports move with them |
| `courses/services/local_course_seed.py` (394) | catalog seed + placeholders | **Scaffolding.** Stays out of `scripts/prod/`; defanged (§6.5) |
| `courses/services/local_question_seed.py` | question placeholders | Scaffolding; defanged |
| `courses/management/commands/import_development_course_content.py` (82) | dev content bootstrap | **Keep** — load-bearing for `core/tests/test_course_platform_adoption.py` |
| `scripts/generate_production_like_leaderboard_data.py` (917) | placeholder generator | Scaffolding. Keep for now — referenced by `README.md` and the adoption docs |
| `scripts/build_synthetic_design_review_db.py` (147) | synthetic review DB | Scaffolding. Keep — referenced by `_docs/design/issue-237-review-state-matrix.md` |
| `scripts/add_data.py` (476), `scripts/add_more_test_data.py` (447) | unreachable seeders | ⚠ **Keep.** Zero code references, but ledger-pinned (rows 747, 748). Contribute zero rows to the site. Deletion is a separate groomed ledger issue |
| `scripts/production_like_course_specs.json` | pinned upstream catalogue | ⚠ **Keep, unedited.** SHA-256-pinned in three places (§3.4) |

Genuinely needed by tests/CI, and therefore kept despite generating placeholders:
`local_course_seed.py`, `local_question_seed.py`, `import_development_course_content.py`,
`production_like_course_specs.json`.

⚠ **Management-command budget.** `core/tests/test_course_platform_adoption.py:330-333`
asserts `EXPECTED_COMMANDS` by **exact equality** over the `accounts`, `api`,
`studio_courses`, `courses` and `data` apps. Adding *or* removing a command in those apps
turns the tree red until the ledger is amended and
`scripts/render_course_platform_inventory.py` re-renders
`_docs/adoption/course-platform/behavior-inventory.md`. This plan therefore puts the new
entry points under `scripts/prod/` rather than adding management commands, which keeps the
whole refactor outside that assertion. Any later change that does add one must budget the
ledger amendment into the same commit.

## 5. Target layout

```
scripts/prod/                     # importable package; "prod" = touches real data
    __init__.py
    run.py                        # orchestrator (was scripts/prepare_local_data.py)
    registry.py                   # generalised from events/importers.py:766-870
    provenance.py                 # SourceProvenance header; snapshot identification (§6.4)
    modes.py                      # ImportMode + NEVER_IMPORTABLE (§6.2)
    identity.py                   # re-exports courses.migration_family_identity; adds nothing
    normalize.py                  # -> family+year, legacy-vs-modules routing
    loading.py                    # idempotent upsert on natural keys (§6.1)
    sources/
        cmp_snapshot.py           # CMP RDS export        (from local_cmp_content_import.py)
        course_repository.py      # course repos          (wraps local_course_modules.py)
        historical_scoring.py     # (from scripts/historical_import/)
        luma.py, eventbrite.py    # thin registrations of events.importers.derive_*
        event_identity.py, registration_mapping.py
    binding/
        curriculum_format.py      # legacy vs modules routing (§7.1)
        homework_binding.py       # CMP homework <-> repo module (§7.2)
scripts/tests/test_prod_*.py      # tests live where the repo already puts script tests
```

Call sites to update in the same change as the move: `Makefile:392`, two call sites in
`_docs/runbooks/local-course-modules-preparation.md`, and
`scripts/tests/test_prepare_local_data.py`. **Four call sites total — no shim needed.**

### The adapter contract, written down

Generalized from `derive_luma`/`derive_eventbrite`, which already agree on it implicitly:

```python
@dataclass(frozen=True, slots=True)
class SourceProvenance:  # shared header; DerivedSource gains this too
    provider: str
    adapter_version: str
    schema_version: str
    whole_source_checksum: str
    snapshot_id: str


class SourceAdapter(Protocol):
    name: str

    def describe(self, reference: str) -> SourceProvenance: ...  # no row data read
    def extract(self, reference: str, mode: ImportMode) -> SourceGraph: ...
```

Three deliberate properties, each inherited from the events implementation:

- `reference` is a **registry key**, never a filesystem path, so paths never reach a UI,
  a log or a report. `resolve_registered_source_reference` already does this.
- `describe` reads provenance only. The orchestrator can report what it *would* import
  without opening learner tables.
- `mode` is a parameter of `extract`, **not** of the loader, so a sanitized run never
  materializes excluded rows in memory at all.

Can CMP and course-repo adapters satisfy it? **Yes for CMP** — a snapshot has a checksum,
a schema version (migration `0043`) and a snapshot id, and it maps cleanly.
**Course repositories need one addition**: their reference resolves to a *commit*, so
`SourceProvenance.snapshot_id` carries the commit SHA and `whole_source_checksum` the
manifest snapshot checksum that `local_course_modules.py` already computes. Neither needs
`DerivedSource`'s event vocabulary — which is exactly why the payload is split from the
header (§3.7).

## 6. Requirements, and how the structure delivers them

### 6.1 Idempotent by construction

Natural keys, per entity (aligned with `.tmp/cmp-schema-mapping-codex.md` §6):

| Entity | Natural key |
| --- | --- |
| Course family | canonical family slug (frozen rule) |
| Cohort | `(family, identifier)`; `slug` is a collision check |
| Homework | `(cohort, slug)` |
| Question | `(homework, source_question_id)` |
| Project | `(cohort, slug)` |
| Review criteria | `(cohort, source_criterion_id)` |
| Module / Unit | `(cohort, position)` / `(module, position)` — owned by the repo importer |
| Enrollment | `(cohort, student)` |
| Submission | `(enrollment, homework)` |
| Answer | `(submission, question)` |

`loading.py` exposes one `upsert(model, natural_key, values)`; adapters never call `.save()`
or `.create()`. ⚠ The Codex map flags that submissions, answers, project submissions, peer
reviews, criterion responses and evaluation scores have **no business-key uniqueness
constraint** in the target schema. Those keys are enforced by the loader plus a preflight
duplicate report until constraints land under their own issue (§11).

### 6.2 PII boundary in the type system

`review_import/manifest.py` already holds `ALLOWLIST` and `SENSITIVE_TABLES`. Promote it,
and make credential tables structurally unreachable rather than merely denied:

```python
class ImportMode(Enum):
    SANITIZED = "sanitized"  # default
    FULL_FIDELITY = "full-fidelity"


NEVER_IMPORTABLE: Final[frozenset[str]] = frozenset(
    {
        "django_session",
        "socialaccount_socialaccount",
        "socialaccount_socialapp",
        "socialaccount_socialapp_sites",
        "socialaccount_socialtoken",
        "accounts_token",
        "account_emailconfirmation",
    }
)
```

`NEVER_IMPORTABLE` is subtracted from the adapter's readable table set at connection setup,
in **both** modes, before any mode branch. No flag re-admits it; admitting one requires
editing the constant. Tests assert `NEVER_IMPORTABLE & set(COPY_ORDER) == frozenset()` and
that a `FULL_FIDELITY` extract never yields those table names.

Sanitized stays the default: `ImportMode.SANITIZED` is the default argument and `run.py`
requires an explicit `--mode full-fidelity` plus a reason string.

Error discipline is inherited from `ProtectedSourceError(code)` (§3.7): failures carry
codes, never source values. That is what keeps personal data out of logs and reports by
construction rather than by review.

### 6.3 Sanitization is a mode, not a fork

Today `review_import` (sanitizing) and `load_rds_export.py` (full copy, disabled) are
different programs — which is precisely why one works and the other is off. One pipeline,
one mode parameter. `load_rds_export.py` is **deleted** once `FULL_FIDELITY` exists, not
revived. Its only remaining reference is `review_import/tests/test_workflow.py:2411-2417`
asserting `main()` returns 2; that test is replaced by the mode test.

**`FULL_FIDELITY` is the goal, not a follow-up.** The owner's target is a complete local
database on their own workstation — all CMP data including the 20,009 accounts, 20,907
enrollments and 218,157 answers, plus events with registrations, plus wiki, podcast and
articles (§8). `SANITIZED` remains a first-class mode because CI, design review and
anything shareable need it, but it is no longer the only path that works.

Full fidelity means **the data, not live credentials**. `NEVER_IMPORTABLE` (§6.2) applies
in both modes and is not negotiable: there is no version of "see how production looks" that
requires someone's session key or OAuth token. `courses/random_names.py` (CMP's own
pseudonym generator) remains available for `SANITIZED`; it is *not* applied in
`FULL_FIDELITY`.

### 6.4 Fail closed on schema drift, with a repeatable resolution

Keep the fail-closed check; give it a resolution that is a **data change, not a code
change**, so absorbing drift is routine while CMP stays live.

In `review_import/manifest.py`:

```python
ADOPTED_UPSTREAM_PIN = "6d3cc0e…"  # CMP commit the allowlist was reviewed against
KNOWN_UNMAPPED_TABLES: Final[Mapping[str, str]] = {
    "courses_emailcampaign": "cmp-0043; no website model; excluded",
    "courses_systemprojectevaluation": "cmp-0041; no website model; excluded",
    "courses_systemevaluationcriteriaresponse": "cmp-0041; no website model; excluded",
}
```

`_validate_source_schema` passes a source-only table **iff** it is declared here,
regardless of row count, and still fails closed on anything undeclared. This **replaces**
the row-count escape hatch, which §3.2 shows does not work against production.
`ALLOWLIST_SCHEMA_VERSION` bumps to `cmp-public-review-v2`.

`provenance.py` additionally asserts the snapshot is a pipeline export (§3.1) and within a
freshness window, so pointing `PRODUCTION_PREP_CMP_SOURCE` at CMP's dev database becomes
an *error* rather than a silent downgrade to 595 questions.

Rejected: adding the three CMP models to the website's `courses` app to satisfy the check.
The website does not own email campaigns; modelling them to pass a guard inverts the
dependency.

### 6.5 Placeholder generators structurally unable to overwrite real data

**The answer, stated directly: the seed becomes a synthetic-target-only bootstrap that can
never run alongside a real adapter.** Of the three options —

1. *edit the catalogue JSON* — **rejected.** Blocked by three independent SHA-256 pins
   (§3.4); it would break the public projection's provenance chain and the adoption ledger.
2. *let the real adapter overwrite the seeded rows* — **rejected.** It only works if the
   slug namespaces agree, and §3.5 shows they do not: the seeder writes
   `homework-01-<slug>` while CMP writes `hw1`, so the rows never collide and the seeded
   ones survive beside the real ones. That is the observed 100-row homework table.
3. *seed is synthetic-target-only* — **chosen.** The seed is a bootstrap for a database
   with **no real source**, not an overlay on one that has one.

Layout does most of the work (§2). Four layers, because the current guard is
namespace-scoped and therefore useless (§3.4):

1. **Import boundary, enforced by test.** A test walks the import graph of every module
   under `scripts/prod/` and asserts it never reaches `local_course_seed`,
   `local_question_seed`, `generate_production_like_leaderboard_data` or
   `build_synthetic_design_review_db`. This is the structural guarantee, not a convention.
2. **Ordering.** Seeding runs *before* every real adapter, never after — a bootstrap for an
   empty database, not an overlay.
3. **Provenance flag, not slug shape.** Adapter-written rows carry
   `source_content_id`/`source_stable_id`. The seeders refuse to write to a cohort holding
   **any** source-owned row, instead of testing for a slug they happen to own.
4. **Mode.** Seeding is unavailable in `FULL_FIDELITY`. A test asserts a seed run *after* a
   CMP run changes zero rows.

### 6.6 One frozen identity rule

`courses/migration_family_identity.py` is the only derivation. `scripts/prod/identity.py`
re-exports it and adds nothing. No adapter may call `rsplit("-")` or strip `-\d{4}$`.
Five families genuinely publish *with* the suffix (`de-`, `ml-`, `llm-`, `mlops-`,
`sma-zoomcamp`); there is no blanket strip rule.

Unmapped slugs **fail closed into an exception report**, per Codex map §2. The production
export holds six slugs the reviewed catalog does not cover: `ai-bootcamp-2025`,
`ai-hero-2025`, `ai-hero-2026`, `sma-zoomcamp-2026`, `ai-buildcamp-2`, `ai-buildcamp-3`.
⚠ The last two carry **edition numbers, not years** — `2` and `3` must never be read as a
year. `llm-zoomcamp-2026`, `ml-zoomcamp-2026` and `ai-dev-tools-2026` also need adding to
`COHORT_FAMILY_IDENTITIES`. That is an owner decision, not an importer inference (§12).

⚠ The Codex map lists `fake-course`/`fake-course-2` as needing exclusion. They exist in the
CMP **dev** database only; the production export has neither.

## 7. The curriculum-format split

The second of the two transformations, and the hardest. (Schema drift is §6.4; they are
independent and must not be conflated.)

### 7.1 How "modules format" is determined

**Derived from the course-modules manifest, not hardcoded.** A cohort is modules-format iff
the manifest contains a source for it.

`TARGET_COHORTS` (`local_course_modules.py:47`) and `PRODUCTION_PREP_COURSE_REPOSITORIES`
(`Makefile:426-429`) are today **two hardcoded lists of the same three courses that can
drift apart** — the bug the brief flagged. The Makefile list is the *input* that produces
the manifest, so the manifest is the single downstream fact. `TARGET_COHORTS` becomes a
**validation assertion against the manifest** rather than a second source of truth, so a
drift between the two becomes a test failure instead of a silent miscategorisation.

### 7.2 How a CMP homework binds to a repo module

**Owner decision: CMP's slug, copied verbatim. No overrides, no mapping table.** CMP is
the source of truth for homework identity, so where the repository declares a different
slug, CMP's wins and the repository's becomes dead configuration.

⚠ This **supersedes the first draft**, which proposed emitting the `homework_slug_overrides`
hook as a reviewed table. That hook now has **no production caller**: the manifest builder
never emitted it, and its only remaining uses are its own validation and three tests. It is
dead code to be dropped under its own issue, not here.

Adopting CMP's slug means re-pointing `Module.terminal_homework`, so the module and its
homework must be paired. The pairing is **read from data both sides already publish** —
the slug where they agree, otherwise an exact title match — and is never derived:

| Cohort | Paired by slug | Paired by title | Unpaired |
| --- | ---: | ---: | --- |
| `ml-zoomcamp-2026` | 9 | 0 | — |
| `ai-dev-tools-2026` | 0 | 4 | — |
| `llm-zoomcamp-2026` | 0 | 4 | CMP `hw3`, `dlt`; repo `homework-03`, `-06`, `-07` |

**Ordinal position is rejected.** CMP orders `dlt` sixth for `llm-zoomcamp-2026`, where the
sixth module is Best Practices, so positional pairing silently binds a workshop to the
wrong module — a page that renders fine and is wrong. A visible gap is recoverable; a wrong
attachment is not.

The `^hw(\d+)$` regex at `local_cmp_content_import.py:50` is **deleted**.

Unmatched cases, both of which will occur — decided, not emergent:

- **CMP homework with no repo module** (`llm-zoomcamp-2026`'s `dlt` workshop): imported,
  attached to the cohort, reachable at its own homework URL, not bound to a module and not
  inserted into the module rail. It is real content; dropping it loses data.
- **Repo module with no CMP homework** (`homework-06`, `homework-07`): renders with
  `terminal_homework = NULL`. **No placeholder homework is invented** — inventing one is
  exactly the behaviour that produced `Practice assignment for …`.
- **Ambiguity** (two CMP homework mapping to one module; an override naming a slug that
  does not exist): fail closed, reporting the cohort slug and offending slug only.

### 7.3 Precedence when CMP and the repo disagree

Field-level ownership, declared once, so re-running cannot flip a value:

| Field group | Owner | Rationale |
| --- | --- | --- |
| Module/Unit structure, ordering, unit content | **Course repository** | CMP has no such tables |
| Homework/question/project content — titles, descriptions, questions, answers, due dates, scores | **CMP** | Authored in CMP; the repo has no questions |
| Cohort dates, visibility, `finished`, passing scores | **CMP** | Operational state |
| Family description | **`SITE.md` in the course repository**, else unchanged | Owner decision. The README is never read; a repo without `SITE.md` leaves the stored description alone |
| Family title, outcome | **Reviewed catalog → CMP → repo** | §3.6 |
| `curriculum_format` | **Manifest presence** (§7.1) | |

⚠ The family-description reversal is the **one behaviour change** in this plan that is not
purely structural. It is called out separately so it cannot be smuggled into a move-only
commit (§9 step 7), and it needs owner confirmation (§12).

### 7.4 Idempotency across both sources

Each field has exactly one owner (§7.3), so re-running either source is a no-op for fields
it does not own — no flip-flopping by run order. A test runs `repo → cmp → repo → cmp` and
asserts the database is unchanged after run 2.

## 8. The complete local database

> "then put ALL DATA into local db - all cmp, all events (with registrations), wiki,
> podcast, articles, etc" — owner

### 8.1 Every source, and how much work each is

⚠ The single most useful finding for scoping: **the generic content models already exist
and are empty.** `content/models.py` defines `ContentSource`, `ContentRelease`,
`ContentDocument`, `ContentRelation`, `ContentAsset` and `ActiveContentPath`, written by
`content_sync/dtc_content/preparation.py`. In the current dev database
`content_contentdocument`, `content_contentsource`, `content_contentrelease` and
`content_contentasset` all hold **0 rows**. So wiki, podcast, articles and books need
**loading**, not new models. That is a much smaller job than "build a model per surface",
and it is the difference the brief asked to be made visible before anyone starts.

| Source | Location | Scale | Target model | Work |
| --- | --- | --- | --- | --- |
| CMP production | newest `/data/tmp/rds-export/rds-prod-*.db` | 38 tables, 664,806 rows | `courses.*`, `accounts.*` | **Adapter + full-fidelity mode** |
| Events + registrations | `<main checkout>/.local/migration-data/events/` — `luma/` 78 MB, `luma-aggregate-v1/` 13 MB, `eventbrite/` 2.1 MB | ~93 MB, ~174 Luma events | `events.*` (9 models exist) | **Adapters exist and are tested** (`events/importers.py`); register them |
| Course curricula | three repos, `Makefile:426-429` | 20 modules, 181 units | `courses.Module/Unit` | **Working today.** Do not disturb |
| Historical scoring | `zoomcamp-scoring` repo | pre-2024 cohorts, certificates | `courses.*` | Adapter exists (`scripts/historical_import/`); relocate |
| Wiki / podwiki | `content/public_projection/wiki.json`, `wiki_graph.json`, `wiki_search.json`, `wiki_assets/` | 310 baseline rows | `content.ContentDocument` **(exists, empty)** | **Loader only** |
| Podcast | `content/public_projection/podcasts.json`, `media/` | episodes + media | `content.ContentDocument` + `ContentAsset` **(exist, empty)** | **Loader only** |
| Articles / blog | `content/public_projection/articles.json` | | `content.ContentDocument` **(exists, empty)** | **Loader only** |
| Books, docs, FAQ, people | `books.json`, `people.json`, `media.json` | | `content.ContentDocument` / `ContentAsset` | **Loader only** |

⚠ The brief located the events data at `.local/migration-data/events/` relative to this
worktree; it is **not there**. It resolves against the *main* checkout via
`_main_checkout_root()` (`scripts/prepare_local_data.py:32-33`). Measured total is ~93 MB,
not ~104 MB — the difference is a `luma-aggregate-v1.backup-20260902/` directory that is
not an input.

This closes the loop on the owner's earlier ruling that **"we don't read projections — it
must come from the db"**. Course surfaces were converted to ORM reads by #307
(`2f3f946`); wiki, podcast and articles remain projection-backed only because nothing has
loaded them, not because they lack a home.

### 8.2 Dependency order

One topological order, asserted by the orchestrator rather than implied by call order:

```
1  content sources/releases      (ContentSource -> ContentRelease)
2  accounts                      (accounts_customuser)
3  course families -> cohorts    (frozen identity rule, §6.6)
4  modules -> units              (course repositories; §7.1)
5  homework -> questions         (CMP; bound to modules per §7.2)
6  projects -> review criteria   (CMP)
7  enrollments                   (needs 2 + 3)
8  submissions                   (needs 5 + 7)
9  answers                       (needs 5 + 8)
10 project submissions -> peer reviews -> criterion responses -> evaluation scores
11 course registrations          (needs 3)
12 events -> aliases -> registration aggregates
13 content documents/assets/relations -> active content paths
14 derived statistics            (recomputed, never trusted from source)
```

Step 14 matters: homework/project statistics, enrollment totals and leaderboard positions
are **imported for comparison and then recalculated** before acceptance, per the migration
controls. A copied aggregate that disagrees with the rows beneath it is worse than none.

### 8.3 Scale and expected cost

~665k CMP rows + ~93 MB of event exports + projection content into SQLite. Row-by-row ORM
saves would take hours, so:

- **Bulk by default.** `bulk_create(..., update_conflicts=True, unique_fields=<natural key>)`
  in batches of 5,000. SQLite's `INSERT … ON CONFLICT DO UPDATE` gives upsert semantics
  directly, so idempotency (§6.1) does not cost a read-per-row.
- **One transaction per source, not per run.** A failed adapter rolls back its own source
  and leaves earlier ones committed — which is what makes §8.4 resumable.
- **ID mapping in memory.** A dict per entity type from CMP id → target id, built during
  that entity's pass. At ~665k rows this is tens of MB, not a problem.
- **`PRAGMA synchronous=OFF`, `journal_mode=MEMORY`** for the load, restored afterwards.
  Safe: the target is a rebuildable local database, not a system of record.
- **Defer index creation** on the largest tables (`courses_answer` at 218k,
  `courses_criteriaresponse` at 108k) until after their load.

**Expected wall-clock for a full run: 10–20 minutes**, dominated by
`courses_answer` and `courses_criteriaresponse`. This is a design target to be measured in
the first increment that loads them, and reported as a number, not a feeling. If it exceeds
30 minutes the batching strategy is wrong and should be revisited rather than tolerated.

### 8.4 Partial and resumable

Nobody re-imports 665k rows to fix one adapter.

- `run.py --source cmp` / `--source events` / `--source content`; default is all.
- Per-source transaction boundaries (§8.3) plus natural-key upserts (§6.1) make re-running
  one source safe and cheap regardless of what else has run.
- A per-source run ledger records source reference digest, adapter version, schema version,
  snapshot checksum and row counts, so `--source cmp` on an unchanged snapshot is a no-op
  that reports "unchanged" rather than redoing the work.

### 8.5 Verification

**Extend `scripts/verify_local_dataset.py`** (237 lines, already the acceptance gate for
`make production-prep-dataset`) rather than writing a second verifier. It already reports
aggregates only and explicitly reads no learner data — the right posture. Add:

1. **Per-table row-count reconciliation**, source ↔ target, as a table of counts and a
   pass/fail. Differences are expected where the loader consolidates or excludes; each
   expected difference is declared, so an *undeclared* difference fails.
2. **Referential-integrity sweep**: no orphaned enrollments, submissions, answers, peer
   reviews or criterion responses; every cohort has a family; every module belongs to a
   cohort with `curriculum_format="modules"`.
3. **Binding coverage** (§7.2): per module cohort, how many modules have a
   `terminal_homework`, how many CMP homework rows are unbound, listed by slug.
4. **Placeholder assertion**: zero rows matching `Production-like generated`. ⚠ Not
   `Project Attempt`, which is real production copy (§3.4).
5. **PII assertion**: `NEVER_IMPORTABLE` tables are empty in the target, in both modes.

⚠ `verify_local_dataset.py:30` still expects the cohort slug `ai-dev-tools-zoomcamp-2026`,
which the family-identity work renamed to `ai-dev-tools-2026`. Fix while extending.

### 8.6 Where the database lives, and how the rules are enforced

The full-fidelity database holds ~20,000 real people.

- **Location:** `.tmp/` in this worktree, created mode `600`.
- **Gitignored:** verified — `.gitignore` lines 3, 4, 7, 8 cover `.tmp/`, `.local/`,
  `*.sqlite3`, `*.sqlite3-*`. It cannot be committed by accident.
- **Enforcement, not convention:** the loader `chmod`s the target to `600` on creation and
  the verifier fails if the mode is wider. A test asserts the target path matches a
  gitignore rule.
- **Source exports are read in place**, mode 600 in mode-700 directories, opened
  `file:…?mode=ro`. They are never copied into the worktree.
- **Reports and logs carry counts and table names only**, enforced by the
  `ProtectedSourceError(code)` discipline inherited from `events/importers.py` (§3.7).
- ⚠ Observed while surveying: `/data/tmp/rds-export/rds-aisl_prod-*.db` are mode **644**
  while the CMP exports beside them are 600. The containing directory is 700 so they are
  not exposed, but the inconsistency is worth the owner knowing.

### 8.7 Increment order, so the site fills up progressively

Each row is a landable increment that visibly changes the site, rather than one big-bang
run that either works or does not:

| # | Lands | Visible effect |
| --- | --- | --- |
| A | CMP content: courses, homework, questions, projects, criteria | `/courses` and homework pages show 616 real questions instead of 27 |
| B | Wiki + podcast + articles into `ContentDocument` | those surfaces read from the database, per the owner's ruling |
| C | Events + registrations | event pages show real registration counts |
| D | Accounts + enrollments | leaderboards populate |
| E | Submissions + answers + peer reviews | dashboards and scoring populate |
| F | Historical scoring | pre-2024 cohorts and certificates |

A is the first implementation increment because it is the one the owner reported.

## 9. Order of extraction

Behaviour-preserving first. Each step leaves the tree green. **No step mixes "move code"
with "change behaviour".**

⚠ **No step deletes a file under `scripts/`** (§4). Relocations must keep the ledger's
`copied-files.tsv` destination paths valid, so a "move" of a ledger-pinned file is a *copy
plus re-export*, not a `git mv`, until the ledger issue lands.

1. **Create `scripts/prod/`**; move `prepare_local_data.py` → `scripts/prod/run.py`
   verbatim. Update the four call sites. No shim. `prepare_local_data.py` is **not**
   ledger-pinned, so this one is a true rename.
2. **Extract the five seams** from `run()` into `scripts/prod/sources/`: event identity,
   CMP snapshot, course catalog, course modules, registration. Pure moves; `run()` becomes
   composition.
3. **Relocate** the remaining real-data scripts into `scripts/prod/` (public projection,
   course-platform sync, historical scoring, course-modules manifest, review DB CLI).
   Path changes only. Reconcile against `script-inventory.md` first.
4. **Add the registry and `SourceProvenance`** (§5); register `events.importers` in place.
5. **`modes.py`** with `ImportMode` and `NEVER_IMPORTABLE`, threaded through `extract`.
   Default `SANITIZED` — no behaviour change.
6. **`provenance.py`**; assert the CMP snapshot is a pipeline export and fresh (§6.4).
7. **Import-boundary test** (§6.5 layer 1) plus seeder defanging and the precedence change
   (§6.5, §7.3). **Behaviour changes here** — its own commit, family-description ownership
   flip called out explicitly.
8. **Schema-drift adoption** (§6.4): `KNOWN_UNMAPPED_TABLES`, version bump.
9. **`Makefile:408`** → newest `/data/tmp/rds-export/rds-prod-*.db`, resolved not
    hardcoded, still overridable. *(This takes ownership of `Makefile`; see §12.)*
10. **Homework binding** (§7.2): adopt CMP's slug verbatim and re-point the module
    binding; delete the regex. `homework_slug_overrides` becomes dead and is dropped
    under its own issue.

Steps 1-6 are behaviour-preserving. Steps 7-10 are the visible fix.

## 10. What gets deleted

⚠ **No file is deleted.** The first draft proposed removing `add_data.py`,
`add_more_test_data.py` and `load_rds_export.py`; the script inventory and the independent
checks in §4 overturned all three. Only code *within* surviving files goes:

| Deleted | Why |
| --- | --- |
| `local_cmp_content_import.py:50` — the `^hw(\d+)$` regex | Replaced by the per-cohort override table (§7.2). It would break `ml-zoomcamp-2026` |
| row-count escape hatch in `_validate_source_schema` | Replaced by `KNOWN_UNMAPPED_TABLES` (§6.4); it does not work against production (§3.2) |
| unconditional `course.description` assignment, `curriculum_import.py:339` | Replaced by declared field ownership (§7.3). Held by another lane — coordinate before touching |

File deletion under `scripts/` is a **separate groomed ledger issue** covering the nine
genuinely unused files the inventory lists (~2,000 lines). It is one issue, not nine, and
it is not this refactor.

## 11. Out of scope

- Internals of `build_public_projection.py` and the public projection pin.
  `content_sync/dtc_content/contract.py` is **not** re-pinned.
- Full-fidelity learner import (§6.3) — separate groomed issue.
- Business-key uniqueness constraints for submissions/answers/reviews — needs a production
  preflight first.
- Extracting `review_import/workflow.py` (1658 lines) — next candidate, not this change.
- Consolidating `content/event_description_bridge.py` into `events/` (§3.8) — different
  lane's code.
- Adding cohorts to `COHORT_FAMILY_IDENTITIES` — owner decision (§6.6).
- `MemberProfile` and `Certificate` models (Codex map §8.6, §8.7).

## 12. Open questions for the owner

1. ~~The six unmapped cohorts.~~ **Answered: skip all six for now** — `ai-bootcamp-2025`,
   `ai-hero-2025`, `ai-hero-2026`, `sma-zoomcamp-2026`, `ai-buildcamp-2`, `ai-buildcamp-3`.
   They are excluded by an explicit named list with a recorded reason each, not by an
   unmatched branch, and appear in the verification output with their row counts and their
   skipped dependent rows. Four need only a mapping entry to return; `ai-buildcamp-2`/`-3`
   need real design first, because family+year cannot express an edition number.
2. ~~Family description precedence.~~ **Answered:** `Course.description` comes from a
   repository-authored `SITE.md`; the README is never read, and its absence leaves the
   stored description untouched. Authored for the three module repos only.
   *Known follow-up, deliberately deferred:* `de-zoomcamp`, `mlops-zoomcamp` and
   `sma-zoomcamp` keep their curated text with no `SITE.md`; unifying the two sources is
   a later decision, not a temporary inconsistency to design around.
3. **`Makefile` ownership.** Step 10 edits it. No other lane has claimed it; this plan
   takes it.
4. **Reconciliation with `script-inventory.md`** (§4), which did not exist when this was
   written.
