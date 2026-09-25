# Project gallery repository enrichment — playbook

How the project gallery's repository enrichment is produced, reviewed, staged
and injected — and how to run the whole pipeline again for a **new cohort of
projects** (for example, when a new zoomcamp year's capstone repositories
land). Issue #416 produced the first release; this runbook is the recipe so
the next round is a repeat, not an archaeology dig.

The direction of work is the ingest rule from `data-ingest.md`: **source →
staging → production database**, one way. Everything analytical lives in
`.tmp/` scratch (ephemeral); the only durable artifacts are the two staged
JSONL files under `~/prod/dtc-data/content-staging/` and the rows in the
database.

## 1. What exists

| Layer | Where | Shape |
| --- | --- | --- |
| Enrichment (prose) | `~/prod/dtc-data/content-staging/project_gallery_repo_enrichment.jsonl` | one row per distinct submitted repository (5,224 as of 2026-09-20: legacy-2021-2023 1,279 + prod-2024-2026 3,945): `repo`, `url`, `effective_url`, `era`, `availability`, `courses`, `years`, `assignments`, `n_submissions`, score facts (upstream only), `what_it_is`, `interesting`, `why_check`, `improvements`, `topics`, `confidence` |
| Structured content | `~/prod/dtc-data/content-staging/project_gallery_structured.jsonl` | `course-structured-v1` records from the same README snapshots (4,976 as of 2026-09-21: prod-era 2024-2026 3,775 + legacy 2021-2023 1,201), one per repo that has one: `repo`, `course`, `schema`, `confidence`, then omit-if-unevidenced `title`/`summary`/`provider`/`deployment_style`/`technologies`/`course_fields` |
| Database | `courses.models.ProjectRepoEnrichment` | keyed on lowercased `owner/name`; carries the gallery-facing fields plus the structured record stored whole as JSON |
| Read path | `/courses/projects` gallery template | renders card summary + up to 4 topics + first 4 `course_fields` entries when `confidence != low` and the repo is live. Field keys render dynamically — **a new course family needs no code change** |

The two staged files are **one reviewed release**: they are cross-checked at
import (a structured record without an enrichment row refuses with
`structured_without_enrichment_row`), so they are always replaced together and
imported together.

## 2. The pipeline, stage by stage

### A — Export the submission set

The upstream is the gallery's own submissions: distinct `owner/name` repository
slugs per course cohort, with each repo's course/cohort/assignment facts. Take
them from the database the gallery reads (never author them by hand). Freeze
the set — the rest of the pipeline analyses exactly this list.

### B — Probe liveness

For every slug, one GitHub API call resolves live / moved / gone and the
effective URL. **The org's API budget is shared (~5k requests/hour across
every agent and job on this machine) — never probe concurrently with another
fetching pipeline.** Probe scripts are resumable; re-run until zero pending.
Dead repositories stay in the enrichment with `availability` set accordingly;
the gallery renders them as "gone" chips. Nothing else downstream analyses a
dead repo's README.

### C — Snapshot READMEs and metadata

For each live repo: the GitHub metadata record (`meta`, includes
`description`) and the raw-CDN README text, stored as one JSON per repo —
`repos/<owner>__<name>.json` with `meta`, `readme_text`, `requested_slug`.
These snapshots are the **only** text the enrichment and structured passes may
ground claims in. Store them under `.tmp/<experiment>/repos/`. Sanity-check
the encoding on arrival: a README fetched UTF-16 but stored latin-1 comes out
NUL-interleaved (`\x00` between characters) and silently matches nothing at
the evidence audit — strip the NULs in place before extraction (first-release
case: 5 snapshots, see the experiment's `MOJIBAKE_REPAIR_LOG.md`).

### D — Prose enrichment (why a project is worth a look)

Per-repo prose fields (`what_it_is`, `interesting`, `why_check`,
`improvements`, `topics`, `confidence`) are produced offline: slice the repo
set into batches, have analyser agents read the snapshots, merge into one row
per distinct repo keyed on the lowercased slug. A repo may be submitted by
several learners across cohorts — the enrichment row is per **repository**,
and the per-learner facts (scores, cohort membership) stay with the
submissions upstream.

### E — Structured content (what the project is made of)

1. **Define the per-course fields first** (§3) for every course family in the
   batch — one table per family in the schema doc.
2. **Briefs**: slice the live repos into course cohorts (first course in the
   row's `courses` string), then batches of 30; clean each README (strip
   badges/images/HTML/table noise, cap ~2,400 chars) under a one-line header
   (`=== owner/name | course=… | conf=…`). One brief file per batch.
3. **Extraction**: per-cohort agents read the briefs and emit one JSONL record
   per repo, same order as the brief, per the schema contract
   (`course-structured-v1`). Every repo gets a record — a thin/empty README
   yields an envelope-only record with `confidence: "low"`.
4. **Validation**: the validator re-checks every record against its brief —
   required keys, enums, token charset, field bounds, allowed
   `course_fields` keys, brief coverage, duplicates — and warns when evidence
   is not verbatim in the brief. Iterate to **0 errors**; every warning needs
   a re-read and a deliberate keep (a real excerpt differing only in
   whitespace, never a paraphrase).
5. **Merge**: concatenate per-batch outputs and assert one record per live
   repo — the release merger (`merge_prod_release.py`) *refuses* a
   cross-cohort duplicate slug rather than silently deduping; adjudicate the
   collision by re-extracting or dropping one batch, then re-merge.
6. **Review queue (mandatory)**: scan every record for forbidden terms —
   scores, points, grading, passing/failing, peer review, the scoring
   pipeline, emails, personal names. Adjudicate every hit in writing:
   subject-matter vocabulary (a "grade-prediction dataset", exam-score data,
   sports "scores") is benign; anything touching learner assessment outcomes
   is a record change. Re-run until the queue closes clean.
7. **Run it as a wave (multi-agent)**: one extractor agent per 1–3 batches,
   each with a self-contained prompt (schema path, its stems, the write
   protocol, the gates). The rules that kept the prod-era wave (135 batches,
   2026-09-21) convergent:
   - **Claim files, first-writer-wins.** An extractor writes a
     `CLAIM_<range>.txt` before working and skips any stem whose
     `outputs/<stem>.jsonl` already exists when it goes to land.
   - **Takeovers.** A range whose scratch/output has been silent for ~1h is
     dead — re-dispatch it under a new claim name. Silence, not status
     reports, is the liveness signal; a dead range is cheaper to re-extract
     than to wait on.
   - **Idempotent writes only.** Same-command exec duplicates were observed
     throughout the wave: write part files with a truncate (`cat >` /
     `write_text`), never an append; merge parts into the final output with
     one write; never `rm` in the same command as a merge; re-check record
     counts after every merge — a doubled count is a duplicated write, so
     regenerate from the parts.
   - **Two local gates per batch**: the validator (0 errors) and the
     verbatim evidence check (0 bad excerpts). The validator's
     "evidence not found" warnings are structural — it matches the whole
     `README:`-prefixed string against brief text that never carries those
     markers — so the verbatim checker is the real evidence gate.
   - **The brief is not the evidence authority.** Briefs cap READMEs at
     ~2,400 chars, so excerpts quoted past the cap legitimately fail a
     brief-level verbatim check (first release: 1,296 such spans, every one
     verified in its full snapshot). The authoritative evidence gate is the
     snapshot audit run over all stems at merge time — `regen_residual.py`
     regenerates the worklist (first release: 23,193 evidence fields,
     0 fails).
   - **Evidence repair classes** seen repeatedly: a missing markdown prefix
     (`[`, `*`, `#`), a missing space before a capitalized sentence, a
     >160-char span (re-pick a shorter sub-span), and an excerpt lifted
     from the wrong repo's brief section.

### F — Stage (the reviewed release)

Copy the two canonical files into `~/prod/dtc-data/content-staging/` as
`project_gallery_repo_enrichment.jsonl` + `project_gallery_structured.jsonl`,
**together**, and record their md5s in the coordination log. The staged pair
is the release; nothing in the repo holds data (database-only content —
`_docs/architecture/database-only-content.md`).

### G — Inject

```bash
# rehearse against any local SQLite target first — reports would-be counts, writes nothing
env -u DJANGO_SETTINGS_MODULE -u DTC_SQLITE_PATH -u DTC_ENVIRONMENT \
uv run --frozen python scripts/prod/import_project_repo_enrichment.py \
    --database .tmp/production-prep-dataset/dataset.sqlite3 --dry-run

# apply (idempotent: a replay reports every row unchanged)
env -u DJANGO_SETTINGS_MODULE -u DTC_SQLITE_PATH -u DTC_ENVIRONMENT \
uv run --frozen python scripts/prod/import_project_repo_enrichment.py \
    --database .tmp/production-prep-dataset/dataset.sqlite3
```

The importer needs `courses_projectrepoenrichment` to exist in the target. A
dataset DB built before the gallery legs has no such table and no Django
migration bookkeeping — the fix is a full `scripts/production_data.py dataset`
rebuild (its gallery leg imports the staged pair itself), never a hand-made
table.

**Production is an operator step**, never an improvised shell: the same
command with the reviewed target flags and the five names the deployed task
carries (the script refuses by name when any is missing — that refusal is the
guard working):

```bash
export DATABASE_URL=…        # from Secrets Manager
export DJANGO_SECRET_KEY=…   # from Secrets Manager
export VERSION=… SOURCE_SHA=… IMAGE_DIGEST=…   # read off the released task definition
uv run --frozen python scripts/prod/import_project_repo_enrichment.py \
    --deployment-target website-production \
    --allow-production-write website-production
```

### H — Verify

Row counts on the target (`SELECT COUNT(*), SUM(structured IS NOT NULL) FROM
courses_projectrepoenrichment;`), and the gallery page
(`/courses/projects`) rendering 200 with structured lines visible on
enriched cards. A local dataset rebuilt through
`scripts/production_data.py dataset` picks the gallery import up as part of
its normal legs, so a fresh dataset DB is also the end-to-end rehearsal.

## 3. Adding a new course family

Structured content is **per course family**: a small table of fields that
make sense for that course's projects, derived from the course's own
curriculum (its repository README — module list, project stages). Rules:

- Field values are either short free text (≤80–260 chars, say what it is) or
  a **tech token** (lowercase `[a-z0-9.+#_-]`, ≤28 chars, e.g. `airflow`,
  `qdrant`, `rag`) for a pipeline/tool slot.
- Keep families small (3–7 fields); reuse common slots (`problem_domain`,
  `data_source`, `serving`, `dashboard`, `model_type`, `eval_metric`)
  wherever they fit so cross-course rendering stays coherent.
- Add the family table to the schema doc and the allowed `course_fields`
  keys to the validator. The gallery template renders whatever keys exist —
  no runtime code changes.

Families so far: `de` (pipeline stages), `ml` (problem→deployment arc),
`mlops` (experiment→operations loop), `llm` (retrieval-to-agent arc:
`llm_provider`, `approach`, `vector_store`, `serving`), `adt` (AI-native
build: `tool_chain`, `dev_stage`, `llm_provider`), `sma` (market-analysis
loop: `data_source`, `analysis_focus`), `ai` (generic for the small AI
cohorts).

## 4. Non-negotiables

- **Privacy guard**: enrichment prose and structured records never mention
  scores, points, grading, passing/failing, peer review, or the scoring
  pipeline; no emails; no personal names. The review-queue scan is part of
  the release, and its adjudication is recorded.
- **Snapshots only**: every claim grounds in the frozen snapshot of that
  repo's README/description; evidence excerpts are verbatim and ≤160 chars.
- **Omit-if-unevidenced**: absence of evidence is absence of the field —
  never a guess, placeholder, or `unknown`.
- **One release, two files**: the enrichment and structured staging files are
  replaced and imported together, and their md5s recorded.
- **Scratch is ephemeral**: `.tmp/` working dirs (briefs, outputs, merge
  scripts) are disposable; this runbook + the staged files are the memory.

## 5. Where the first-release artifacts live

- Legacy pass (2021–2023 cohorts): `.tmp/project-gallery-416/structured/` —
  `SCHEMA.md`, briefs, per-batch outputs, validator, merge, review queue.
- Prod-era pass (2024–2026 cohorts):
  `.tmp/project-gallery-416/structured-prod/` — `PROD_SCHEMA.md` (adds the
  `llm`/`adt`/`sma`/`ai` families), briefs builder (`make_briefs_prod.py`),
  validator (`validate_records_prod.py`), outputs, and the release chain:
  `regen_residual.py` (snapshot-audit fails → worklist) →
  `repair_evidence.py` (align each failing excerpt to its verbatim snapshot
  span; dry-run by default) → `hand_fix_evidence_prod.py` (the cases alignment
  cannot settle) → `merge_prod_release.py` (full-coverage gate, quote
  normalization, forbidden-term scan, legacy+prod merge).
- Importer: `scripts/prod/import_project_repo_enrichment.py` (its docstring
  restates the release rules); ingest entry: `data-ingest.md` §22.
