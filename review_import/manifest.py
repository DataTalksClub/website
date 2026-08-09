"""Versioned source-field boundary for local CMP content review data."""

from __future__ import annotations

ALLOWLIST_SCHEMA_VERSION = "cmp-public-review-v1"

HOMEWORK_STAT_METRICS = (
    "questions_score",
    "total_score",
    "learning_in_public_score",
    "time_spent_lectures",
    "time_spent_homework",
)
PROJECT_STAT_METRICS = (
    "project_score",
    "project_learning_in_public_score",
    "peer_review_score",
    "peer_review_learning_in_public_score",
    "total_score",
    "time_spent",
)
STAT_PREFIXES = ("min", "max", "avg", "median", "q1", "q3")


def _stat_columns(metrics: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{prefix}_{metric}" for metric in metrics for prefix in STAT_PREFIXES)


ALLOWLIST: dict[str, tuple[str, ...]] = {
    "courses_course": (
        "id",
        "slug",
        "title",
        "description",
        "start_date",
        "end_date",
        "registration_url",
        "github_repo_url",
        "social_media_hashtag",
        "first_homework_scored",
        "finished",
        "faq_document_url",
        "min_projects_to_pass",
        "homework_problems_comments_field",
        "project_passing_score",
        "visible",
    ),
    "courses_registrationcampaign": (
        "id",
        "slug",
        "title",
        "edition_label",
        "current_course_id",
        "is_active",
        "marketing_markdown",
        "meta_description",
        "hero_image_url",
        "video_url",
        "created_at",
        "updated_at",
    ),
    "courses_homework": (
        "id",
        "slug",
        "course_id",
        "title",
        "description",
        "instructions_url",
        "due_date",
        "learning_in_public_cap",
        "homework_url_field",
        "time_spent_lectures_field",
        "time_spent_homework_field",
        "faq_contribution_field",
        "state",
    ),
    "courses_question": (
        "id",
        "homework_id",
        "text",
        "question_type",
        "answer_type",
        "possible_answers",
        "correct_answer",
        "scores_for_correct_answer",
    ),
    "courses_homeworkstatistics": (
        "id",
        "homework_id",
        "total_submissions",
        "last_calculated",
        *_stat_columns(HOMEWORK_STAT_METRICS),
    ),
    "courses_project": (
        "id",
        "course_id",
        "slug",
        "title",
        "description",
        "instructions_url",
        "submission_due_date",
        "learning_in_public_cap_project",
        "peer_review_due_date",
        "time_spent_project_field",
        "problems_comments_field",
        "faq_contribution_field",
        "learning_in_public_cap_review",
        "number_of_peers_to_evaluate",
        "points_for_peer_review",
        "time_spent_evaluation_field",
        "state",
    ),
    "courses_reviewcriteria": (
        "id",
        "course_id",
        "description",
        "options",
        "review_criteria_type",
    ),
    "courses_projectstatistics": (
        "id",
        "project_id",
        "total_submissions",
        "last_calculated",
        *_stat_columns(PROJECT_STAT_METRICS),
    ),
    "courses_wrappedstatistics": (
        "id",
        "year",
        "is_visible",
        "total_participants",
        "total_enrollments",
        "total_hours",
        "total_certificates",
        "total_points",
        "course_stats",
        "calculated_at",
        "created_at",
    ),
}

COPY_ORDER = tuple(ALLOWLIST)

# These tables may contain identity, learner activity, communication data,
# credentials, operational payloads, or user-authored free text. They must be
# empty in a sanitized artifact. A final review DB may contain only the one
# separately-created synthetic administrator in accounts_customuser.
SENSITIVE_TABLES = frozenset(
    {
        "account_emailaddress",
        "account_emailconfirmation",
        "accounts_customuser",
        "accounts_customuser_groups",
        "accounts_customuser_user_permissions",
        "accounts_token",
        "auth_group",
        "auth_group_permissions",
        "courses_answer",
        "courses_courseregistration",
        "courses_criteriaresponse",
        "courses_enrollment",
        "courses_leaderboardcomplaint",
        "courses_peerreview",
        "courses_projectevaluationscore",
        "courses_projectsubmission",
        "courses_projectvote",
        "courses_submission",
        "courses_userwrappedstatistics",
        "django_admin_log",
        "django_session",
        "socialaccount_socialaccount",
        "socialaccount_socialapp",
        "socialaccount_socialapp_sites",
        "socialaccount_socialtoken",
    }
)

SENSITIVE_PREFIXES = (
    "api_",
    "content_sync_",
    "core_",
    "data_",
    "django_q_",
    "email_app_",
    "events_",
    "jobs_",
    "management_auth_",
)

LOCAL_METADATA_TABLES = frozenset(
    {
        "auth_permission",
        "django_content_type",
        "django_migrations",
        "django_site",
    }
)


def is_sensitive_table(table: str) -> bool:
    return table in SENSITIVE_TABLES or table.startswith(SENSITIVE_PREFIXES)
