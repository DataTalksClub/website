"""Per-learner homework status for navigation rows and the homework page.

The label comes from the shared ``community_base.homework_steps.state`` model
(Not submitted / Draft / Submitted / Unsubmitted changes / Scored / Closed —
not submitted), computed from the same descriptor the stepper builds, so a
row and the homework page can never disagree. Rows are batch-loaded: one
query each for the questions, the learner's submissions, their answers, the
enrollment and the drafts, whatever the number of homework rows.
"""

from collections import defaultdict

from community_base.homework_steps.models import HomeworkDraft
from community_base.homework_steps.state import calculate_homework_state

from courses import coursework_badges
from courses.models.cohort import Enrollment
from courses.models.homework import Answer, Question, Submission
from courses.services.enrollment_flags import learning_in_public_disabled
from courses.views.homework_steps import (
    assignment_key,
    build_state_assignment,
    learning_in_public_cap,
)

# The course page's one coursework vocabulary (`courses.coursework_badges`).
HOMEWORK_STATE_PILLS = {
    "not_submitted": coursework_badges.YOUR_MOVE,
    "draft": coursework_badges.YOUR_MOVE,
    "unsubmitted_changes": coursework_badges.YOUR_MOVE,
    "submitted": coursework_badges.DONE,
    "scored": coursework_badges.RESULT,
    "closed_not_submitted": coursework_badges.PAST,
}


def homework_state_pill(state) -> str:
    return HOMEWORK_STATE_PILLS.get(getattr(state, "value", ""), coursework_badges.PAST)


def learner_homework_states(user, course, homeworks) -> dict:
    """``{homework.pk: LearnerHomeworkState}`` for one learner in one cohort."""

    homeworks = [homework for homework in homeworks if homework.course_id == course.pk]
    if not getattr(user, "is_authenticated", False) or not homeworks:
        return {}
    questions_by_homework = defaultdict(list)
    for question in Question.objects.filter(homework__in=homeworks).order_by("id"):
        questions_by_homework[question.homework_id].append(question)
    submissions = {}
    for submission in Submission.objects.filter(student=user, homework__in=homeworks).order_by(
        "id"
    ):
        submissions.setdefault(submission.homework_id, submission)
    answers_by_submission = defaultdict(list)
    for answer in Answer.objects.filter(submission__in=list(submissions.values())):
        answers_by_submission[answer.submission_id].append(answer)
    enrollment = Enrollment.objects.filter(student=user, course=course).first()
    disable_lip = learning_in_public_disabled(enrollment)
    drafts = {
        draft.assignment_key: draft
        for draft in HomeworkDraft.objects.filter(
            user=user,
            assignment_key__in=[assignment_key(homework) for homework in homeworks],
        ).only("assignment_key", "answers", "final_fields")
    }
    states = {}
    for homework in homeworks:
        questions = questions_by_homework.get(homework.pk, [])
        submission = submissions.get(homework.pk)
        assignment = build_state_assignment(
            course=course,
            homework=homework,
            questions=questions,
            submission=submission,
            answers=answers_by_submission.get(submission.pk, []) if submission else [],
            lip_cap=learning_in_public_cap(homework, disable_lip),
        )
        draft = drafts.get(assignment.key)
        states[homework.pk] = calculate_homework_state(
            assignment,
            draft_answers=draft.answers if draft else {},
            draft_final_fields=draft.final_fields if draft else {},
            draft_exists=draft is not None,
        )
    return states


def learner_homework_state(user, homework):
    """One learner's state for one homework, or None for an anonymous reader."""

    return learner_homework_states(user, homework.course, [homework]).get(homework.pk)
