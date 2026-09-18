# Project gallery submission coverage

Issue: #421

This audit contains aggregate counts only. It does not contain learner names,
addresses, repository URLs, source identifiers, or source payloads.

## Inputs and counting rules

- Target database: the local database served at `localhost:8000`, measured
  before the authorized cutover on 2026-09-17 and after it on 2026-09-18.
- Current-cohort authority: CMP export
  `rds-prod-20260910-182337.db`, SHA-256
  `15d12ec48236c720e5ceb1e2f82e4e26c6970a695546507ca00168f830d90362`.
- Historical authority: `DataTalksClub/zoomcamp-scoring` revision
  `b6830b1256f91a1491dad5d2b7cef3513a55c4d8`.
- `source` is the aggregate number of CMP `courses_projectsubmission` rows for
  the edition. A dash means that source has no row for the edition; historical
  editions are instead supplied by the legacy importer.
- `imported` is the number of live target `ProjectSubmission` rows under a
  visible family and visible cohort.
- `eligible` is the subset of imported rows that is not volunteer-review-only
  and has `passed = true`, matching unscoped `/courses/projects` discovery.

## Visible catalogue matrix

| Family | Cohort | Source | Imported | Eligible | Classification |
| --- | --- | ---: | ---: | ---: | --- |
| AI Dev Tools | 2025 | 135 | 135 | 118 | Covered by authorized CMP import |
| AI Dev Tools | 2026 | - | 0 | 0 | No authoritative submission history found |
| Data Engineering | 2022 | legacy | 95 | 88 | Covered by historical importer |
| Data Engineering | 2023 | legacy | 298 | 260 | Covered by historical importer |
| Data Engineering | 2024 | 397 | 397 | 360 | Covered by authorized CMP import |
| Data Engineering | 2025 | 398 | 398 | 358 | Covered by authorized CMP import |
| Data Engineering | 2026 | 686 | 686 | 612 | Covered by authorized CMP import |
| LLM | 2024 | 221 | 221 | 177 | Covered by authorized CMP import |
| LLM | 2025 | 157 | 157 | 136 | Covered by authorized CMP import |
| LLM | 2026 | 536 | 536 | 363 | Covered by authorized CMP import; 7 volunteer-review-only rows are ineligible |
| Machine Learning | 2021 | legacy | 212 | 173 | Covered by historical importer |
| Machine Learning | 2022 | legacy | 307 | 260 | Covered by historical importer |
| Machine Learning | 2023 | legacy | 518 | 429 | Covered by historical importer |
| Machine Learning | 2024 | 438 | 438 | 391 | Covered by authorized CMP import |
| Machine Learning | 2025 | 885 | 885 | 766 | Covered by authorized CMP import |
| Machine Learning | 2026 | - | 0 | 0 | No authoritative submission history found |
| MLOps | 2022 | legacy | 91 | 82 | Covered by historical importer |
| MLOps | 2023 | legacy | 125 | 97 | Covered by historical importer |
| MLOps | 2024 | 171 | 171 | 138 | Covered by authorized CMP import |
| MLOps | 2025 | 230 | 230 | 200 | Covered by authorized CMP import |
| MLOps | self-paced | - | 0 | 0 | No authoritative submission history found |
| Stock Market Analytics | 2024 | 16 | 16 | 15 | Covered by authorized CMP import |
| Stock Market Analytics | 2025 | 23 | 23 | 22 | Covered by authorized CMP import |
| Stock Market Analytics | 2026 | - | 0 | 0 | No authoritative submission history found |

Before the cutover, the target held 1,646 imported rows and 1,389 eligible
rows. That explained the observed three-family gallery exactly. After the
authorized local cutover it holds 5,939 imported rows and 5,045 eligible rows;
the database-derived gallery exposes all six visible families.

The selected CMP export contains 4,363 project submissions overall: 4,354 are
not volunteer-review-only and 3,704 are passed. The visible website catalogue
has authoritative CMP rows for current editions of all six course families.
The authorized run imported all 4,293 project submissions belonging to
reviewed visible cohorts. The remaining 70 source project submissions have no
reviewed project parent because their cohorts are owner-deferred; the history
importer reported them under the bounded `project` unresolved bucket and did
not invent target rows.

## Authorized local import and replay

The product owner authorized this import for `.tmp/local.sqlite3` only in issue
#421 comment `5724617954`. No deployed target or production database was used.
The selected source checksum is recorded above; command output and this audit
contain aggregate counts and bounded unresolved codes only.

The required order was executed as `import_cmp_content`,
`import_cmp_learners`, then `import_cmp_learner_history`:

- account preflight: 21,240 source accounts and 21,237 source email rows, with
  zero existing claims;
- history preflight: 478,631 rows across the nine allowed history tables, with
  zero existing claims;
- first account run: 21,240 account claims, all phases complete;
- first history run: 470,515 created claims and 8,116 unresolved references.
  The unresolved buckets were `cohort` 172, `homework` 227, `submission`
  1,209, `project` 70, `project_submission` 815 across its two dependent
  tables, `peer_review` 1,404, and `wrapped` 4,219;
- privacy verification: claimed accounts with a usable password, staff flag,
  superuser flag, or social-account row were all zero;
- replay: content created no cohort, family, or campaign, removed no homework
  or project, retained no stale question or criterion, and rebound no module;
  account and history totals stayed at 21,240 and 470,515 claims. The post-run
  dry run reported the same per-table claim totals and the target remained at
  5,939 project submissions.

The first replay also revealed that the content importer still deleted and
recreated question and criterion definitions. Once learner history referenced
those rows, Django correctly blocked criterion deletion. The importer now
reconciles questions by text/source order and criteria by description—the same
natural keys used by the history importer—so learner foreign keys survive.
Referenced stale rows are retained and aggregate-counted instead of deleted or
rendered through a verbose `ProtectedError`. The learner command summary also
now emits reconciliation counts rather than lists of source ids.

The pre-cutover database snapshot is retained locally at
`.tmp/issue-421-pre-cmp.sqlite3`; it is gitignored and must never be committed
or attached because local databases may contain protected data.

## Public review-count semantics

The gallery's `Reviews` value counts distinct `PeerReview` rows received by the
submission whose state is `SUBMITTED`. An assigned but unfinished review is not
counted. `ProjectEvaluationScore` rows are deliberately not counted: they are
criterion-level derived scores, so one completed review can create several of
them and the number would describe rubric size rather than people who reviewed
the project. The count uses an aggregate annotation alongside a distinct vote
count, avoiding join multiplication and per-row queries. It exposes only a
count already implied by the public assessment state, never review text,
reviewer identity, or criterion responses. Pass status remains the separate
stored `ProjectSubmission.passed` fact and is never inferred from this count.
