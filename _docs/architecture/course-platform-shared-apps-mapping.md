# Course platform mapping to the shared apps

Issue: #414 (plan item `D5.1`, step 1 — the mapping document).

Status of steps 2–4: the owner has waived the `C5.3` dependency for work on the
pinned v0.4.6 release, but the pinned release imposes a hard ordering the waiver
cannot lift — **the two shared course apps cannot be installed on this site until
the D4.1 events cutover (#412) moves the `events` label to the package app**:

- `community_base.curriculum`'s initial migration depends on
  `('events', '0003_provisional_integration_attempt')`, a node only the package
  events chain has.
- `curriculum.Course.instructors` targets `events.Host`, and the site's own
  events app has no `Host` model, so the model registry cannot resolve while the
  site owns the label.

Steps 2–4 therefore execute on this branch immediately after #412 merges, in the
merge order already planned (412 first, then main into 414). The decision record
below is the part of `D5.1` that can land before that; it records the
proposed-default decisions the P6 migration will implement, each open to owner
veto.

Sources read for this mapping: site `courses/models/` at website 65b852dd; package
`community_base/curriculum/models.py` and `community_base/coursework/models.py` at
community-base 2f37924 (after the `C5.1e` ownership rework). This branch pins release
v0.4.6 (`449e315c`), whose `community_base/` tree is identical to that commit, so every
package-side claim below holds against the pinned release.

Verdicts: **maps** (the row migrates into the named package model), **stays** (remains
site-owned), **gap** (no package home; a package follow-up or an owner decision is
required before the P6 data migration is written).

## Naming traps

- `Homework.course`, `Project.course` and `Enrollment.course` are legacy-named foreign
  keys to `courses.Cohort`, not to `courses.Course`
  (`courses/models/homework.py:43`, `courses/models/project.py:51`,
  `courses/models/cohort.py:665`). The package names the same target `cohort`.
- `RegistrationCampaign.current_course` is likewise a `Cohort` foreign key; the package
  calls it `current_cohort`.
- `Cohort.students` is a `through="Enrollment"` many-to-many
  (`courses/models/cohort.py:339`), not an independent store; the package expresses the
  same rows as `Cohort.enrollments`.

## Curriculum (`cb_curriculum`)

| Site model | Verdict | Package model | Notes |
|---|---|---|---|
| `Course` | maps | `curriculum.Course` | field deltas below |
| `Cohort` | maps | `curriculum.Cohort` | field deltas below |
| `Module` | maps | `curriculum.Module` | ownership moves cohort → course; `terminal_homework` and `link` are gaps |
| `Unit` | maps | `curriculum.Unit` | `link` and `code_sources` are gaps |
| `UnitReadState` | maps | `curriculum.UnitProgress` | 1:1 rename; `read_at` → `completed_at` |
| `CurriculumFlowItem` | gap | `curriculum.CohortModule` (half) | the package places modules only; flow entries that place a project have no home |
| certificates (fields on `Enrollment`) | maps | `curriculum.Certificate` | see "Certificates" below |

### Course field deltas

Site `courses.Course` (`courses/models/cohort.py:54`), package
`community_base/curriculum/models.py:78`.

| Site field | Package field | Verdict |
|---|---|---|
| `slug` | `slug` | maps |
| `title` | `title` | maps |
| `description` | `description` (+ package-rendered `description_html`) | maps |
| `starting_point`, `prerequisites`, `weekly_commitment`, `progression`, `outcome` | — | stays (site-owned long-form content; no package columns) |
| `github_repo_url` | `github_repo_url` | maps |
| `docs_url` | `docs_url` | maps |
| `faq_document_url` | `faq_url` | maps (rename) |
| `social_media_hashtag` | `hashtag` | maps (rename) |
| `visible` | `visible` | maps |
| `source_stable_id` | — | gap: the package `SourceProvenanceMixin` has no stable-id column, and its `source_content_id` is a `UUIDField` where the site's is `CharField(255)` — the id conversion rule needs a decision |
| — | `cover_image_url`, `auto_banner_url`, `custom_banner_url` | package-only; the site's banner generator becomes the producer |
| — | `required_level`, `default_unit_required_level` | package-only access levels; DTC has no tiers, leave defaults |
| — | `status`, `discussion_url`, `tags` | package-only; unused by DTC rows |
| — | `testimonials` (JSON column) | DTC carries testimonial rows (`coursework.Testimonial`), not JSON; keep rows, ignore the column |
| — | `instructors` M2M through `CourseInstructor` → `events.Host` | gap: DTC has no course-scoped people rows — instructor display is assembled site-side from the people app; Host is not that |

### Cohort field deltas

Site `courses.Cohort` (`courses/models/cohort.py:207`), package
`community_base/curriculum/models.py:244`.

| Site field | Package field | Verdict |
|---|---|---|
| `course` FK | `course` FK | maps |
| `identifier` (public route identity) | `slug` | maps — the package slug is the single public identity |
| `slug` (legacy edition slug) | — | stays as a redirect duty during the compatibility window |
| `uuid` | — | stays (retire after migration; identity moves to provenance) |
| `title` | `title` | maps |
| `year` | — | gap (schedule/display metadata the package has no column for) |
| `curriculum_format` | — | deleted upstream: `C5.1e` collapsed the format trio into one shape |
| `delivery_mode` (`live`/`self_paced`) | `mode` (`cohort`/`self_paced`) | maps with a value rename (`live` → `cohort`) |
| `curriculum_source`, `archive_notice_path`, `archive_url`, `archive_commit_sha` | — | stays (site archive provenance) |
| `description`, `outcome`, `promo_summary`, `delivery_format` | — | gap (cohort-level overrides the site's Target model promises and live rows carry) |
| `start_date`, `end_date` | `start_date`, `end_date` | maps |
| `registration_url` | `registration_url` | maps |
| `github_repo_url` | — | gap (cohort-level repo link) |
| `students` M2M | `enrollments` | maps (through `Enrollment`) |
| `social_media_hashtag` | `hashtag` | maps |
| `first_homework_scored`, `finished`, `min_projects_to_pass`, `homework_problems_comments_field`, `project_passing_score`, `visible` | same names | maps |
| — | `max_participants` | package-only; unused by DTC, stays null |

### Module / Unit / flow

- Site `Module` is cohort-owned (`courses/models/curriculum.py:12`); the package module
  is course-owned with optional `CohortModule` placement — the same convergence the
  site already made for the shared graph. Site `Module.terminal_homework` (OneToOne to
  `courses.Homework`) and `Module.link` have **no package column**: the package module
  carries `overview`/`overview_html` text, not bindings. Gap.
- Site `Unit` content maps as `content_markdown` → `body`, `rendered_html` →
  `body_html`, `video_url` → `video_url`, `position` → `sort_order`. Site `Unit.link`
  (external-link units) and `Unit.code_sources` (declared code sources) have **no
  package column**. Gap. Package-only unit columns (`kind`, `session_position`,
  `is_bonus`, `is_preview`, `required_level`, `available_after_days`, `timestamps`,
  `content_hash`, unit-level `homework` text) stay at defaults for migrated DTC rows.
- `UnitReadState` → `UnitProgress` is a 1:1 rename (`read_at` → `completed_at`).
- `CurriculumFlowItem` places one module **or one project** at a cohort position
  (`courses/models/curriculum.py:158`). The package equivalent is `CohortModule`
  (module placements only); project entries in the flow have no package home. Gap.

## Shared current-curriculum family

The site's `Shared*` family (`courses/models/shared_curriculum.py`) was deliberately
not lifted into the package (`C5.1e` adoption note); the package's course-owned module
tree plus `CurriculumImportRun` is its answer to "one current graph per course".

| Site model | Verdict | Package home |
|---|---|---|
| `SharedCurriculum` (one current graph per course) | maps (concept) | course-owned `Module` tree; "current" = latest successful `CurriculumImportRun` |
| `SharedModule` | maps | `curriculum.Module` |
| `SharedLesson` | maps | `curriculum.Unit` (`link`/`code_sources` gaps as above) |
| `CohortSharedModule` (placement + terminal-homework binding) | maps (half) | `curriculum.CohortModule`; the terminal-homework binding is a gap |
| `SharedLessonReadState` | maps | `curriculum.UnitProgress` |
| `SharedCurriculumAsset` | gap | no package asset model exists (also the docs-asset follow-up from #384) |
| `CurriculumRouteAlias` | stays | no package alias model; keep site-side through the compatibility window, then retire like the `cadmin` shim |

The bespoke site ingestion pipeline (`content_sync/course_repository_*.py`) retires in
favour of the package's `curriculum` import (parser `parsers_dtc.py`), per the
convergence analysis.

## Coursework (`cb_coursework`)

Ported from this site under `C5.2a`–`C5.2e`; every family is field-for-field identical
apart from the FK renames listed. All verdicts **maps**.

| Site model | Package model | Delta |
|---|---|---|
| `Homework`, `Question`, `Submission`, `Answer`, `HomeworkStatistics` | same names | `Homework.course` → `cohort` |
| `Project`, `ProjectSubmission`, `ProjectVote`, `ReviewCriteria`, `ProjectCriteriaAssignment`, `PeerReview`, `CriteriaResponse`, `ProjectEvaluationScore`, `ProjectStatistics` | same names | `Project.course` → `cohort` |
| `LeaderboardComplaint` | same | identical |
| `RegistrationCampaign` | same | `current_course` → `current_cohort` |
| `CourseRegistration` | same | `course` → `cohort` |
| `Testimonial` | same | identical |
| `WrappedStatistics`, `UserWrappedStatistics` | same | identical |
| `StatRow`/`StatSection` (`courses/models/stat_display.py`, dataclasses) | `coursework/stat_display.py` | helpers, not models; already ported |

`Question.answer_envelope` maps fully: the package's `coursework/answer_crypto.py` is
the authenticated-encryption boundary with an injected keyring, so only key material
and its loading stay site-side.

## Certificates

The site has no `Certificate` model; certificates are `Enrollment.certificate_name` and
`Enrollment.certificate_url` (`courses/models/cohort.py:679`, `:689`), rendered by the
site-only banner generator. The package model is `curriculum.Certificate` (UUID id,
OneToOne to `Enrollment`, `url`, `issued_at`, `hash`). The P6 migration promotes every
enrollment with a non-empty `certificate_url` to a `Certificate` row; urls are
preserved byte-for-byte (issue step 2), and the banner generator keeps rendering
site-side and remains the writer of `url`.

## Learner profile

`LearnerProfile` stays, per the issue. No such model exists site-side today: learner
profile fields live on `CustomUser` (`role`, `certificate_name`, `country`,
`identity_state`, …) and on `Enrollment` (`display_name`, display flags). The package's
`accounts.MemberProfile` is a phase-3 accounts adoption question, not a `D5.1` one.

## Stays site-side

`CohortBuildItem` (featured-cohort "what you'll build" rows), the CMP / Mailchimp /
Datamailer legacy imports, the `cadmin` redirect shim, people profiles, the banner
generator, cohort archive provenance, `CurriculumRouteAlias`, and the
identity-migration tables. None of these have package homes and none should.

## Package gaps blocking P6

Each needs a community-base follow-up (or an explicit owner decision) before the data
migration is written:

1. Terminal-homework binding: where a module's terminal `coursework.Homework` attaches
   (site `Module.terminal_homework`, shared placement binding).
2. Project flow placement: package has module placements only; the site interleaves
   projects between modules.
3. `Module.link`, `Unit.link`, `Unit.code_sources` columns.
4. Cohort-level overrides: `description`, `outcome`, `promo_summary`,
   `delivery_format`, `github_repo_url`, `year`.
5. `SharedCurriculumAsset` / docs-asset records equivalent.
6. Provenance id conversion: site `source_content_id` `CharField(255)` + separate
   `source_stable_id` vs package `UUIDField` and no stable-id column.
7. Course people: `CourseInstructor` targets `events.Host`; DTC course people come from
   the site people app.
8. Trivia but blocking: `mode` value rename (`live` → `cohort`) must be in the migration
   mapping, not left to a datafix.

## D5.1 decision record (proposed defaults, open to owner veto)

The owner waived the `C5.3` gate for work on the v0.4.6 pin. Each item below is
the proposed default the executable steps will implement; any of them can be
vetoed before the D5.2 freeze weekend without rework beyond the named scope.

1. **Install ordering (new, structural).** `community_base.curriculum` and
   `community_base.coursework` enter `INSTALLED_APPS` only after #412's events
   label swap lands, for the two hard reasons in the head note. The merge order
   is 412, then main into 414, then the install + P6 import commit here.
2. **Terminal-homework binding (gap 1).** Not migrated; the binding stays on
   the site rows. Counted per placement in the import report.
3. **Project flow placements (gap 2).** `CurriculumFlowItem` rows that place a
   project are skipped and counted; module placements map to `CohortModule`.
4. **Link and code-source content (gap 3).** `Module.link`, `Unit.link`,
   `Unit.code_sources` have no package columns and are skipped and counted.
5. **Cohort-level overrides (gap 4).** `description`, `outcome`,
   `promo_summary`, `delivery_format`, `github_repo_url`, `year` stay
   site-side; the site remains their renderer until D5.2 decides their fate.
6. **Shared curriculum assets (gap 5).** No package home; they stay site-side
   with the docs-asset follow-up.
7. **Provenance id conversion (gap 6).** A site `source_content_id` present as
   a complete provenance set converts to
   `uuid5(namespace, source_content_id)`; site rows without a complete set
   migrate with all-None provenance (the package validates the set as
   all-or-nothing). The namespace is fixed in the import module.
8. **Course people (gap 7).** `CourseInstructor`/`events.Host` rows are not
   migrated; instructor display stays assembled site-side.
9. **Mode rename (gap 8).** `delivery_mode` `live` → package `mode` `cohort`
   lives in the import mapping, not a datafix.
10. **Course status.** Migrated families get `status="published"` when the
    site `visible` flag is set, `"draft"` otherwise; the package catalogue
    filters on published.
11. **Unpublished and retired shared rows.** `SharedModule` rows with
    `published=False` and `SharedLesson` rows that are unpublished or
    `retired_at`-stamped are skipped and counted; count equality is asserted
    on the migratable subset.
12. **Bespoke graph precondition.** The P6 import maps only the shared
    current-curriculum graph; a course whose teaching material still sits in
    cohort-owned `Module`/`Unit` rows fails the run with a bounded report
    naming the course and the instruction to run the site's own
    `migrate_shared_curriculum` backfill first. P6 does not reimplement the
    backfill.
13. **Derived rows are copied, not recomputed.** Statistics and leaderboard
    positions were produced by the same calculators the package ported; the
    import copies them verbatim and asserts counts. Recomputation stays
    available as a post-import datafix, not part of P6.
14. **Route re-pointing (issue step 3) is freeze-weekend work.** The package
    learner views render generic donor-parity templates; re-pointing the
    learner HTML surface without re-homing the site's templates would regress
    the visually accepted family, module and documentation pages. D5.1 lands
    the decision and the mount pattern (package urlconfs under the site's
    `/courses/` prefix, site paths and URL names preserved, per the package
    coursework urlconf's own instructions); the flip itself is D5.2. The
    checked-in `course-route-contracts.json` is archival evidence validated
    for internal consistency, not against the live urlconf, so the flip is
    also where its route rows get re-recorded.
15. **App-shell deletion (issue step 4) deletes nothing yet.** After the
    re-point decision above, none of `courses`, `studio_courses`,
    `course_management`, `cadmin`, `review_import`, `compatibility` is empty:
    models serve until the D5.2 cutover, and `course_management` middleware
    and the `cadmin` redirect shim stay mounted. Deletion is a D5.2 follow-up
    list, one app per commit.

## Verification hooks (steps 2–4, executed after the #412 merge)

- `_docs/compatibility/course-route-contracts.json` test keeps passing;
  `cadmin` legacy redirects re-pointed during the D5.2 flip.
- Row counts equal between site tables and package tables after the P6
  rehearsal on the development copy (migratable subset, per decision 11).
- `uv run pytest -q` passes.

## Mount pattern for the route flip (D5.2)

The site has two proven package-mount idioms; the flip picks one per
surface.

1. **Wholesale urlconf include** — a package urlconf mounted under a site
   prefix, ahead of the site shell. Precedent: `studio/mail/` and
   `studio/content-sync/` in `website/urls.py` (D1.2a, D2.2a). Used only
   where the package owns the surface outright. The package learner
   urlconfs are *not* mounted this way: their paths carry trailing slashes
   and the generic `<cohort_slug>` shape, so including them would change
   public URLs — the D4.1 events decision (keep DTC public routes, do not
   mount package public urls) applies to courses too.
2. **Import re-point** — `courses/urls.py` keeps every path and URL name
   (the slash-less shapes, the `cohort_*`/family names, and the route-group
   sorter at the bottom of the file are load-bearing, and the
   `course-route-contracts.json` rows get re-recorded at the flip per
   decision 14); the view imports switch to the package. This is the
   coursework urlconf's own contract: "the concrete paths stay with each
   site's urlconf".

Route categories the flip works through:

- Re-point candidates (package coursework parity views exist): homework +
  stats + submissions; project + eval/add/delete/submit + results + stats +
  submissions; leaderboard + score breakdown + complaint; enrollment +
  toggle. The site's fragmented view modules carry context builders the
  package consolidated, so each re-point compares rendered output, not view
  signatures.
- Stays site-side: the project galleries (`site_project_gallery` — the
  auto-submit filters and table rendering postdate the package
  extraction), the dashboard and its context builders, the two-segment
  `cohort` dispatch into the shared-course views, shared module/lesson
  routes and assets, the calendar `.ics`, registration campaigns, and the
  `course_aliases` legacy edition shims.
- Package views with no current site surface: `coursework_certificate` —
  `courses/urls.py` has no certificate route (certificates render via the
  banner-generator landing include), so the flip decides whether to mount
  it or leave it unmounted. The package `wrapped` view likewise meets a
  bespoke site surface; parity is decided per view during the flip.
- `cadmin/legacy_urls` redirects re-point at whatever view each flip
  installs; their route names must keep resolving.

## Mount pattern for the route flip (D5.2)

The site has two proven package-mount idioms; the flip picks one per
surface.

1. **Wholesale urlconf include** — a package urlconf mounted under a site
   prefix, ahead of the site shell. Precedent: `studio/mail/` and
   `studio/content-sync/` in `website/urls.py` (D1.2a, D2.2a). Used only
   where the package owns the surface outright. The package learner
   urlconfs are *not* mounted this way: their paths carry trailing slashes
   and the generic `<cohort_slug>` shape, so including them would change
   public URLs — the D4.1 events decision (keep DTC public routes, do not
   mount package public urls) applies to courses too.
2. **Import re-point** — `courses/urls.py` keeps every path and URL name
   (the slash-less shapes, the `cohort_*`/family names, and the route-group
   sorter at the bottom of the file are load-bearing, and the
   `course-route-contracts.json` rows get re-recorded at the flip per
   decision 14); the view imports switch to the package. This is the
   coursework urlconf's own contract: "the concrete paths stay with each
   site's urlconf".

Route categories the flip works through:

- Re-point candidates (package coursework parity views exist): homework +
  stats + submissions; project + eval/add/delete/submit + results + stats +
  submissions; leaderboard + score breakdown + complaint; enrollment +
  toggle. The site's fragmented view modules carry context builders the
  package consolidated, so each re-point compares rendered output, not view
  signatures.
- Stays site-side: the project galleries (`site_project_gallery` — the
  auto-submit filters and table rendering postdate the package
  extraction), the dashboard and its context builders, the two-segment
  `cohort` dispatch into the shared-course views, shared module/lesson
  routes and assets, the calendar `.ics`, registration campaigns, and the
  `course_aliases` legacy edition shims.
- Package views with no current site surface: `coursework_certificate` —
  `courses/urls.py` has no certificate route (certificates render via the
  banner-generator landing include), so the flip decides whether to mount
  it or leave it unmounted. The package `wrapped` view likewise meets a
  bespoke site surface; parity is decided per view during the flip.
- `cadmin/legacy_urls` redirects re-point at whatever view each flip
  installs; their route names must keep resolving.
