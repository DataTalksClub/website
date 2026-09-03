# CMP content import fix

## Root cause

Real CMP questions, homework copy, and projects never reached the local production-prep database because **the orchestrated path never called the sanitizing importer**. This is not “the importer cannot copy those tables”.

1. `scripts/prepare_local_data.py` (called by `make production-prep-local` / `make production-prep-dataset`) ran `migrate` → `seed_local_courses` → `prepare_local_course_modules`. It never invoked `review_import` or any CMP snapshot reader.
2. `courses/services/local_course_seed.py:268-272` and `:349-351` write the placeholder strings the live database showed (`Practice assignment for …`, `Production-like generated project: …`). Titles such as `Project Attempt N` come from `scripts/production_like_course_specs.json`.
3. The 27 questions on the live database, including homework-01’s two prompts, come from course-repository YAML via `prepare_local_course_modules` (`content_sync` fixtures / 2026 `homework.yaml`), not from CMP.
4. `make review-data` *can* copy allowlisted content (`courses_question`, `courses_homework`, `courses_project`, …) and already sanitizes learner tables. It was a separate workflow, and it fail-closed on empty upstream tables the pinned schema does not have (`review_import/workflow.py` `_validate_source_schema`, previously around line 728): `schema-unknown-table table=courses_emailcampaign count=3`.

`scripts/load_rds_export.py` was not used and remains disabled.

## What changed

- **Empty unknown tables are skipped**, not dropped by hand. `_validate_source_schema` now counts rows on source-only tables. Zero-row tables are listed as `skipped_empty_unknown_tables` and ignored. A source-only table with rows still fails closed.
- **Production-prep now imports the sanitized CMP snapshot** before the catalogue seed and module overlay (`courses/services/local_cmp_content_import.py`, wired from `scripts/prepare_local_data.py --cmp-source-db` and `PRODUCTION_PREP_CMP_SOURCE` in the Makefile).
- Fixture courses `fake-course` and `fake-course-2` are dropped, with dependent homework/questions/projects/stats.
- ~~CMP `hwN` slugs on the 2026 module cohorts are rewritten to `homework-0N` so `prepare_local_course_modules` (`preserve_existing_records=True`) adopts them by slug~~ — reverted in `1f4be1a`/`2de9760` (CMP owns homework identity, copied verbatim), and `prepare_local_course_modules` itself is gone: course content now comes in through `scripts/prod/sync_course_repositories.py`, the one path the push webhook drives, which has no preservation mode. See `_docs/runbooks/course-content-push-and-pull.md`.
- `seed_local_courses` no longer overwrites an imported cohort description, and does not add placeholder homework/projects when the cohort already has assignments.

## Verification (scratch DB `.tmp/cmp-import-prep.sqlite3`)

Sanitization:

- enrollments 0, submissions 0, project submissions 0, users 0
- `fake-course` / `fake-course-2` absent

Content:

| metric | value |
| --- | --- |
| questions | 600 (583 from CMP after dropping 12 fixture questions, plus YAML questions for module homework CMP does not have) |
| homework | 125 |
| projects | 44 |
| `Practice assignment for` | 0 |
| `Production-like generated` | 0 |

`llm-zoomcamp-2026` homework question counts:

| slug | questions | source |
| --- | --- | --- |
| homework-01 | 6 | CMP (`hw1` remapped) |
| homework-02 | 6 | CMP |
| homework-03 | 0 | CMP (empty in dump) |
| homework-04 | 0 | CMP |
| homework-05 | 0 | CMP |
| homework-06 | 2 | course-repo YAML (not in CMP) |
| homework-07 | 2 | course-repo YAML (not in CMP) |
| dlt | 0 | CMP workshop row, kept |

Module curricula preserved:

- `ml-zoomcamp-2026`: 9 modules / 105 units
- `llm-zoomcamp-2026`: 7 modules / 72 units
- `ai-dev-tools-2026`: 4 modules / 4 units

Browser: served `.tmp/cmp-import-prep.sqlite3` on port 8012 (not 8000). `GET /courses/llm-zoomcamp/2026/homework/homework-01` returned 200 and rendered CMP prompts (`Indexing and searching`, `RAG with chunking`, `Turning it into an agent`). It did not render `What did you learn from this module?`. Server stopped. Port 8000 was left running.

`make review-data` against this dump should now pass schema validation without dropping the three empty tables. A dry-run was not repeated end-to-end here (public-page smoke on the review artifact is slow); the empty-table skip is covered by `review_import/tests/test_workflow.py`.

## What still cannot be imported, and why

- **Learner data** (accounts, enrollments, submissions, answers, peer reviews, Datamailer, sessions). Deliberately denylisted. Correct posture.
- **`fake-course` / `fake-course-2`**. Visible in the dump; excluded from the website import.
- **`ml-zoomcamp-2026` and `ai-dev-tools-2026` homework questions.** Those cohorts are not in the CMP dump. Their questions still come from course-repository YAML (including the “How many lesson pages…” / reflection placeholders).
- **`llm-zoomcamp-2026` homework-06 and homework-07.** Not present in CMP; YAML placeholders remain.
- **`llm-zoomcamp-2026` homework-03/04/05.** Present in CMP with zero questions.
- **Real CMP project titles** include `Project Attempt N` for many historical cohorts. That string is production copy, not the seed. The seed marker that is gone is `Production-like generated`.
- **Registration campaigns.** 0 rows in this dump.
- **Three empty upstream tables** (`courses_emailcampaign`, `courses_systemprojectevaluation`, `courses_systemevaluationcriteriaresponse`). Skipped until the course-platform pin moves.

## Owner decisions

1. **Pin move vs skip-empty-tables.** Skipping empty unknown tables is implemented. Moving the pin to CMP `6d3cc0e` (migrations 0041/0043) is still required before those tables can exist in the target schema; they have no rows today.
2. **YAML vs CMP for 2026 homework that CMP lacks.** homework-06/07 (LLM) and all ML / AI Dev Tools 2026 homework still use repository YAML. Replacing those placeholders needs either a newer CMP dump or real questions in the course repositories.
3. **`ai-dev-tools-2026` vs `ai-dev-tools-zoomcamp-2026`.** Module import now publishes the cohort as `ai-dev-tools-2026` (family-identity normalization, issue #308). `scripts/verify_local_dataset.py` still names `ai-dev-tools-zoomcamp-2026`. That mismatch is owned by the family-identity work, not this import.
4. **`make review-data` still imports `fake-course`.** Exclusion is in the production-prep merge only, so the content-review database remains a faithful public-content snapshot. Exclude there too if the review homepage must not list fixture courses.
