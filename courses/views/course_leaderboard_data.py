import logging
from dataclasses import dataclass

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Prefetch, Value
from django.db.models.functions import Coalesce

from courses.models.cohort import Enrollment
from courses.models.project import Project, ProjectState, ProjectSubmission

logger = logging.getLogger(__name__)

LEADERBOARD_PAGE_SIZE = 100

# The number of distinct star colors the leaderboard cycles through before a
# project's position repeats a color. Kept small and real (the same palette
# `.step-number`/`.step-number-2`/`.step-number-3` and `.wrapped-rank-1/2/3`
# already use elsewhere for "same shape, different meaning") rather than
# growing an unbounded, decreasingly distinguishable set of colors.
PROJECT_STAR_COLOR_COUNT = 4


@dataclass(frozen=True)
class CurrentLeaderboardStudent:
    enrollment: Enrollment | None
    enrollment_id: int | None


def leaderboard_context(course, user, page_number):
    current_student = current_student_leaderboard_enrollment(course, user)
    enrollments_data = get_leaderboard_data(
        course,
        current_student,
    )

    paginator = Paginator(enrollments_data, LEADERBOARD_PAGE_SIZE)
    page_obj = paginator.get_page(page_number)
    enrollments_page = page_obj.object_list
    page_range = paginator.get_elided_page_range(page_obj.number)

    current_student_page_number = current_student_page_number_for_leaderboard(
        enrollments_data,
        current_student,
    )

    return {
        "enrollments": enrollments_page,
        "page_obj": page_obj,
        "page_range": page_range,
        "total_enrollments": paginator.count,
        "course": course,
        "course_family": course.course,
        "current_student_enrollment": current_student.enrollment,
        "current_student_enrollment_id": current_student.enrollment_id,
        "current_student_page_number": current_student_page_number,
    }


def invalidate_leaderboard_cache(course_id: int) -> None:
    cache.delete(f"leaderboard:{course_id}")
    version_key = f"leaderboard_cache_version:{course_id}"
    current_version = cache.get(version_key, 1)
    next_version = current_version + 1
    cache.set(version_key, next_version, None)


def current_student_leaderboard_enrollment(course, user):
    if user.is_authenticated:
        try:
            enrollment = Enrollment.objects.get(
                student=user,
                course=course,
            )
            return CurrentLeaderboardStudent(
                enrollment=enrollment,
                enrollment_id=enrollment.id,
            )
        except Enrollment.DoesNotExist:
            pass

    return CurrentLeaderboardStudent(enrollment=None, enrollment_id=None)


def completed_project_submissions_prefetch():
    submissions = ProjectSubmission.objects.filter(
        project__state=ProjectState.COMPLETED.value,
        volunteer_review_only=False,
    )
    submissions = submissions.select_related("project")
    submissions = submissions.order_by("project__id")
    return Prefetch(
        "projectsubmission_set",
        queryset=submissions,
        to_attr="completed_project_submissions",
    )


def project_positions(course):
    """Map each of this cohort's project ids to its 1-based cohort position.

    Position is the project's declared order in the cohort — the same
    ascending-id order `courses.views.course_projects.get_projects_for_course`
    already uses to list a cohort's projects — not anything derived from one
    learner's own submission order. That keeps "project 2" naming the same
    project for every learner on this leaderboard, and keeps a project's
    position stable even if a learner skipped an earlier project or a later
    project has not completed review yet.
    """
    ordered_project_ids = (
        Project.objects.filter(course=course).order_by("id").values_list("id", flat=True)
    )
    positions = {}
    for position, project_id in enumerate(ordered_project_ids, 1):
        positions[project_id] = position
    return positions


def project_star_class(position):
    """Return the CSS modifier class for a project's star color.

    Returns "" for the palette's first (default) color, so the markup only
    ever adds a modifier class for colors 2 and up — the same convention
    `.step-number`/`.step-number-2`/`.step-number-3` uses. A cohort with more
    projects than colors cycles back through the palette rather than growing
    it, since a handful of real, distinguishable colors is the point.
    """
    color_number = ((position - 1) % PROJECT_STAR_COLOR_COUNT) + 1
    if color_number == 1:
        return ""
    return f"leaderboard-passed-{color_number}"


def serialize_leaderboard_enrollment(enrollment, project_positions_by_id):
    passed_projects = []
    completed_submissions = enrollment.completed_project_submissions
    for submission in completed_submissions:
        if not submission.passed:
            continue
        position = project_positions_by_id.get(submission.project_id)
        star_class = project_star_class(position) if position else ""
        passed_project = {
            "title": submission.project.title,
            "slug": submission.project.slug,
            "position": position,
            "star_class": star_class,
        }
        passed_projects.append(passed_project)

    return {
        "id": enrollment.id,
        "display_name": enrollment.display_name,
        "total_score": enrollment.total_score,
        "position_on_leaderboard": enrollment.position_on_leaderboard,
        "passed_projects": passed_projects,
    }


def build_leaderboard_data(course, cache_key):
    logger.info(f"Cache miss for leaderboard of course {course.slug}")
    enrollments = Enrollment.objects.filter(
        course=course,
        display_on_leaderboard=True,
    )
    enrollments = enrollments.select_related("student")
    completed_submissions = completed_project_submissions_prefetch()
    enrollments = enrollments.prefetch_related(completed_submissions)
    unranked_position = Value(999999)
    leaderboard_position = Coalesce(
        "position_on_leaderboard",
        unranked_position,
    )
    enrollments = enrollments.order_by(leaderboard_position, "id")
    project_positions_by_id = project_positions(course)
    enrollments_data = []
    for enrollment in enrollments:
        enrollment_data = serialize_leaderboard_enrollment(enrollment, project_positions_by_id)
        enrollments_data.append(enrollment_data)
    cache.set(cache_key, enrollments_data, 3600)
    return enrollments_data


def leaderboard_cache_missing_current_student(
    enrollments_data,
    current_student: CurrentLeaderboardStudent,
):
    if current_student.enrollment_id is None:
        return False
    if not current_student.enrollment.display_on_leaderboard:
        return False

    for enrollment in enrollments_data:
        if enrollment["id"] == current_student.enrollment_id:
            return False

    return True


def get_leaderboard_data(
    course,
    current_student: CurrentLeaderboardStudent,
):
    cache_key = f"leaderboard:{course.id}"
    enrollments_data = cache.get(cache_key)

    if enrollments_data is None:
        return build_leaderboard_data(course, cache_key)

    logger.info(f"Cache hit for leaderboard of course {course.slug}")
    if leaderboard_cache_missing_current_student(
        enrollments_data,
        current_student,
    ):
        return build_leaderboard_data(course, cache_key)

    return enrollments_data


def current_student_page_number_for_leaderboard(
    enrollments_data,
    current_student: CurrentLeaderboardStudent,
):
    if (
        current_student.enrollment_id is None
        or not current_student.enrollment.display_on_leaderboard
    ):
        return None

    for index, enrollment in enumerate(enrollments_data):
        if enrollment["id"] == current_student.enrollment_id:
            return (index // LEADERBOARD_PAGE_SIZE) + 1

    return None
