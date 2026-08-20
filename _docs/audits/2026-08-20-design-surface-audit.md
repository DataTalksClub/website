# Design surface audit — 2026-08-20

This audit records the shared-site visual rules requested during the course
surface rebuild. It covers page grounds, widths, form primitives, and page-level
eyebrows/overlines.

## Rules

- The shared design system owns page grounds, reading width, controls, buttons,
  callouts, and shell spacing.
- Lavender is the default content ground for the course/task surfaces. Cream
  remains a deliberate hero or entrance surface; sand/mint/ink remain local
  semantic surfaces where their meaning is clear.
- New pages use the shared `.shell`/`.shell-reading` contract rather than a
  page-specific max width. Home and sign-in/sign-up are the only intentional
  page-family exceptions at present.
- An eyebrow is optional context, never a second title. Remove it when it
  repeats the breadcrumb, page title, course/cohort name, or year. Useful
  metadata such as podcast season/episode, event type, profile, legal/status,
  and unique catalogue counts may remain.
- Editable controls, callouts, and buttons use shared primitives and tokens;
  page-local copies should be removed or reduced to layout-only rules.

## Redundant eyebrows to remove

These page-level labels repeat the breadcrumb or heading:

- Course/task surfaces: `courses/templates/courses/dashboard.html`,
  `enrollment.html`, `leaderboard.html`, `leaderboard_score_breakdown.html`,
  `leaderboard_complaint.html`, `courses/templates/homework/stats.html`,
  `courses/templates/homework/submissions.html`, and the project pages under
  `courses/templates/projects/` (`stats`, `list_all`, `eval`, `list`,
  `submissions`, `results`).
- Review/public detail surfaces: `templates/review/course_cohort.html`,
  `templates/review/faq_detail.html`,
  `templates/review/registration_preview.html`, and
  `templates/public/wiki_detail.html`.
- Studio/Course Studio pages: `studio_courses/templates/studio_courses/course_list.html`,
  `course_admin.html`, `datamailer_operations.html`, `campaign_form.html`,
  `enrollment_edit.html`, `homework_submissions.html`,
  `homework_submission_edit.html`, `project_submissions.html`,
  `project_submission_edit.html`, and `templates/studio/event_identity_detail.html`.
- Generic account labels on password reset, inactive, closed-signup, logout,
  social-login completion, and connection pages should be removed unless they
  communicate security or another non-redundant state.
- The `registration open` card marker in `courses/templates/courses/course_list.html`
  repeats the surrounding `Open registration` section heading.

Keep meaningful context: collection totals, wiki/FAQ/docs counts, podcast
season, `book of the week`, profile/legal/status labels, diagnostic 400/404
labels, event type/state, result document type, and genuine Studio safety or
operational-domain labels.

## Ground, width, and primitive deviations

- Later cream bands or missing lavender page grounds: account settings,
  connections, enrollment, leaderboard/complaint, homework submissions, the
  shared submission shell, general project pages, and the Studio base.
- Bespoke width overrides: docs home/detail, registration preview, course
  registration, account settings, leaderboard complaint, non-entrance auth
  outcomes, Studio base, and repeated 46rem limits inside the shared
  submission shell.
- Duplicate `.form-control` adapters exist in registration, enrollment,
  leaderboard complaint, account settings, homework, projects, and Studio.
  The shared submission shell should provide the common control treatment.
- Wrapped pages contain unsanctioned literal colors; leaderboard score
  breakdown contains a page-local dialog backdrop token.
- Existing `.kicker` CSS is currently unused; retain the primitive only if a
  meaningful future context needs it.

## Audit provenance

The source audit was performed against the rendered user-facing templates in
this worktree. Generated snapshots, mockups, vendor files, and test-only
fixtures were excluded. The next implementation batches should preserve form
field names, CSRF, permissions, and semantic status/callout roles while
removing only presentation duplication.
