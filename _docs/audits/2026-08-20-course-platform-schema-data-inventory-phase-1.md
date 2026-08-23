# Phase-1 current course-platform schema and data inventory

This is the current phase-1 evidence slice for the adopted course-platform schema. It records the local Course-to-Cohort rename and squashed courses migration while leaving the reusable Course entity and phase-2 URL contract for a later change.

The 2026-08-14 audit remains historical evidence for the pre-squash Course graph. This document is the active phase-1 inventory; the [migration history](../adoption/course-platform/migration-squash-gate.md) and [verification](../adoption/course-platform/verification.md) record the local-only reset and adoption evidence.

## Binding and method

- Snapshot repository: `DataTalksClub/website`
- Snapshot ref: `refs/heads/main`
- Snapshot SHA: `b7c693efbb4ea8c429fd3525d129bbd84504719d`
- Source pin commit: `98a235283904b4ef9ad29e196298540756cf1bcc`
- Snapshot at (UTC): `2026-08-20T14:03:00Z`
- Repository provenance observed at (UTC): `2026-08-20T14:03:01Z`
- Models (current adopted target): `32`
- Models (pinned CMP): `26`
- Models (target overlays): `6`
- Current migrations: `accounts=12; courses=52; data=5`
- Pinned CMP migrations: `accounts=10; courses=40; data=5`
- Adopted routes: `115`
- Pinned CMP commands: `13`
- Target-owned commands: `5`
- Current command registry: `22`

The source binding is the checked-in [CMP source pin](../adoption/course-platform/source-pin.json). The pinned ledger, overlay checksums, migration-squash gate, generated route/command inventory, and adoption verification are the evidence authority: [copied-files.tsv](../adoption/course-platform/copied-files.tsv), [integration-patched-files.tsv](../adoption/course-platform/integration-patched-files.tsv), [migration-squash-gate.md](../adoption/course-platform/migration-squash-gate.md), [behavior-inventory.md](../adoption/course-platform/behavior-inventory.md), and [verification.md](../adoption/course-platform/verification.md).

The inventory was made from checked-in source declarations, migration files, manifests, and generated registries only. No database, production row, export, snapshot, PII, protected CMP data, AWS resource, or network source was read. The validator is stdlib-only and performs structural checks; it does not import Django, call GitHub, open a database, or update a source pin.

## Baseline interpretation

The current adopted target surface is exactly 32 model classes: 26 classes retained from the pinned CMP baseline and six target-owned overlays. The six overlays are the three account identity/reconciliation classes from [#100](https://github.com/DataTalksClub/website/issues/100) and the three course registration-count aggregate classes from [#133](https://github.com/DataTalksClub/website/issues/133). They are not presented as pinned CMP source.

Migration counts intentionally keep two baselines separate. The phase-1 target graph has 12, 10, and 5 active numbered migrations for `accounts`, `courses`, and `data`; the pinned CMP graph remains 10, 40, and 5. The active `courses` graph retains the local phase-1 `Course`/`Cohort` schema and its reviewed curriculum migrations, while the `courses_course` table is represented by `Cohort`, including `Cohort.outcome`. The generated adopted behavior inventory remains the route/command authority: 115 routes, 13 pinned CMP commands, five target-owned commands, and a 22-command current registry. This document links that inventory rather than copying its route or command tables.

## Model/table inventory

| Key | App | Model class | Table | Source path | Migration provenance | Provenance | Owner issue | Classification | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| accounts.CustomUser | accounts | CustomUser | accounts_customuser | accounts/models.py | accounts/migrations/0001_initial.py; accounts/migrations/0011_identity_expansion.py; accounts/migrations/0012_backfill_normalized_identity.py | pinned-cmp | #30 | identity | [declaration](../../accounts/models.py); [migration](../../accounts/migrations/0001_initial.py) |
| accounts.Token | accounts | Token | accounts_token | accounts/models.py | accounts/migrations/0002_token.py | pinned-cmp | #30 | identity | [declaration](../../accounts/models.py); [migration](../../accounts/migrations/0002_token.py) |
| accounts.AccountIdentityAlias | accounts | AccountIdentityAlias | accounts_accountidentityalias | accounts/models.py | accounts/migrations/0011_identity_expansion.py | target-overlay | #100 | identity | [declaration](../../accounts/models.py); [migration](../../accounts/migrations/0011_identity_expansion.py) |
| accounts.AccountIdentityQuarantine | accounts | AccountIdentityQuarantine | accounts_accountidentityquarantine | accounts/models.py | accounts/migrations/0011_identity_expansion.py | target-overlay | #100 | history | [declaration](../../accounts/models.py); [migration](../../accounts/migrations/0011_identity_expansion.py) |
| accounts.AccountReconciliationRun | accounts | AccountReconciliationRun | accounts_accountreconciliationrun | accounts/models.py | accounts/migrations/0011_identity_expansion.py | target-overlay | #100 | history | [declaration](../../accounts/models.py); [migration](../../accounts/migrations/0011_identity_expansion.py) |
| courses.Cohort | courses | Cohort | courses_course | courses/models/cohort.py | courses/migrations/0042_course_schema_bridge.py | pinned-cmp | #30 | definition | [declaration](../../courses/models/cohort.py); [migration](../../courses/migrations/0042_course_schema_bridge.py) |
| courses.RegistrationCampaign | courses | RegistrationCampaign | courses_registrationcampaign | courses/models/cohort.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | definition | [declaration](../../courses/models/cohort.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.CourseRegistration | courses | CourseRegistration | courses_courseregistration | courses/models/cohort.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | operational | [declaration](../../courses/models/cohort.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.Enrollment | courses | Enrollment | courses_enrollment | courses/models/cohort.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/cohort.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.LeaderboardComplaint | courses | LeaderboardComplaint | courses_leaderboardcomplaint | courses/models/cohort.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | operational | [declaration](../../courses/models/cohort.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.Homework | courses | Homework | courses_homework | courses/models/homework.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | definition | [declaration](../../courses/models/homework.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.Question | courses | Question | courses_question | courses/models/homework.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | definition | [declaration](../../courses/models/homework.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.Submission | courses | Submission | courses_submission | courses/models/homework.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/homework.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.Answer | courses | Answer | courses_answer | courses/models/homework.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/homework.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.HomeworkStatistics | courses | HomeworkStatistics | courses_homeworkstatistics | courses/models/homework.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | history | [declaration](../../courses/models/homework.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.WrappedStatistics | courses | WrappedStatistics | courses_wrappedstatistics | courses/models/wrapped.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | history | [declaration](../../courses/models/wrapped.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.UserWrappedStatistics | courses | UserWrappedStatistics | courses_userwrappedstatistics | courses/models/wrapped.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | history | [declaration](../../courses/models/wrapped.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.Project | courses | Project | courses_project | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | definition | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.ProjectSubmission | courses | ProjectSubmission | courses_projectsubmission | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.ProjectVote | courses | ProjectVote | courses_projectvote | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.ReviewCriteria | courses | ReviewCriteria | courses_reviewcriteria | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | definition | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.PeerReview | courses | PeerReview | courses_peerreview | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.CriteriaResponse | courses | CriteriaResponse | courses_criteriaresponse | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.ProjectEvaluationScore | courses | ProjectEvaluationScore | courses_projectevaluationscore | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | learner | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.ProjectStatistics | courses | ProjectStatistics | courses_projectstatistics | courses/models/project.py | courses/migrations/0001_initial.py | pinned-cmp | #30 | history | [declaration](../../courses/models/project.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.CourseRegistrationCountSourceRun | courses | CourseRegistrationCountSourceRun | courses_courseregistrationcountsourcerun | courses/models/registration_counts.py | courses/migrations/0001_initial.py | target-overlay | #133 | history | [declaration](../../courses/models/registration_counts.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.CourseRegistrationCountRevision | courses | CourseRegistrationCountRevision | courses_courseregistrationcountrevision | courses/models/registration_counts.py | courses/migrations/0001_initial.py | target-overlay | #133 | history | [declaration](../../courses/models/registration_counts.py); [migration](../../courses/migrations/0001_initial.py) |
| courses.CourseRegistrationCountSlot | courses | CourseRegistrationCountSlot | courses_courseregistrationcountslot | courses/models/registration_counts.py | courses/migrations/0001_initial.py | target-overlay | #133 | operational | [declaration](../../courses/models/registration_counts.py); [migration](../../courses/migrations/0001_initial.py) |
| data.DatamailerContactEvent | data | DatamailerContactEvent | data_datamailercontactevent | data/models.py | data/migrations/0001_initial.py | pinned-cmp | #30 | side-effect | [declaration](../../data/models.py); [migration](../../data/migrations/0001_initial.py) |
| data.DatamailerOutboxEvent | data | DatamailerOutboxEvent | data_datamaileroutboxevent | data/models.py | data/migrations/0002_datamaileroutboxevent.py | pinned-cmp | #30 | side-effect | [declaration](../../data/models.py); [migration](../../data/migrations/0002_datamaileroutboxevent.py) |
| data.DatamailerOutboxDispatchRun | data | DatamailerOutboxDispatchRun | data_datamaileroutboxdispatchrun | data/models.py | data/migrations/0003_datamaileroutboxdispatchrun.py | pinned-cmp | #30 | side-effect | [declaration](../../data/models.py); [migration](../../data/migrations/0003_datamaileroutboxdispatchrun.py) |
| data.DatamailerSendAudit | data | DatamailerSendAudit | data_datamailersendaudit | data/models.py | data/migrations/0005_datamailersendaudit.py | pinned-cmp | #30 | side-effect | [declaration](../../data/models.py); [migration](../../data/migrations/0005_datamailersendaudit.py) |

The six `target-overlay` rows are additive; they do not alter the 26 `pinned-cmp` model identities. `CustomUser` retains its pinned identity while its target-owned identity fields are extended by migrations 0011 and 0012. The phase-1 rename preserves the `courses_course` table identity while exposing it as `Cohort`.

## Phase-1 schema assertions

The phase-1 rename keeps the existing course table and attaches the new outcome copy to the active `Cohort` model.

| Model | Field | Declaration evidence | Migration evidence |
| --- | --- | --- | --- |
| courses.Cohort | outcome | [declaration](../../courses/models/cohort.py) | [migration](../../courses/migrations/0042_course_schema_bridge.py) |
## Migration baselines

| App | Current numbered migrations | Pinned CMP numbered migrations | Current evidence | Pinned evidence | Distinction |
| --- | ---: | ---: | --- | --- | --- |
| accounts | 12 | 10 | [current graph](../../accounts/migrations/0001_initial.py); [behavior inventory](../adoption/course-platform/behavior-inventory.md) | [pinned ledger](../adoption/course-platform/copied-files.tsv); [verification](../adoption/course-platform/verification.md) | 0011 identity overlays and 0012 normalized-identity backfill are target additions; no squash |
| courses | 52 | 40 | [legacy boundary](../../courses/migrations/0041_courseregistrationcountsourcerun_and_more.py); [bridge](../../courses/migrations/0042_course_schema_bridge.py); [behavior inventory](../adoption/course-platform/behavior-inventory.md) | [pinned ledger](../adoption/course-platform/copied-files.tsv); [verification](../adoption/course-platform/verification.md) | the deployed 0001–0041 identities remain available; fresh installs replace only the main 0001–0026, 0028–0029 branch, retain 0027 for the 0031 merge, cross at 0042, and finish at 0051; pinned CMP remains at 40 |
| data | 5 | 5 | [current graph](../../data/migrations/0001_initial.py); [behavior inventory](../adoption/course-platform/behavior-inventory.md) | [pinned ledger](../adoption/course-platform/copied-files.tsv); [verification](../adoption/course-platform/verification.md) | Current and pinned numbered graphs have the same count |

The checked-in [migration-squash gate](../adoption/course-platform/migration-squash-gate.md) records the compatibility repair. The deployed numbered course migrations remain in the repository so Django can recognize applied legacy identities; the deterministic `0042` bridge preserves the physical table and data before the repaired leaf. This inventory does not claim production authorization.

## Relationship edges

Every declared `ForeignKey`, `OneToOneField`, and `ManyToManyField` in the 32-model target surface appears once below. Course-owned edges in the active graph point to `Cohort`; the reusable `Course` entity is phase 2. `User` and `settings.AUTH_USER_MODEL` resolve to the adopted `accounts.CustomUser` identity; no relationship is inferred from reverse accessors.

| Key | Declaring field | Kind | Target | Classification | Owner / provenance | Evidence | Hand-off |
| --- | --- | --- | --- | --- | --- | --- | --- |
| accounts.Token.user | accounts.Token.user | ForeignKey | accounts.CustomUser | identity | pinned-cmp; #30 | [declaration](../../accounts/models.py) | #30 |
| accounts.AccountIdentityAlias.survivor | accounts.AccountIdentityAlias.survivor | ForeignKey | accounts.CustomUser | identity | target-overlay; #100 | [declaration](../../accounts/models.py) | #100; #23 |
| courses.Cohort.students | courses.Cohort.students | ManyToManyField | accounts.CustomUser through courses.Enrollment | learner | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #14; #15 |
| courses.RegistrationCampaign.current_course | courses.RegistrationCampaign.current_course | ForeignKey | courses.Cohort | definition | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #14; #15 |
| courses.CourseRegistration.campaign | courses.CourseRegistration.campaign | ForeignKey | courses.RegistrationCampaign | operational | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #15; #23 |
| courses.CourseRegistration.course | courses.CourseRegistration.course | ForeignKey | courses.Cohort | operational | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #14; #15; #23 |
| courses.CourseRegistration.user | courses.CourseRegistration.user | ForeignKey | accounts.CustomUser | operational | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #23 |
| courses.Enrollment.student | courses.Enrollment.student | ForeignKey | accounts.CustomUser | learner | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #14; #23 |
| courses.Enrollment.course | courses.Enrollment.course | ForeignKey | courses.Cohort | learner | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #14; #15 |
| courses.LeaderboardComplaint.enrollment | courses.LeaderboardComplaint.enrollment | ForeignKey | courses.Enrollment | operational | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #14; #23 |
| courses.LeaderboardComplaint.reporter | courses.LeaderboardComplaint.reporter | ForeignKey | accounts.CustomUser | operational | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #23 |
| courses.LeaderboardComplaint.resolved_by | courses.LeaderboardComplaint.resolved_by | ForeignKey | accounts.CustomUser | operational | pinned-cmp; #30 | [declaration](../../courses/models/cohort.py) | #23 |
| courses.Homework.course | courses.Homework.course | ForeignKey | courses.Cohort | definition | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14; #15 |
| courses.Question.homework | courses.Question.homework | ForeignKey | courses.Homework | definition | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14 |
| courses.Submission.homework | courses.Submission.homework | ForeignKey | courses.Homework | learner | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14; #23 |
| courses.Submission.student | courses.Submission.student | ForeignKey | accounts.CustomUser | learner | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14; #23 |
| courses.Submission.enrollment | courses.Submission.enrollment | ForeignKey | courses.Enrollment | learner | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14; #23 |
| courses.Answer.submission | courses.Answer.submission | ForeignKey | courses.Submission | learner | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14; #23 |
| courses.Answer.question | courses.Answer.question | ForeignKey | courses.Question | learner | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14; #23 |
| courses.HomeworkStatistics.homework | courses.HomeworkStatistics.homework | OneToOneField | courses.Homework | history | pinned-cmp; #30 | [declaration](../../courses/models/homework.py) | #14 |
| courses.Project.course | courses.Project.course | ForeignKey | courses.Cohort | definition | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #15 |
| courses.ProjectSubmission.project | courses.ProjectSubmission.project | ForeignKey | courses.Project | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectSubmission.student | courses.ProjectSubmission.student | ForeignKey | accounts.CustomUser | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectSubmission.enrollment | courses.ProjectSubmission.enrollment | ForeignKey | courses.Enrollment | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectVote.submission | courses.ProjectVote.submission | ForeignKey | courses.ProjectSubmission | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectVote.voter | courses.ProjectVote.voter | ForeignKey | accounts.CustomUser | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ReviewCriteria.course | courses.ReviewCriteria.course | ForeignKey | courses.Cohort | definition | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14 |
| courses.PeerReview.submission_under_evaluation | courses.PeerReview.submission_under_evaluation | ForeignKey | courses.ProjectSubmission | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.PeerReview.reviewer | courses.PeerReview.reviewer | ForeignKey | courses.ProjectSubmission | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.CriteriaResponse.review | courses.CriteriaResponse.review | ForeignKey | courses.PeerReview | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.CriteriaResponse.criteria | courses.CriteriaResponse.criteria | ForeignKey | courses.ReviewCriteria | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectEvaluationScore.submission | courses.ProjectEvaluationScore.submission | ForeignKey | courses.ProjectSubmission | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectEvaluationScore.review_criteria | courses.ProjectEvaluationScore.review_criteria | ForeignKey | courses.ReviewCriteria | learner | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14; #23 |
| courses.ProjectStatistics.project | courses.ProjectStatistics.project | OneToOneField | courses.Project | history | pinned-cmp; #30 | [declaration](../../courses/models/project.py) | #14 |
| courses.CourseRegistrationCountSourceRun.actor | courses.CourseRegistrationCountSourceRun.actor | ForeignKey | accounts.CustomUser | history | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133; #23 |
| courses.CourseRegistrationCountRevision.source_run | courses.CourseRegistrationCountRevision.source_run | ForeignKey | courses.CourseRegistrationCountSourceRun | history | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133; #15 |
| courses.CourseRegistrationCountRevision.campaign | courses.CourseRegistrationCountRevision.campaign | ForeignKey | courses.RegistrationCampaign | history | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133; #15; #23 |
| courses.CourseRegistrationCountRevision.cohort | courses.CourseRegistrationCountRevision.cohort | ForeignKey | courses.Cohort | history | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133; #14; #15 |
| courses.CourseRegistrationCountSlot.campaign | courses.CourseRegistrationCountSlot.campaign | ForeignKey | courses.RegistrationCampaign | operational | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133; #15 |
| courses.CourseRegistrationCountSlot.cohort | courses.CourseRegistrationCountSlot.cohort | ForeignKey | courses.Cohort | operational | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133; #14; #15 |
| courses.CourseRegistrationCountSlot.active_baseline_revision | courses.CourseRegistrationCountSlot.active_baseline_revision | OneToOneField | courses.CourseRegistrationCountRevision | operational | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133 |
| courses.CourseRegistrationCountSlot.prior_baseline_revision | courses.CourseRegistrationCountSlot.prior_baseline_revision | ForeignKey | courses.CourseRegistrationCountRevision | history | target-overlay; #133 | [declaration](../../courses/models/registration_counts.py) | #133 |
| courses.UserWrappedStatistics.wrapped | courses.UserWrappedStatistics.wrapped | ForeignKey | courses.WrappedStatistics | history | pinned-cmp; #30 | [declaration](../../courses/models/wrapped.py) | #30 |
| courses.UserWrappedStatistics.user | courses.UserWrappedStatistics.user | ForeignKey | accounts.CustomUser | history | pinned-cmp; #30 | [declaration](../../courses/models/wrapped.py) | #30; #23 |

The classification is one controlled value per edge. Open decision issues are hand-offs, not inferred approvals: #14 owns curriculum/history semantics, #15 owns family/cohort mapping, #23 owns privacy/retention, #16 owns consumer/redirect inventory, and #21/#22 own Relay/Datamailer and purpose decisions. #30, #100, and #133 identify the owning implementation/baseline evidence where no open policy decision is being inferred.

## Export and compatibility boundaries

The rows below name each exported or compatibility surface without duplicating the 115-route/22-command generated tables. Every row has one classification, provenance, evidence, and a hand-off.

| Key | Boundary | Classification | Owner / provenance | Evidence | Hand-off |
| --- | --- | --- | --- | --- | --- |
| routes.accounts | 9 account routes mounted from accounts.urls | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #30; #23 |
| routes.compatibility-api | 30 compatibility API routes mounted from api.urls | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| routes.studio-courses | 26 Studio Courses routes mounted from studio_courses.urls | side-effect | target-overlay; #116 | [behavior inventory](../adoption/course-platform/behavior-inventory.md); [integration patches](../adoption/course-platform/integration-patches.md) | #14; #16; #23 |
| routes.public-courses | 50 public course routes mounted from courses.urls | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #14; #15; #16; #23 |
| export.api-course-criteria | Course criteria YAML export | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| export.api-leaderboard | Leaderboard YAML export | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| export.api-homework-submissions | Homework submissions export | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| export.api-project-submissions | Project submissions export | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| export.api-graduates | Course graduates export | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| export.api-certificates | Certificate update/export boundary | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| export.public-calendar-ics | Public course calendar.ics export | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #16; #23 |
| compat.cadmin-legacy | Legacy /cadmin compatibility redirects | side-effect | target-overlay; #116 | [target shims](../adoption/course-platform/target-owned-compatibility-shims.tsv) | #16; #23 |
| compat.namespaced-admin-api | Namespaced /api/v1/admin compatibility API | side-effect | target-overlay; #100 | [target shims](../adoption/course-platform/target-owned-compatibility-shims.tsv) | #16; #23 |
| compat.datamailer-callback | /api/datamailer/events callback history boundary | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md); [verification](../adoption/course-platform/verification.md) | #21; #22; #23 |
| compat.datamailer-send-audits | /api/datamailer/send-audits history/export boundary | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md); [verification](../adoption/course-platform/verification.md) | #21; #22; #23 |
| commands.pinned-cmp | 13 pinned CMP management commands | side-effect | pinned-cmp; #30 | [behavior inventory](../adoption/course-platform/behavior-inventory.md) | #21; #22; #23 |
| commands.target-owned | 5 target-owned commands in the 22-command current registry | side-effect | target-overlay; #100; #107; #128 | [behavior inventory](../adoption/course-platform/behavior-inventory.md); [integration patches](../adoption/course-platform/integration-patches.md) | #15; #21; #22; #23 |

The route and command numbers are observations, not approval to redirect, import, send, or expose data. Authenticated API consumers and legacy-host behavior remain unresolved under #16; Datamailer remains migration/history-only under #21 and #22; privacy and retention remain unresolved under #23.

## Unresolved policy hand-offs

| Issue | State | Unresolved contract / hand-off | Evidence |
| --- | --- | --- | --- |
| [#14](https://github.com/DataTalksClub/website/issues/14) | OPEN | Confirm cohort-owned curriculum and duplication isolation before introducing the reusable Course-to-Cohort relationship in phase 2. | [verification](../adoption/course-platform/verification.md) |
| [#15](https://github.com/DataTalksClub/website/issues/15) | OPEN | Approve an explicit legacy edition-to-family/cohort mapping; regex/year stripping is not migration authority. | [verification](../adoption/course-platform/verification.md) |
| [#16](https://github.com/DataTalksClub/website/issues/16) | OPEN | Inventory authenticated/browser/script/certificate/calendar/email/API consumers before any legacy-host redirect or API migration. | [behavior inventory](../adoption/course-platform/behavior-inventory.md) |
| [#21](https://github.com/DataTalksClub/website/issues/21) | OPEN | Reconcile Relay ownership and Datamailer migration-only status; no new direct Datamailer/SES sends are authorized. | [verification](../adoption/course-platform/verification.md) |
| [#22](https://github.com/DataTalksClub/website/issues/22) | OPEN | Approve each transactional purpose, audience, owner, template/version, idempotency input, sender, and retention class. | [verification](../adoption/course-platform/verification.md) |
| [#23](https://github.com/DataTalksClub/website/issues/23) | OPEN | Approve privacy ownership, retention, minors, public-profile, deletion/anonymization, and processor propagation policy. | [verification](../adoption/course-platform/verification.md) |

These rows preserve unresolved decisions and implementation hand-offs; they do not infer owner approval or unblock #72, #73, or #77.

## Related evidence

Closed evidence inputs [#30](https://github.com/DataTalksClub/website/issues/30), [#34](https://github.com/DataTalksClub/website/issues/34), and [#150](https://github.com/DataTalksClub/website/issues/150) are linked rather than copied. #150 is the incremental Milestone-0 readiness matrix; it does not replace this schema, relationship, or export inventory and is not recaptured here.

## Validation

Run `uv run --frozen python scripts/validate_course_platform_schema_inventory.py`. It fails closed on the phase-1 snapshot/source-pin binding, 32-model coverage, Cohort-backed relationships, the Cohort.outcome assertion, pinned-versus-overlay provenance, migration counts, 44 relationship edges, 17 boundaries, controlled vocabularies, duplicate/unknown rows, evidence-path links, and all six unresolved hand-offs. The focused tests mutate those contracts, including SHA, duplicate/unknown model, migration count, vocabulary, evidence, and hand-off drift.

This remains a static phase-1 input. It contains no production data or private export, makes no source-pin update, and does not implement the reusable Course entity, phase-2 URLs, redirects, privacy, email, or API cutover.
