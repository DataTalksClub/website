# Adopted course-platform behavior inventory

This file is generated from the pinned URLconfs, Django app registry, and
management-command registry by `scripts/render_course_platform_inventory.py`.
`core.tests.test_course_platform_adoption` smoke-resolves every listed route through the
unified root URLconf, loads every listed command, checks the original app identities,
and verifies all copied destination checksums.

## Surface summary

| Surface | Mounted URLconf | Routes |
| --- | --- | ---: |
| Accounts | `accounts.urls` | 13 |
| Compatibility API | `api.urls` | 28 |
| Studio Courses | `studio_courses.urls` | 24 |
| Public courses | `courses.urls` | 58 |
| **Total** |  | **123** |

The compatibility API and Studio Courses rows below retain the complete adopted
behavior. Issues #115 and #116 change the management mount, names, and package
mechanically without changing its logic.

## Routes

### Accounts

Mounted from `accounts.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/accounts/settings/` | `account_settings` | `accounts.views.account_settings.account_settings` |
| `/accounts/welcome/` | `account_welcome` | `accounts.views.welcome.welcome` |
| `/accounts/home/dismiss/` | `dismiss_home_item` | `accounts.views.home_dismissals.dismiss_home_item` |
| `/accounts/login/` | `login` | `accounts.views.login.SiteLoginView` |
| `/accounts/signed-in/` | `login_popup_done` | `accounts.views.signed_in.login_popup_done` |
| `/accounts/email/` | — | `accounts.views.disabled.disabled` |
| `/accounts/toggle-dark-mode/` | `toggle_dark_mode` | `accounts.views.account_toggles.toggle_dark_mode` |
| `/accounts/settings/toggle/` | `update_account_toggle` | `accounts.views.account_toggles.update_account_toggle` |
| `/accounts/settings/email-preferences/` | `account_email_preferences` | `accounts.views.email_preferences.account_email_preferences` |
| `/accounts/settings/timezone/` | `update_timezone_preference` | `accounts.views.timezone.update_timezone_preference` |
| `/accounts/settings/sign-in-methods/disconnect/` | `disconnect_social_account` | `accounts.views.social_connections.disconnect_social_account` |
| `/accounts/3rdparty/` | — | `accounts.views.social_connections.social_connections_moved` |
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
| `/api/webhooks/github` | `api_github_course_repository_webhook` | `api.views.course_repository_webhooks.github_course_repository_webhook` |
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

Mounted from `studio_courses.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/studio/courses` | `studio_courses_course_list` | `studio_courses.views.course_admin.course_list` |
| `/studio/courses/campaigns/new/` | `studio_courses_campaign_create` | `studio_courses.views.campaigns.campaign_create` |
| `/studio/courses/campaigns/<slug:campaign_slug>/edit/` | `studio_courses_campaign_edit` | `studio_courses.views.campaigns.campaign_edit` |
| `/studio/courses/registrations/<slug:campaign_slug>/` | `studio_courses_campaign_registrations` | `studio_courses.views.campaigns.campaign_registrations` |
| `/studio/courses/cloudwatch/` | `studio_courses_cloudwatch_dashboard` | `studio_courses.views.observability.cloudwatch_dashboard` |
| `/studio/courses/<slug:course_slug>/` | `studio_courses_course` | `studio_courses.views.course_admin.course_admin` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/score` | `studio_courses_homework_score` | `studio_courses.views.homework.homework_score` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/rescore` | `studio_courses_homework_rescore` | `studio_courses.views.homework.homework_rescore` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/extend-deadline` | `studio_courses_homework_extend_deadline` | `studio_courses.views.homework.homework_extend_deadline` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/notify-scores` | `studio_courses_homework_notify_scores` | `studio_courses.views.homework.homework_notify_scores` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/save-answers` | `studio_courses_homework_save_answers` | `studio_courses.views.homework.homework_save_answers` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/set-correct-answers` | `studio_courses_homework_set_correct_answers` | `studio_courses.views.homework.homework_set_correct_answers` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/clear-correct-answers` | `studio_courses_homework_clear_correct_answers` | `studio_courses.views.homework.homework_clear_correct_answers` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/submissions` | `studio_courses_homework_submissions` | `studio_courses.views.homework.homework_submissions` |
| `/studio/courses/<slug:course_slug>/homework/<slug:homework_slug>/submissions/<int:submission_id>/edit` | `studio_courses_homework_submission_edit` | `studio_courses.views.homework.homework_submission_edit` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/assign-reviews` | `studio_courses_project_assign_reviews` | `studio_courses.views.projects.project_assign_reviews` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/extend-deadline` | `studio_courses_project_extend_deadline` | `studio_courses.views.projects.project_extend_deadline` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/score` | `studio_courses_project_score` | `studio_courses.views.projects.project_score` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/submissions` | `studio_courses_project_submissions` | `studio_courses.views.projects.project_submissions` |
| `/studio/courses/<slug:course_slug>/project/<slug:project_slug>/submissions/<int:submission_id>/edit` | `studio_courses_project_submission_edit` | `studio_courses.views.projects.project_submission_edit` |
| `/studio/courses/<slug:course_slug>/enrollments/` | `studio_courses_enrollments` | `studio_courses.views.enrollment.enrollments_list` |
| `/studio/courses/<slug:course_slug>/leaderboard-complaints/` | `studio_courses_leaderboard_complaints` | `studio_courses.views.enrollment.leaderboard_complaints` |
| `/studio/courses/<slug:course_slug>/leaderboard-complaints/<int:complaint_id>/resolve` | `studio_courses_leaderboard_complaint_resolve` | `studio_courses.views.enrollment.leaderboard_complaint_resolve` |
| `/studio/courses/<slug:course_slug>/enrollment/<int:enrollment_id>/edit` | `studio_courses_enrollment_edit` | `studio_courses.views.enrollment.enrollment_edit` |

### Public courses

Mounted from `courses.urls`.

| Route | Name | Callback |
| --- | --- | --- |
| `/<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>/solution` | `cohort_homework_solution` | `courses.views.homework_solution.homework_solution` |
| `/<slug:course_slug>/<slug:cohort_identifier>/calendar.ics` | `cohort_calendar` | `courses.views.course_calendar.course_calendar_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>` | `cohort_homework` | `courses.views.homework.homework_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/leaderboard` | `cohort_leaderboard` | `courses.views.course_leaderboard.leaderboard_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/dashboard` | `cohort_dashboard` | `courses.views.dashboard.dashboard_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/projects` | `cohort_projects` | `courses.views.site_project_gallery.cohort_projects_redirect` |
| `/<slug:course_slug>/<slug:cohort_identifier>/enrollment` | `cohort_enrollment` | `courses.views.course_enrollment.enrollment_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/modules/<slug:module_slug>` | `cohort_module` | `courses.views.module.module_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/modules/<slug:module_slug>/<slug:unit_slug>` | `cohort_unit` | `courses.views.unit.unit_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/modules/<slug:module_slug>/<slug:unit_slug>/read` | `cohort_unit_read_state` | `courses.views.module.update_unit_read_state` |
| `/<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>/stats` | `cohort_homework_statistics` | `courses.views.homework_statistics.homework_statistics` |
| `/<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>/submissions` | `cohort_homework_submissions` | `courses.views.homework_submissions.homework_submissions` |
| `/<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>/<str:homework_step>` | `cohort_homework_step` | `courses.views.homework.homework_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/leaderboard/<int:enrollment_id>/` | `cohort_leaderboard_score_breakdown` | `courses.views.course_leaderboard.leaderboard_score_breakdown_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/leaderboard/<int:enrollment_id>/report` | `cohort_leaderboard_complaint` | `courses.views.course_leaderboard.leaderboard_complaint_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/enrollment/toggle` | `cohort_update_enrollment_toggle` | `courses.views.course_enrollment.update_enrollment_toggle` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>` | `cohort_project` | `courses.views.project.project_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/list` | `cohort_project_list` | `courses.views.site_project_gallery.project_gallery_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval` | `cohort_projects_eval` | `courses.views.project_eval.projects_eval_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/results` | `cohort_project_results` | `courses.views.project_results.project_results` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/stats` | `cohort_project_statistics` | `courses.views.project_statistics.project_statistics` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/submissions` | `cohort_project_submissions` | `courses.views.project_submissions.project_submissions` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval/<int:review_id>` | `cohort_projects_eval_submit` | `courses.views.project_eval_submit.projects_eval_submit` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval/add/<int:submission_id>` | `cohort_projects_eval_add` | `courses.views.project_eval_actions.projects_eval_add` |
| `/<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval/delete/<int:review_id>` | `cohort_projects_eval_delete` | `courses.views.project_eval_actions.projects_eval_delete` |
| `/<slug:course_slug>/calendar.ics` | `course_calendar` | `courses.views.course_calendar.course_calendar_view` |
| `/<slug:course_slug>/projects/all` | `family_projects` | `courses.views.site_project_gallery.family_projects_redirect` |
| `/<slug:course_slug>/leaderboard` | `leaderboard` | `courses.views.course_leaderboard.leaderboard_view` |
| `/<slug:course_slug>/leaderboard/<int:enrollment_id>/` | `leaderboard_score_breakdown` | `courses.views.course_leaderboard.leaderboard_score_breakdown_view` |
| `/<slug:course_slug>/leaderboard/<int:enrollment_id>/report` | `leaderboard_complaint` | `courses.views.course_leaderboard.leaderboard_complaint_view` |
| `/<slug:course_slug>/enrollment/toggle` | `update_enrollment_toggle` | `courses.views.course_enrollment.update_enrollment_toggle` |
| `/<slug:course_slug>/enrollment` | `enrollment` | `courses.views.course_enrollment.enrollment_view` |
| `/<slug:course_slug>/dashboard` | `dashboard` | `courses.views.dashboard.dashboard_view` |
| `/<slug:course_slug>/project/<slug:project_slug>` | `project` | `courses.views.project.project_view` |
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
| `/<slug:course_slug>/homework/<slug:homework_slug>/<str:homework_step>` | `homework_step` | `courses.views.homework.homework_view` |
| `/<slug:course_slug>/<slug:cohort_identifier>` | `cohort` | `courses.views.course.course_view` |
| `/<slug:course_slug>/<slug:module_slug>` | `shared_module` | `courses.views.shared_course.shared_module_view` |
| `/<slug:course_slug>/<slug:module_slug>/<slug:lesson_slug>` | `shared_lesson` | `courses.views.shared_course.shared_lesson_view` |
| `/<slug:course_slug>/` | `course` | `courses.views.course.course_view` |
| `/` | `course_list` | `courses.views.course_list.course_list` |
| `/register/confirm/<str:token>/` | `registration_confirm` | `courses.views.registration_confirmation.registration_confirm_view` |
| `/register/resend-confirmation/` | `registration_confirmation_resend` | `courses.views.registration_confirmation.registration_confirmation_resend_view` |
| `/register/fix-email/` | `registration_confirmation_fix_email` | `courses.views.registration_confirmation.registration_confirmation_fix_email_view` |
| `/register/<slug:campaign_slug>/` | `registration_campaign` | `courses.views.registration.registration_campaign_view` |
| `/register/<slug:campaign_slug>/also/` | `registration_cross_sell` | `courses.views.registration.registration_cross_sell_view` |
| `/wrapped/<int:year>/` | `wrapped` | `courses.views.wrapped.wrapped_view` |
| `/wrapped/<int:year>/<int:student_id>/` | `user_wrapped` | `courses.views.wrapped.user_wrapped_view` |
| `/<slug:course_slug>` | `course_family` | `courses.views.course.course_family_view` |

## Management commands

| Command | Owning app | Registered help |
| --- | --- | --- |
| `account_identity_inventory` | `accounts` | Emit the redacted single-account inventory as JSON. |
| `bootstrap_development_owner` | `accounts` | Interactively bootstrap the single development owner without echoing secrets. |
| `close_peer_review` | `courses` | Close peer review for a project (move it to Completed). |
| `import_development_course_content` | `courses` | Import the one approved sanitized CMP public-content artifact into development |
| `import_mail_category_optouts` | `courses` | Apply Datamailer category opt-outs to the local user fields from a preferences JSON export. |
| `learner_duplicate_preflight` | `courses` | Read-only preflight for the learner logical identities: reports duplicate groups and inconsistent references by record/user ID. Never prints learner payload. |
| `migrate_shared_curriculum` | `courses` | Backfill the one current shared curriculum graph from cohort-owned rows. |
| `preview_peer_review_email` | `courses` | Preview the peer-review-assignment email for a project. |
| `seed_local_courses` | `courses` | Seed the local development database with the pinned public course catalog so / and /courses show the same real courses. |
| `seed_local_project_review` | `courses` | Seed a synthetic DE Zoomcamp Project 1 scenario and assign peer reviews in local/test SQLite only. |
| `seed_local_questions` | `courses` | Seed representative homework questions for a local development form. |
| `seed_local_social_providers` | `accounts` | Seed the local development database with placeholder Google, GitHub and Slack sign-in apps so /accounts/signup/ and /accounts/login/ render their provider buttons. The credentials are inert placeholders, not secrets. |
| `send_deadline_reminders` | `courses` | Send deadline reminders through the package mail app. |
| `set_project_state` | `courses` | Set a project state (collecting_submissions, peer_reviewing, completed, closed). |
| `shared_curriculum_inventory` | `courses` | Build the per-cohort shared-curriculum rollout inventory for one course family, optionally validating reviewed keep/archive decisions. |
| `verify_development_course_content` | `courses` | Verify the development course content, route callbacks, and template origins |

## Preserved app identities

| App label | App module |
| --- | --- |
| `accounts` | `accounts` |
| `api` | `api` |
| `studio_courses` | `studio_courses` |
| `courses` | `courses` |
