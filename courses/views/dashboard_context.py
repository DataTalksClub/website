import statistics

from django.utils import timezone

from courses.models.cohort import Enrollment
from courses.models.homework import Submission
from courses.models.project import Project
from courses.views.dashboard_engagement import (
    dashboard_engagement_chart,
    dashboard_engagement_trend,
)
from courses.views.dashboard_homeworks import (
    dashboard_homework_stats,
    dashboard_homework_submissions,
    dashboard_homeworks,
)
from courses.views.dashboard_metrics import quartile_fields, safe_pct
from courses.views.dashboard_projects import dashboard_project_stats
from courses.views.dashboard_questions import (
    dashboard_hardest_questions,
    dashboard_question_difficulty,
)
from courses.views.dashboard_timing import (
    dashboard_submission_timing,
    dashboard_submission_timing_deadline_pct,
    dashboard_submission_timing_is_degenerate,
)


def dashboard_context(course):
    registered_count = (
        Enrollment.objects.filter(course=course)
        .values("student_id")
        .distinct()
        .count()
    )
    enrolled_count = (
        Submission.objects.filter(
            homework__course=course,
            enrollment__course=course,
        )
        .values("student_id")
        .distinct()
        .count()
    )
    homeworks = dashboard_homeworks(course)
    homework_submissions = dashboard_homework_submissions(course)
    homework_stats, homework_difficulty_stats = dashboard_homework_stats(
        homeworks,
        homework_submissions,
        enrolled_count,
    )
    raw_avg_total_score = dashboard_avg_total_score(course)
    avg_total_score = round(raw_avg_total_score, 1)
    overall_completion_rate = dashboard_overall_completion_rate(homework_stats)
    total_score_distribution = dashboard_total_score_distribution(course, raw_avg_total_score)
    question_difficulty = dashboard_question_difficulty(course)
    hardest_questions = dashboard_hardest_questions(question_difficulty)
    submission_timing = dashboard_submission_timing(course)
    engagement_trend, engagement_dropped_count = dashboard_engagement_trend(course)
    engagement_chart = dashboard_engagement_chart(engagement_trend)
    graduates_count = dashboard_graduates_count(course)
    project_stats = dashboard_project_stats(course, enrolled_count)
    homework_total_submissions = sum(hw_stat["submissions_count"] for hw_stat in homework_stats)
    has_time_data, has_score_data = dashboard_homework_column_flags(homework_stats)
    engagement_span = dashboard_engagement_span(engagement_trend)
    hardest_tier_count = sum(1 for hw_stat in homework_stats if hw_stat.get("is_hardest_tier"))
    hardest_tier_pct = (
        homework_difficulty_stats[0]["score_ratio_pct"] if homework_difficulty_stats else None
    )

    return {
        "course": course,
        "course_family": course.course,
        # This legacy context key still denotes the cohort's registration
        # records. The public labels below distinguish those records from
        # learners who started the coursework.
        "total_enrollments": registered_count,
        "registered_count": registered_count,
        "enrolled_count": enrolled_count,
        "avg_total_score": avg_total_score,
        "overall_completion_rate": overall_completion_rate,
        "project_passing_score": course.project_passing_score,
        "graduates_count": graduates_count,
        "graduates_rate": safe_pct(graduates_count, enrolled_count),
        "homework_stats": homework_stats,
        "homework_difficulty_stats": homework_difficulty_stats,
        "homework_total_submissions": homework_total_submissions,
        "homeworks_scored_count": len(homework_stats),
        "homework_has_time_data": has_time_data,
        "homework_has_score_data": has_score_data,
        "hardest_tier_count": hardest_tier_count,
        "hardest_tier_pct": hardest_tier_pct,
        "projects_count": Project.objects.filter(course=course).count(),
        "question_difficulty": question_difficulty,
        "hardest_questions": hardest_questions,
        "submission_timing": submission_timing,
        "submission_timing_degenerate": dashboard_submission_timing_is_degenerate(
            submission_timing
        ),
        "submission_timing_deadline_pct": dashboard_submission_timing_deadline_pct(
            submission_timing
        ),
        "engagement_trend": engagement_trend,
        "engagement_dropped_count": engagement_dropped_count,
        "engagement_chart": engagement_chart,
        "engagement_span_start": engagement_span[0] if engagement_span else None,
        "engagement_span_end": engagement_span[1] if engagement_span else None,
        "lifecycle_status": dashboard_lifecycle_status(course),
        **total_score_distribution,
        **project_stats,
    }


def dashboard_overall_completion_rate(homework_stats):
    completion_rates = [
        hw_stat["completion_rate"] for hw_stat in homework_stats
    ]
    if not completion_rates:
        return None
    return round(statistics.mean(completion_rates), 1)


def dashboard_total_score_distribution(course, avg_total_score):
    total_scores = list(
        Enrollment.objects.filter(
            course=course, total_score__isnull=False
        ).values_list("total_score", flat=True)
    )
    fields = quartile_fields("total_score", total_scores)
    fields["total_score_range"] = dashboard_score_range(
        total_scores,
        avg_total_score,
        fields["total_score_q25"],
        fields["total_score_median"],
        fields["total_score_q75"],
    )
    return fields


def dashboard_score_range(total_scores, mean_value, q25, median, q75):
    """The range-bar geometry for "total score across the cohort".

    ``None`` when there is nothing to scale against -- no scores at all, or
    every recorded score is zero -- so the template can show a plain
    sentence instead of a bar with nothing on it.
    """

    if not total_scores:
        return None
    max_value = max(total_scores)
    if max_value <= 0:
        return None

    def pct(value):
        if value is None:
            return 0.0
        bounded = max(0.0, min(value, max_value))
        return round(bounded / max_value * 100, 1)

    q25_pct = pct(q25)
    q75_pct = pct(q75) if q75 is not None else q25_pct
    return {
        "max": max_value,
        "mid": round(max_value / 2),
        "mean": round(mean_value, 1),
        "median": median,
        "q25": q25,
        "q75": q75,
        "q25_pct": q25_pct,
        "iqr_width_pct": max(round(q75_pct - q25_pct, 1), 0),
        "median_pct": pct(median),
        "mean_pct": pct(mean_value),
    }


def dashboard_avg_total_score(course):
    enrollments_with_scores = Enrollment.objects.filter(
        course=course, total_score__isnull=False
    ).values_list("total_score", flat=True)
    if enrollments_with_scores:
        return statistics.mean(enrollments_with_scores)
    return 0


def dashboard_graduates_count(course):
    return (
        Enrollment.objects
        .filter(
            course=course, certificate_url__isnull=False
        )
        .exclude(certificate_url="")
        .count()
    )


def dashboard_homework_column_flags(homework_stats):
    """Whether any homework recorded time or score data.

    A column that is a dash for every row (2021 recorded no time spent at
    all) is dropped from the table entirely instead of being drawn as dead
    weight, so the template renders a narrower grid rather than 27 dashes.
    """

    has_time = any(hw_stat["time_total_median"] is not None for hw_stat in homework_stats)
    has_score = any(hw_stat["score_median"] is not None for hw_stat in homework_stats)
    return has_time, has_score


def dashboard_engagement_span(engagement_trend):
    """First and last week label of the engagement trend, for the hero note."""

    if not engagement_trend:
        return None
    return engagement_trend[0]["label"], engagement_trend[-1]["label"]


def dashboard_lifecycle_status(course):
    """The hero's honest lifecycle pill: what the cohort's own dates say now.

    Mirrors the reading `courses.course_page_content.family_edition_rows`
    already gives an edition card -- finished cohorts read against
    ``finished``, everything else reads against ``start_date`` -- but for one
    cohort rather than a strip of them, so there is no registration-campaign
    override or self-paced branch to carry.
    """

    if course.finished:
        return {"words": "finished cohort", "variant": "wait"}
    today = timezone.localdate()
    if course.start_date and course.start_date > today:
        return {"words": "registration open", "variant": "open"}
    return {"words": "in progress", "variant": "live"}
