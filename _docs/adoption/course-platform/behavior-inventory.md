# Adopted course-platform behavior inventory

This file is generated from the pinned URLconfs, Django app registry, migration files,
and management-command registry by `scripts/render_course_platform_inventory.py`.
`core.tests.test_course_platform_adoption` smoke-resolves every listed route through the
unified root URLconf, loads every listed command, checks the original app/migration
identities, and verifies all copied destination checksums.

## Surface summary

| Surface | Mounted URLconf | Routes |
| --- | --- | ---: |
| Accounts | `accounts.urls` | 9 |
| Compatibility API | `api.urls` | 29 |
| Studio Courses | `cadmin.urls` | 26 |
| Public courses | `courses.urls` | 25 |
| **Total** |  | **89** |

The compatibility API and Studio Courses rows below retain the complete adopted
behavior; issue #115 changes the management adapter names and mount, not its logic.

## Routes

### Accounts

Mounted from `accounts.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/accounts/settings/` | `account_settings` | `accounts.views.account_settings.account_settings` |
| `/accounts/login/` | `login` | `accounts.views.login.social_login_view` |
| `/accounts/email/` | — | `accounts.views.disabled.disabled` |
| `/accounts/password/reset/` | — | `accounts.views.disabled.disabled` |
| `/accounts/toggle-dark-mode/` | `toggle_dark_mode` | `accounts.views.account_toggles.toggle_dark_mode` |
| `/accounts/settings/toggle/` | `update_account_toggle` | `accounts.views.account_toggles.update_account_toggle` |
| `/accounts/settings/email-preferences/` | `account_email_preferences` | `accounts.views.email_preferences.account_email_preferences` |
| `/accounts/settings/timezone/` | `update_timezone_preference` | `accounts.views.timezone.update_timezone_preference` |
| `/accounts/stop-impersonating/` | `stop_impersonating` | `accounts.views.impersonation.stop_impersonating` |

### Compatibility API

Mounted from `api.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/api/openapi.json` | `api_openapi_json` | `api.openapi.spec.openapi_json_view` |
| `/api/health/` | `api_health` | `api.views.health.health_view` |
| `/api/courses/<slug:course_slug>/course-criteria.yaml` | `api_course_criteria_yaml` | `api.views.course_exports.course_criteria_yaml_view` |
| `/api/courses/<slug:course_slug>/leaderboard.yaml` | `api_course_leaderboard` | `api.views.leaderboard_exports.leaderboard_data_view` |
| `/api/courses/<slug:course_slug>/homeworks/<slug:homework_slug>/submissions` | `api_homework_submissions_export` | `api.views.homework_exports.homework_data_view` |
| `/api/courses/<slug:course_slug>/projects/<slug:project_slug>/submissions` | `api_project_submissions_export` | `api.views.project_exports.project_data_view` |
| `/api/courses/<slug:course_slug>/graduates` | `api_course_graduates` | `api.views.enrollment_graduates.graduates_data_view` |
| `/api/courses/<slug:course_slug>/certificates` | `api_course_certificates` | `api.views.enrollment_certificates.bulk_update_enrollment_certificates_view` |
| `/api/datamailer/events` | `api_datamailer_events` | `api.views.webhooks.datamailer_event_webhook` |
| `/api/datamailer/send-audits` | `api_datamailer_send_audits` | `api.views.datamailer_send_audits.datamailer_send_audits_view` |
| `/api/courses/` | `api_courses_list` | `api.views.courses.courses_list_view` |
| `/api/courses/<slug:course_slug>/` | `api_course_detail` | `api.views.courses.course_detail_view` |
| `/api/registration-campaigns/` | `api_registration_campaigns` | `api.views.registration_campaigns.registration_campaigns_view` |
| `/api/registration-campaigns/<slug:campaign_slug>/` | `api_registration_campaign_detail` | `api.views.registration_campaigns.registration_campaign_detail_view` |
| `/api/registration-campaigns/<slug:campaign_slug>/registrations/` | `api_registration_campaign_registrations` | `api.views.registration_campaigns.registration_campaign_registrations_view` |
| `/api/courses/<slug:course_slug>/homeworks/` | `api_homeworks` | `api.views.homeworks.homeworks_view` |
| `/api/courses/<slug:course_slug>/homeworks/<int:homework_id>/` | `api_homework_detail` | `api.views.homeworks.homework_detail_view` |
| `/api/courses/<slug:course_slug>/homeworks/<int:homework_id>/score/` | `api_homework_score` | `api.views.homeworks.homework_score_view` |
| `/api/courses/<slug:course_slug>/homeworks/by-slug/<slug:homework_slug>/` | `api_homework_detail_by_slug` | `api.views.homeworks.homework_detail_by_slug_view` |
| `/api/courses/<slug:course_slug>/homeworks/by-slug/<slug:homework_slug>/score/` | `api_homework_score_by_slug` | `api.views.homeworks.homework_score_by_slug_view` |
| `/api/courses/<slug:course_slug>/projects/` | `api_projects` | `api.views.projects.projects_view` |
| `/api/courses/<slug:course_slug>/projects/<int:project_id>/` | `api_project_detail` | `api.views.projects.project_detail_view` |
| `/api/courses/<slug:course_slug>/projects/<int:project_id>/assign-reviews/` | `api_project_assign_reviews` | `api.views.projects.project_assign_reviews_view` |
| `/api/courses/<slug:course_slug>/projects/<int:project_id>/score/` | `api_project_score` | `api.views.projects.project_score_view` |
| `/api/courses/<slug:course_slug>/projects/by-slug/<slug:project_slug>/` | `api_project_detail_by_slug` | `api.views.projects.project_detail_by_slug_view` |
| `/api/courses/<slug:course_slug>/projects/by-slug/<slug:project_slug>/assign-reviews/` | `api_project_assign_reviews_by_slug` | `api.views.projects.project_assign_reviews_by_slug_view` |
| `/api/courses/<slug:course_slug>/projects/by-slug/<slug:project_slug>/score/` | `api_project_score_by_slug` | `api.views.projects.project_score_by_slug_view` |
| `/api/courses/<slug:course_slug>/homeworks/<int:homework_id>/questions/` | `api_questions` | `api.views.questions.questions_view` |
| `/api/courses/<slug:course_slug>/homeworks/<int:homework_id>/questions/<int:question_id>/` | `api_question_detail` | `api.views.questions.question_detail_view` |

### Studio Courses

Mounted from `cadmin.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/studio/courses/` | `studio_courses_course_list` | `cadmin.views.course_admin.course_list` |
| `/studio/courses/campaigns/new/` | `studio_courses_campaign_create` | `cadmin.views.campaigns.campaign_create` |
| `/studio/courses/campaigns/<slug:campaign_slug>/edit/` | `studio_courses_campaign_edit` | `cadmin.views.campaigns.campaign_edit` |
| `/studio/courses/registrations/<slug:campaign_slug>/` | `studio_courses_campaign_registrations` | `cadmin.views.campaigns.campaign_registrations` |
| `/studio/courses/datamailer/` | `studio_courses_datamailer_operations` | `cadmin.views.datamailer.datamailer_operations` |
| `/studio/courses/datamailer/events/` | `studio_courses_datamailer_events` | `cadmin.views.datamailer.datamailer_events` |
| `/studio/courses/cloudwatch/` | `studio_courses_cloudwatch_dashboard` | `cadmin.views.observability.cloudwatch_dashboard` |
| `/studio/courses/<slug:course_slug>/` | `studio_courses_course` | `cadmin.views.course_admin.course_admin` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/score` | `studio_courses_homework_score` | `cadmin.views.homework.homework_score` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/rescore` | `studio_courses_homework_rescore` | `cadmin.views.homework.homework_rescore` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/extend-deadline` | `studio_courses_homework_extend_deadline` | `cadmin.views.homework.homework_extend_deadline` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/notify-scores` | `studio_courses_homework_notify_scores` | `cadmin.views.homework.homework_notify_scores` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/save-answers` | `studio_courses_homework_save_answers` | `cadmin.views.homework.homework_save_answers` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/set-correct-answers` | `studio_courses_homework_set_correct_answers` | `cadmin.views.homework.homework_set_correct_answers` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/clear-correct-answers` | `studio_courses_homework_clear_correct_answers` | `cadmin.views.homework.homework_clear_correct_answers` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/submissions` | `studio_courses_homework_submissions` | `cadmin.views.homework.homework_submissions` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/submissions/<int:submission_id>/edit` | `studio_courses_homework_submission_edit` | `cadmin.views.homework.homework_submission_edit` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/assign-reviews` | `studio_courses_project_assign_reviews` | `cadmin.views.projects.project_assign_reviews` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/extend-deadline` | `studio_courses_project_extend_deadline` | `cadmin.views.projects.project_extend_deadline` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/score` | `studio_courses_project_score` | `cadmin.views.projects.project_score` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/submissions` | `studio_courses_project_submissions` | `cadmin.views.projects.project_submissions` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/submissions/<int:submission_id>/edit` | `studio_courses_project_submission_edit` | `cadmin.views.projects.project_submission_edit` |
| `/studio/courses/<slug:course_slug>/enrollments/` | `studio_courses_enrollments` | `cadmin.views.enrollment.enrollments_list` |
| `/studio/courses/<slug:course_slug>/leaderboard-complaints/` | `studio_courses_leaderboard_complaints` | `cadmin.views.enrollment.leaderboard_complaints` |
| `/studio/courses/<slug:course_slug>/leaderboard-complaints/<int:complaint_id>/resolve` | `studio_courses_leaderboard_complaint_resolve` | `cadmin.views.enrollment.leaderboard_complaint_resolve` |
| `/studio/courses/<slug:course_slug>/enrollment/<int:enrollment_id>/edit` | `studio_courses_enrollment_edit` | `cadmin.views.enrollment.enrollment_edit` |

### Public courses

Mounted from `courses.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/` | `course_list` | `courses.views.course_list.course_list` |
| `/register/<slug:campaign_slug>/` | `registration_campaign` | `courses.views.registration.registration_campaign_view` |
| `/wrapped/<int:year>/` | `wrapped` | `courses.views.wrapped.wrapped_view` |
| `/wrapped/<int:year>/<int:student_id>/` | `user_wrapped` | `courses.views.wrapped.user_wrapped_view` |
| `/<slug:course_slug>/calendar.ics` | `course_calendar` | `courses.views.course_calendar.course_calendar_view` |
| `/<slug:course_slug>/` | `course` | `courses.views.course.course_view` |
| `/<slug:course_slug>/projects` | `list_all_project_submissions` | `courses.views.course_project_submissions.list_all_project_submissions_view` |
| `/<slug:course_slug>/leaderboard` | `leaderboard` | `courses.views.course_leaderboard.leaderboard_view` |
| `/<slug:course_slug>/leaderboard/<int:enrollment_id>/` | `leaderboard_score_breakdown` | `courses.views.course_leaderboard.leaderboard_score_breakdown_view` |
| `/<slug:course_slug>/leaderboard/<int:enrollment_id>/report` | `leaderboard_complaint` | `courses.views.course_leaderboard.leaderboard_complaint_view` |
| `/<slug:course_slug>/enrollment/toggle` | `update_enrollment_toggle` | `courses.views.course_enrollment.update_enrollment_toggle` |
| `/<slug:course_slug>/enrollment` | `enrollment` | `courses.views.course_enrollment.enrollment_view` |
| `/<slug:course_slug>/dashboard` | `dashboard` | `courses.views.dashboard.dashboard_view` |
| `/<slug:course_slug>/project/<slug:project_slug>` | `project` | `courses.views.project.project_view` |
| `/<slug:course_slug>/project/<slug:project_slug>/list` | `project_list` | `courses.views.project_submissions.projects_list_view` |
| `/<slug:course_slug>/project/<slug:project_slug>/eval` | `projects_eval` | `courses.views.project_eval.projects_eval_view` |
| `/<slug:course_slug>/project/<slug:project_slug>/results` | `project_results` | `courses.views.project_results.project_results` |
| `/<slug:course_slug>/project/<slug:project_slug>/stats` | `project_statistics` | `courses.views.project_statistics.project_statistics` |
| `/<slug:course_slug>/project/<slug:project_slug>/submissions` | `project_submissions` | `courses.views.project_submissions.project_submissions` |
| `/<slug:course_slug>/project/<slug:project_slug>/eval/<int:review_id>` | `projects_eval_submit` | `courses.views.project_eval_submit.projects_eval_submit` |
| `/<slug:course_slug>/project/<slug:project_slug>/eval/add/<int:submission_id>` | `projects_eval_add` | `courses.views.project_eval_actions.projects_eval_add` |
| `/<slug:course_slug>/project/<slug:project_slug>/eval/delete/<int:review_id>` | `projects_eval_delete` | `courses.views.project_eval_actions.projects_eval_delete` |
| `/<slug:course_slug>/homework/<slug:homework_slug>` | `homework` | `courses.views.homework.homework_view` |
| `/<slug:course_slug>/homework/<slug:homework_slug>/stats` | `homework_statistics` | `courses.views.homework_statistics.homework_statistics` |
| `/<slug:course_slug>/homework/<slug:homework_slug>/submissions` | `homework_submissions` | `courses.views.homework_submissions.homework_submissions` |

## Management commands

| Command | Owning app | Registered help |
| --- | --- | --- |
| `account_identity_inventory` | `accounts` | Emit the redacted single-account inventory as JSON. |
| `audit_datamailer_recipient_lists` | `courses` | Compare CMP recipient-list source data with Datamailer active members. |
| `bootstrap_development_owner` | `accounts` | Interactively bootstrap the single development owner without echoing secrets. |
| `datamailer_callback_status` | `data` | Show Datamailer callback health from stored webhook events. |
| `datamailer_campaign` | `courses` | Create/update and optionally operate a Datamailer campaign. |
| `datamailer_outbox_status` | `data` | Show Datamailer outbox health and recent dispatcher state. |
| `datamailer_send_status` | `data` | Show Datamailer send audit counts recorded by CMP. |
| `datamailer_status` | `courses` | Look up Datamailer contact sendability and recent send history. |
| `monitoring_datamailer_health` | `data` | Emit compact Datamailer health observability events. |
| `preview_peer_review_email` | `courses` | Preview the peer-review-assignment email for a project. |
| `process_datamailer_outbox` | `data` | Dispatch pending/retrying Datamailer outbox events. |
| `reconcile_accounts` | `accounts` | Dry-run, apply, or rollback-check reviewed account mappings. |
| `send_deadline_reminders` | `courses` | Send Datamailer deadline reminders with transient recipient lists. |
| `sync_datamailer_contacts` | `courses` | Backfill Datamailer contacts from CMP users. |
| `sync_datamailer_recipient_lists` | `courses` | Backfill Datamailer recipient lists from CMP registrations, enrollments, and submissions. |
| `upsert_datamailer_templates` | `courses` | Create or update CMP's transactional templates in Datamailer. |

## Preserved app and migration identities

| App label | App module | Original numbered migrations |
| --- | --- | --- |
| `accounts` | `accounts` | `0001_initial, 0002_token, 0003_customuser_certificate_name, 0004_customuser_dark_mode, 0005_backfill_certificate_name_from_enrollment, 0006_customuser_country_customuser_region_and_more, 0007_customuser_email_deadline_reminders_and_more, 0008_customuser_email_course_updates, 0009_customuser_preferred_timezone, 0010_remove_customuser_email_course_updates_and_more, 0011_identity_expansion, 0012_backfill_normalized_identity` |
| `api` | `api` | `none` |
| `cadmin` | `cadmin` | `none` |
| `courses` | `courses` | `0001_initial, 0002_alter_enrollment_student, 0003_replace_commas_with_linebreaks_in_possible_answers, 0004_update_correct_answer_indexes, 0005_update_answers_with_indexes, 0006_course_first_homework_scored, 0007_enrollment_position_on_leaderboard, 0008_remove_answer_student, 0009_rename_comments_peerreview_problems_comments_and_more, 0010_remove_reviewcriteria_max_score, 0011_alter_enrollment_position_on_leaderboard, 0012_project_points_for_peer_review_and_more, 0013_remove_homework_is_scored_homework_state_and_more, 0014_alter_projectsubmission_github_link_and_more, 0015_enrollment_certificate_url, 0016_enrollment_about_me_enrollment_github_url_and_more, 0017_alter_projectsubmission_learning_in_public_links_and_more, 0018_course_finished, 0019_remove_homework_problems_comments_field_and_more, 0020_remove_project_points_to_pass_and_more, 0021_course_min_projects_to_pass, 0022_projectstatistics, 0023_course_visible, 0024_alter_question_question_type, 0025_add_wrapped_statistics, 0026_enrollment_disable_learning_in_public_and_more, 0027_homework_instructions_url_project_instructions_url_and_more, 0028_leaderboardcomplaint, 0029_enrollment_display_public_profile, 0030_remove_enrollment_profile_fields, 0031_merge_instruction_urls_and_profile_fields, 0032_course_end_date_course_registration_url_and_more, 0033_projectsubmission_faq_contribution_url_and_more, 0034_preserve_submission_timestamps, 0035_projectvote, 0036_projectsubmission_volunteer_review_only, 0037_registrationcampaign_courseregistration, 0038_alter_courseregistration_mailchimp_sync_status, 0039_remove_courseregistration_mailchimp_error_and_more, 0040_courseregistration_company_name` |
| `data` | `data` | `0001_initial, 0002_datamaileroutboxevent, 0003_datamaileroutboxdispatchrun, 0004_datamailercontactevent_duplicate_count_and_more, 0005_datamailersendaudit` |

The original numbered graph remains an unchanged prefix. Additive target migrations
extend the adopted identity only through reviewed product issues. Migration squashing
remains deferred until the production-like parity gate in `migration-squash-gate.md`
can be exercised.
