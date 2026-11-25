# Email templates

This directory is the source of truth for the DTC transactional mail
purposes. Each file is one purpose: a frontmatter header (`name`, `subject`,
optional `footer_note`) and a markdown body. The file name minus `.md` is the
template key Relay knows; keys match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`.

Bodies and subjects are Django templates. Every template receives
`user_name`, `user_email`, `site_url` and `site_name` from the mail package,
plus the purpose-specific context declared per template below. The deploy's
`import_mail_templates` step derives `required_context` from the placeholders
it finds, minus the file's `optional_context` frontmatter list (keys the send
may legitimately leave empty — Relay rejects an empty string for any required
key), upserts the draft into the Relay catalog and publishes a new
version when the draft changed — a deploy with unchanged templates touches
nothing.

These purposes were previously written inside the Datamailer service; this
directory replaced that copy as the thing to edit, and D1.2ca retired the
service itself. Relay mirrors produced by
the deploy step are never edited by hand.

| Key | Purpose | Extra context |
|---|---|---|
| `application-pending-operator` | Operator notice for a new pending application (approval campaigns only) | `applicant_name`, `applicant_email`, `campaign_title`, `course_title`, `review_url`, `submitted_at` |
| `application-approved` | Applicant accepted after operator review | `applicant_email`, `course_title`, `course_url` |
| `application-rejected` | Applicant not offered a place after operator review | `course_title` |
| `deadline-reminder` | Homework/deadline nudge for an enrolled learner | `course_title`, `course_url`, `deadline` |
| `course-registration-confirmation` | Registration accepted for a course (double opt-in: the learner MUST click the confirmation link) | `course_title`, `course_url`, `confirmation_url` (required); `start_date`, `verify_email_prompt` (optional: empty unless a start date is announced / the registering account's email still needs verifying); `requires_approval` (block-tag-only flag: when true the body adds the "awaiting review, we will email you after review" paragraph instead of implying acceptance) |
| `event-registration-verify` | Confirm an event registration made with the email form (double opt-in: the seat is confirmed only by the link) | `event_title`, `event_url`, `verify_url` |
| `account-verify-email` | Confirm the address of a new email + password account (allauth `email_confirmation[_signup]`; a password login is refused until confirmed) | `verify_url` |
| `account-password-reset` | Forgot-password link (allauth `password_reset_key`); also how an account without a password sets its first one | `reset_url` |
| `account-already-exists` | Sent instead of creating a second account when someone signs up with an address that already has one (no enumeration on the page) | `login_url`, `reset_url` |
| `account-password-changed` | Notice after a password is set, changed or reset (allauth `password_set` / `password_changed` / `password_reset`) | `password_action` (`set`, `changed` or `reset`), `reset_url` |
| `enrollment-confirmation` | Enrollment confirmed in a cohort | `course_title`, `course_url`, `cohort_name` |
| `certificate-ready` | Completion certificate is available | `course_title`, `certificate_url` |
| `slack-access` | Invitation to the course Slack community | `course_title`, `slack_url` |
| `homework-submission-confirmation` | Homework submission saved | `homework_title`, `intro_text`, `update_text`, `update_link_text`, `update_url`, `submitted_fields_text`, `submitted_answers_text` |
| `project-submission-confirmation` | Project submission saved | `project_title`, `intro_text`, `update_text`, `update_link_text`, `update_url`, `submitted_fields_text` |
| `homework-score-notification` | Homework score published | `course_title`, `homework_title`, `total_score`, `questions_score`, `learning_in_public_score`, `faq_score`, `scores_url`, `leaderboard_url`, `course_url` |
| `project-score-notification` | Project score published | `course_title`, `project_title`, `total_score`, the five score components, `github_link`, `commit_id`, `scores_url`, `project_url`, `leaderboard_url`, `course_url` |
| `peer-review-assignment` | Peer review opened for a submitted project | `project_title`, `intro_text`, `submitted_at`, `deadline_summary`, `number_of_peers_to_evaluate`, `assigned_reviews` (list of `review_id`, `eval_url`, `submission_github_link`), `evaluations_url` |
