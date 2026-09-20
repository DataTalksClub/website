# Email templates

This directory is the source of truth for the DTC transactional mail
purposes. Each file is one purpose: a frontmatter header (`name`, `subject`,
optional `footer_note`) and a markdown body. The file name minus `.md` is the
template key Relay knows; keys match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`.

Bodies and subjects are Django templates. Every template receives
`user_name`, `user_email`, `site_url` and `site_name` from the mail package,
plus the purpose-specific context declared per template below. The deploy's
`import_mail_templates` step derives `required_context` from the placeholders
it finds, upserts the draft into the Relay catalog and publishes a new
version when the draft changed — a deploy with unchanged templates touches
nothing.

These purposes were previously written inside the Datamailer service; this
directory replaced that copy as the thing to edit, and D1.2ca retired the
service itself. Relay mirrors produced by
the deploy step are never edited by hand.

| Key | Purpose | Extra context |
|---|---|---|
| `deadline-reminder` | Homework/deadline nudge for an enrolled learner | `course_title`, `course_url`, `deadline` |
| `course-registration-confirmation` | Registration accepted for a course | `course_title`, `course_url`, `start_date`, `verify_email_prompt` (empty unless the registering account's email still needs verifying) |
| `enrollment-confirmation` | Enrollment confirmed in a cohort | `course_title`, `course_url`, `cohort_name` |
| `certificate-ready` | Completion certificate is available | `course_title`, `certificate_url` |
| `slack-access` | Invitation to the course Slack community | `course_title`, `slack_url` |
| `homework-submission-confirmation` | Homework submission saved | `homework_title`, `intro_text`, `update_text`, `update_link_text`, `update_url`, `submitted_fields_text`, `submitted_answers_text` |
| `project-submission-confirmation` | Project submission saved | `project_title`, `intro_text`, `update_text`, `update_link_text`, `update_url`, `submitted_fields_text` |
| `homework-score-notification` | Homework score published | `course_title`, `homework_title`, `total_score`, `questions_score`, `learning_in_public_score`, `faq_score`, `scores_url`, `leaderboard_url`, `course_url` |
| `project-score-notification` | Project score published | `course_title`, `project_title`, `total_score`, the five score components, `github_link`, `commit_id`, `scores_url`, `project_url`, `leaderboard_url`, `course_url` |
| `peer-review-assignment` | Peer review opened for a submitted project | `project_title`, `intro_text`, `submitted_at`, `deadline_summary`, `number_of_peers_to_evaluate`, `assigned_reviews` (list of `review_id`, `eval_url`, `submission_github_link`), `evaluations_url` |
