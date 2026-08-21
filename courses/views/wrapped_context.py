from django.urls import reverse

from courses.models import Cohort
from courses.models.wrapped import UserWrappedStatistics, WrappedStatistics
from courses.views.url_utils import cohort_url_kwargs


def _wrapped_course_records(records: list[dict]) -> list[dict]:
    """Add canonical leaderboard URLs to current and legacy Wrapped records."""

    normalized = [dict(record) for record in records]
    legacy_slugs = {
        str(record["slug"])
        for record in normalized
        if record.get("slug")
        and (not record.get("course_slug") or not record.get("cohort_year"))
    }
    cohorts = {
        cohort.slug: cohort
        for cohort in Cohort.objects.filter(slug__in=legacy_slugs).select_related("course")
    }

    for record in normalized:
        course_slug = record.get("course_slug")
        cohort_year = record.get("cohort_year")
        if not course_slug or not cohort_year:
            cohort = cohorts.get(str(record.get("slug", "")))
            if cohort is not None:
                route_kwargs = cohort_url_kwargs(cohort)
                course_slug = route_kwargs["course_slug"]
                cohort_year = route_kwargs["cohort_year"]
                record.update(route_kwargs)

        if course_slug and cohort_year:
            record["course_url"] = reverse(
                "course",
                kwargs={
                    "course_slug": course_slug,
                    "cohort_year": cohort_year,
                },
            )
        elif record.get("slug"):
            record["course_url"] = reverse(
                "course",
                kwargs={"course_slug": record["slug"]},
            )
        else:
            record["course_url"] = ""

        enrollment_id = record.get("enrollment_id")
        if enrollment_id is None:
            continue
        if course_slug and cohort_year:
            record["leaderboard_url"] = reverse(
                "leaderboard_score_breakdown",
                kwargs={
                    "course_slug": course_slug,
                    "cohort_year": cohort_year,
                    "enrollment_id": enrollment_id,
                },
            )
        elif record.get("slug"):
            # Keep old Wrapped rows usable when their cohort was retired from
            # the current database: the legacy edition route is still valid.
            record["leaderboard_url"] = reverse(
                "leaderboard_score_breakdown",
                kwargs={
                    "course_slug": record["slug"],
                    "enrollment_id": enrollment_id,
                },
            )
        else:
            record["leaderboard_url"] = ""

    return normalized


def visible_wrapped_statistics(year: int) -> WrappedStatistics | None:
    try:
        return WrappedStatistics.objects.get(year=year, is_visible=True)
    except WrappedStatistics.DoesNotExist:
        return None


def platform_stats_context(wrapped_stats: WrappedStatistics) -> dict:
    course_stats = _wrapped_course_records(wrapped_stats.course_stats[:4])
    if not wrapped_stats.course_stats:
        course_stats = []

    return {
        "total_participants": wrapped_stats.total_participants,
        "total_enrollments": wrapped_stats.total_enrollments,
        "course_stats": course_stats,
        "total_hours": wrapped_stats.total_hours,
        "total_certificates": wrapped_stats.total_certificates,
        "total_points": wrapped_stats.total_points,
    }


def wrapped_hours_label(total_hours: float):
    if total_hours > 0:
        return total_hours
    return "N/A"


def user_stats_context(user_wrapped: UserWrappedStatistics) -> dict:
    total_hours = wrapped_hours_label(user_wrapped.total_hours)
    return {
        "total_points": user_wrapped.total_points,
        "courses_enrolled": _wrapped_course_records(user_wrapped.courses),
        "total_hours": total_hours,
        "certificates_earned": user_wrapped.certificates_earned,
    }


def current_user_wrapped_context(request, wrapped_stats) -> dict:
    context = {
        "user_stats": None,
        "user_rank": None,
    }

    if not request.user.is_authenticated:
        return context

    try:
        user_wrapped = UserWrappedStatistics.objects.get(
            wrapped=wrapped_stats, user=request.user
        )
    except UserWrappedStatistics.DoesNotExist:
        return context

    context["user_stats"] = user_stats_context(user_wrapped)
    context["user_rank"] = user_wrapped.rank
    return context


def wrapped_page_context(request, year: int, wrapped_stats) -> dict:
    platform_stats = platform_stats_context(wrapped_stats)
    context = {
        "year": year,
        "platform_stats": platform_stats,
        "leaderboard": wrapped_stats.leaderboard,
        "no_data": False,
    }
    current_user_context = current_user_wrapped_context(
        request,
        wrapped_stats,
    )
    context.update(current_user_context)
    return context


def get_user_wrapped_statistics(year: int, user):
    wrapped_stats = visible_wrapped_statistics(year)
    if wrapped_stats is None:
        return None

    try:
        return UserWrappedStatistics.objects.get(
            wrapped=wrapped_stats, user=user
        )
    except UserWrappedStatistics.DoesNotExist:
        return None


def shareable_user_stats_context(
    user_wrapped: UserWrappedStatistics,
) -> dict:
    total_hours = wrapped_hours_label(user_wrapped.total_hours)
    return {
        "total_points": user_wrapped.total_points,
        "courses": _wrapped_course_records(user_wrapped.courses),
        "total_hours": total_hours,
        "homework_count": user_wrapped.homework_count,
        "project_count": user_wrapped.project_count,
        "peer_reviews_given": user_wrapped.peer_reviews_given,
        "learning_in_public_count": user_wrapped.learning_in_public_count,
        "faq_contributions_count": user_wrapped.faq_contributions_count,
        "certificates_earned": user_wrapped.certificates_earned,
        "has_activity": True,
    }


def wrapped_twitter_text(year: int, user_wrapped) -> str:
    text = (
        f"Check out my @DataTalksClub Wrapped {year}! "
        f"I earned {user_wrapped.total_points} points"
    )
    if user_wrapped.total_hours and user_wrapped.total_hours > 0:
        text += f" and spent {user_wrapped.total_hours} hours learning"
    return f"{text}!"


def shareable_user_wrapped_context(
    year: int, user, user_wrapped
) -> dict:
    user_stats = shareable_user_stats_context(user_wrapped)
    twitter_text = wrapped_twitter_text(year, user_wrapped)
    return {
        "year": year,
        "viewed_user": user,
        "display_name": user_wrapped.display_name,
        "user_stats": user_stats,
        "user_rank": user_wrapped.rank,
        "twitter_text": twitter_text,
        "no_activity": False,
    }
