from dataclasses import dataclass
from pathlib import PurePosixPath

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404
from django.utils import timezone

from courses.models.cohort import Cohort, Enrollment, User
from courses.models.homework import (
    Answer,
    Homework,
    HomeworkState,
    Question,
    Submission,
)
from courses.models.shared_curriculum import CohortSharedModule, SharedLesson
from courses.services.course_context import context_query
from courses.views.homework_answers import process_question_options
from courses.views.url_utils import get_cohort_or_404


@dataclass(frozen=True)
class HomeworkDetailContextData:
    course: Cohort
    homework: Homework
    questions: list[Question]
    submission: Submission | None
    enrollment: Enrollment | None


@dataclass(frozen=True)
class HomeworkDetailObjects:
    course: Cohort
    homework: Homework
    questions: list[Question]


@dataclass(frozen=True)
class AuthenticatedHomeworkContext:
    context: dict
    submission: Submission | None
    enrollment: Enrollment


def homework_deadline_passed(homework: Homework) -> bool:
    return homework.due_date < timezone.now()


def homework_state_context(homework: Homework) -> dict[str, bool]:
    accepting_submissions = homework.state == HomeworkState.OPEN.value
    return {
        "disabled": not accepting_submissions,
        "accepting_submissions": accepting_submissions,
        "deadline_passed": homework_deadline_passed(homework),
    }


def homework_instructions_url(
    course: Cohort,
    homework: Homework,
) -> str:
    """Return the explicit or source-backed instructions URL for a homework."""

    if homework.instructions_url:
        return homework.instructions_url

    repository_url = course.course.github_repo_url or course.github_repo_url
    if not repository_url or not homework.source_path or not homework.source_commit_sha:
        return ""

    instructions_path = PurePosixPath(homework.source_path).with_suffix(".md")
    return (
        f"{repository_url.rstrip('/')}/blob/"
        f"{homework.source_commit_sha}/{instructions_path.as_posix()}"
    )


def homework_terminal_module(homework: Homework):
    """Return the legacy module this homework closes, or None otherwise.

    ``Module.terminal_homework`` is a one-to-one, so the reverse accessor
    raises rather than returning ``None`` for a cohort that publishes no
    ``Module`` at all -- which is every cohort except a
    ``curriculum_format=modules`` one; a flat/legacy cohort and a
    ``curriculum_format=shared`` cohort both land here. The page uses this
    for the module crumb and the back-to-module link, both of which must
    simply disappear there in favour of the shared-curriculum equivalent
    (see :func:`homework_shared_module_placement`).
    """

    try:
        return homework.terminal_module
    except ObjectDoesNotExist:
        return None


def homework_shared_module_placement(homework: Homework) -> CohortSharedModule | None:
    """Return the shared-curriculum module placement this homework closes.

    A ``curriculum_format=shared`` cohort never creates a legacy ``Module``
    row (see :func:`homework_terminal_module`), so its module context lives
    instead on the ``CohortSharedModule`` placement that names this homework
    as the module's terminal homework. A homework can be the terminal
    homework of at most one placement, since a placement's terminal homework
    must belong to the placement's own cohort and a homework belongs to one
    cohort.
    """

    return (
        CohortSharedModule.objects.filter(terminal_homework=homework)
        .select_related("shared_module")
        .first()
    )


def homework_navigation_context(
    course: Cohort,
    homework: Homework,
) -> dict[str, object]:
    """Provide the module/breadcrumb context this homework is reached through."""

    shared_placement = homework_shared_module_placement(homework)
    shared_homework_module = (
        shared_placement.shared_module if shared_placement else None
    )
    shared_homework_module_lessons = (
        list(
            SharedLesson.objects.filter(
                module=shared_homework_module, published=True
            ).order_by("position", "id")
        )
        if shared_homework_module
        else []
    )
    return {
        "instructions_url": homework_instructions_url(course, homework),
        "homework_module": homework_terminal_module(homework),
        "shared_homework_module": shared_homework_module,
        "shared_homework_module_lessons": shared_homework_module_lessons,
        "context_q": context_query(course),
    }


def homework_detail_build_context_not_authenticated(
    course: Cohort,
    homework: Homework,
    questions: list[Question],
) -> dict:
    question_answers = question_answers_for_submission(
        homework,
        questions,
        None,
    )
    accepting_submissions = homework.state == HomeworkState.OPEN.value
    context = {
        "course": course,
        "course_family": course.course,
        "homework": homework,
        "question_answers": question_answers,
        "is_authenticated": False,
        "disabled": True,
        "accepting_submissions": accepting_submissions,
        "deadline_passed": homework_deadline_passed(homework),
    }
    context.update(homework_navigation_context(course, homework))

    return context


def submission_answer_map(
    submission: Submission | None,
) -> dict[int, Answer]:
    if not submission:
        return {}

    answers = Answer.objects.filter(
        submission=submission
    ).select_related("question")
    answer_map = {}
    for answer in answers:
        answer_map[answer.question.id] = answer
    return answer_map


def question_answers_for_submission(
    homework: Homework,
    questions: list[Question],
    submission: Submission | None,
) -> list[tuple[Question, dict]]:
    question_answers_map = submission_answer_map(submission)
    question_answers = []

    for question in questions:
        answer = question_answers_map.get(question.id)
        processed_answer = process_question_options(
            homework,
            question,
            answer,
        )
        question_answer = (question, processed_answer)
        question_answers.append(question_answer)

    return question_answers


def learning_in_public_disabled(
    enrollment: Enrollment | None,
) -> bool:
    if enrollment is None:
        return False
    return enrollment.disable_learning_in_public


def homework_detail_build_context_authenticated(data) -> dict:
    question_answers = question_answers_for_submission(
        data.homework,
        data.questions,
        data.submission,
    )
    disable_learning_in_public = learning_in_public_disabled(
        data.enrollment
    )
    state_context = homework_state_context(data.homework)
    context = {
        "course": data.course,
        "course_family": data.course.course,
        "homework": data.homework,
        "question_answers": question_answers,
        "submission": data.submission,
        "is_authenticated": True,
        "disable_learning_in_public": disable_learning_in_public,
    }
    context.update(state_context)
    context.update(homework_navigation_context(data.course, data.homework))
    return context


def homework_detail_objects(
    course_slug: str,
    homework_slug: str,
    cohort_identifier: str | int | None = None,
):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    homework = get_object_or_404(
        Homework,
        course=course,
        slug=homework_slug,
    )
    questions = Question.objects.filter(homework=homework).order_by(
        "id"
    )
    return HomeworkDetailObjects(
        course=course,
        homework=homework,
        questions=questions,
    )


def authenticated_homework_context(
    user: User,
    course: Cohort,
    homework: Homework,
    questions: list[Question],
    *,
    create_enrollment: bool = False,
):
    submission = Submission.objects.filter(
        homework=homework,
        student=user,
    ).first()
    if create_enrollment:
        # Only the authorized mutation path (an actual POST submission) may
        # create the enrollment it submits against.  A GET/HEAD render, a
        # copied URL, or a context preference never enrolls the reader.
        enrollment, _ = Enrollment.objects.get_or_create(
            student=user,
            course=course,
        )
    else:
        enrollment = Enrollment.objects.filter(
            student=user,
            course=course,
        ).first()
    context_data = HomeworkDetailContextData(
        course=course,
        homework=homework,
        questions=questions,
        submission=submission,
        enrollment=enrollment,
    )
    context = homework_detail_build_context_authenticated(context_data)
    return AuthenticatedHomeworkContext(
        context=context,
        submission=submission,
        enrollment=enrollment,
    )
