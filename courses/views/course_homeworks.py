from django.db.models import Prefetch
from django.utils import timezone

from courses import coursework_badges
from courses.models.course import Course
from courses.models.homework import Homework, HomeworkState, Submission


def get_homeworks_for_course(course: Course, user) -> list[Homework]:
    if user.is_authenticated:
        queryset = Submission.objects.filter(student=user)
    else:
        queryset = Submission.objects.none()

    submissions_prefetch = Prefetch(
        "submission_set",
        queryset=queryset,
        to_attr="submissions",
    )

    homeworks = (
        Homework.objects.filter(course=course)
        .prefetch_related(submissions_prefetch)
        .order_by("due_date")
    )

    for homework in homeworks:
        update_homework_with_additional_info(homework)

    return list(homeworks)


def update_homework_with_additional_info(homework: Homework) -> None:
    days_until_due = 0

    if homework.due_date > timezone.now():
        days_until_due = (homework.due_date - timezone.now()).days + 1

    homework.days_until_due = days_until_due
    homework.submitted = False
    homework.score = None

    if homework.submissions:
        submission = homework.submissions[0]

        homework.submitted = True
        if homework.is_scored():
            homework.score = submission.total_score
        else:
            homework.submitted_at = submission.submitted_at

    homework.badge_state_name, homework.badge_pill = homework_badge(homework)


def homework_badge(homework: Homework) -> tuple[str, str]:
    """The words and the pill for one homework's state.

    Reads :mod:`courses.coursework_badges`, the same vocabulary the project
    rows read, so the two tables a course page stacks agree.
    """

    if homework.state == HomeworkState.CLOSED.value:
        return "Closed", coursework_badges.PAST
    if homework.is_scored():
        if homework.submitted:
            return f"Scored ({homework.score})", coursework_badges.RESULT
        return "Scored", coursework_badges.RESULT
    if homework.submitted:
        return "Submitted", coursework_badges.DONE
    return "Open", coursework_badges.YOUR_MOVE
