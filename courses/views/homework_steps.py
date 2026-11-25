"""DTC's site-owned bridge from cohort homework to shared draft steps."""

from copy import copy
from dataclasses import replace

from community_base.coursework.answer_crypto import HomeworkAnswerCryptoError
from community_base.homework_steps.types import (
    AcceptedSubmission,
    Assignment,
    Eligibility,
    FinalField,
    Option,
    QuestionResult,
)
from community_base.homework_steps.types import (
    Question as StepQuestion,
)
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.safestring import mark_safe

from courses.homework_answer_checks import is_answer_correct
from courses.models.cohort import Enrollment
from courses.models.homework import (
    Answer,
    AnswerTypes,
    HomeworkState,
    QuestionTypes,
    Submission,
)
from courses.registration import render_markdown
from courses.registration_wall import registration_wall_blocks_user, require_registration_approval
from courses.views.homework_context import homework_detail_build_context_authenticated
from courses.views.homework_submission import HomeworkPostData, process_homework_submission
from courses.views.url_utils import canonical_cohort_url_kwargs

LEARNING_IN_PUBLIC_KEY = "learning-in-public"
LEARNING_IN_PUBLIC_LABEL = "Learning in Public"


def assignment_key(homework):
    # A homework is owned by an exact cohort, even when slugs are reused.
    return f"dtc:cohort:{homework.course_id}:homework:{homework.pk}"


def homework_step_url(course, homework, step):
    """Build a canonical path for one step of this cohort-owned homework."""

    return reverse(
        "cohort_homework_step",
        kwargs={
            **canonical_cohort_url_kwargs(course),
            "homework_slug": homework.slug,
            "homework_step": step,
        },
    )


def step_context_query_params(request, course):
    """Keep only a valid explicit cohort context on child-step URLs."""

    values = request.GET.getlist("cohort")
    if values == [str(course.identifier)]:
        return {"cohort": course.identifier}
    return {}


def question_key(question):
    return question.source_question_id or f"q-{question.pk}"


def option_key(question, index):
    if question.source_option_ids:
        return question.source_option_ids[index - 1]
    return str(index)


def _step_question(question):
    question_types = {
        QuestionTypes.MULTIPLE_CHOICE.value: "choice",
        QuestionTypes.CHECKBOXES.value: "checkbox",
        QuestionTypes.FREE_FORM.value: "short_text",
        QuestionTypes.FREE_FORM_LONG.value: "long_text",
    }
    options = tuple(
        Option(option_key(question, index), label)
        for index, label in enumerate(question.get_possible_answers(), start=1)
    )
    return StepQuestion(
        key=question_key(question),
        prompt=question.text,
        type=question_types[question.question_type],
        options=options,
    )


def _submitted_answer(question, answer):
    value = (answer.answer_text or "") if answer else ""
    if not question.has_choice_answers():
        return value
    selected = []
    for part in value.split(","):
        if not part.strip():
            continue
        try:
            index = int(part.strip())
            selected.append(option_key(question, index))
        except (ValueError, IndexError):
            continue
    if question.question_type == QuestionTypes.CHECKBOXES.value:
        return selected
    return selected[0] if selected else ""


def learning_in_public_cap(homework, disable_learning_in_public):
    """The step's link cap, or 0 when this learner has no Learning in Public step."""

    if disable_learning_in_public or homework.learning_in_public_cap <= 0:
        return 0
    return homework.learning_in_public_cap


def _learning_in_public_question():
    # Links are one text answer, one link per line: the shared public-links
    # editor writes that shape and the adapter maps it to the submission list.
    return StepQuestion(
        key=LEARNING_IN_PUBLIC_KEY,
        prompt="Share links to public posts about your progress.",
        type="long_text",
        step_label=LEARNING_IN_PUBLIC_LABEL,
    )


def learning_in_public_links_from_answer(answer):
    if not isinstance(answer, str):
        return []
    return [line.strip() for line in answer.splitlines() if line.strip()]


def _final_fields(homework, course):
    fields = []
    if homework.homework_url_field:
        # DTC also accepts git:// homework links, so the legacy model remains
        # the URL validator at final submission rather than a browser URL input.
        fields.append(FinalField("homework_url", "Homework URL", required=True))
    if homework.time_spent_lectures_field:
        fields.append(FinalField("time_spent_lectures", "Lectures and reading (hours)"))
    if homework.time_spent_homework_field:
        fields.append(FinalField("time_spent_homework", "Homework work (hours)"))
    if course.homework_problems_comments_field:
        fields.append(FinalField("problems_comments", "Problems or comments", "textarea"))
    if homework.faq_contribution_field:
        fields.append(FinalField("faq_contribution_url", "FAQ contribution PR or issue URL", "url"))
    return tuple(fields)


def _existing_final_fields(submission, fields):
    if submission is None:
        return {}
    values = {
        "homework_url": submission.homework_link or "",
        "time_spent_lectures": str(submission.time_spent_lectures)
        if submission.time_spent_lectures is not None
        else "",
        "time_spent_homework": str(submission.time_spent_homework)
        if submission.time_spent_homework is not None
        else "",
        "problems_comments": submission.problems_comments or "",
        "faq_contribution_url": submission.faq_contribution_url or "",
    }
    return {field.key: values.get(field.key, "") for field in fields}


def homework_availability(homework):
    """Map DTC's homework state onto the shared ``open|closed|scored`` value.

    The one mapping used by the stepper and by every homework row's status, so
    a row label and the homework page can never disagree.
    """

    if homework.state == HomeworkState.OPEN.value:
        return "open"
    if homework.state == HomeworkState.SCORED.value:
        return "scored"
    return "closed"


def _choice_labels(question, indices):
    labels = question.get_possible_answers()
    return ", ".join(labels[index - 1] for index in sorted(indices) if 0 < index <= len(labels))


def question_result(question, answer):
    """The revealed verdict on one accepted answer, as the classic page judges it.

    Called only for a scored homework: the classic form shows correctness and
    the correct answer only once ``Homework.is_scored()``, and so does this.
    """

    answered = bool(answer and (answer.answer_text or "").strip())
    correct = answered and is_answer_correct(question, answer)
    try:
        if question.has_choice_answers():
            correct_answer = _choice_labels(question, question.get_correct_answer_indices())
        elif question.answer_type == AnswerTypes.ANY.value:
            correct_answer = ""
        else:
            correct_answer = (question.resolved_correct_answer() or "").strip()
    except (HomeworkAnswerCryptoError, ValueError):
        # An unavailable or malformed key never leaks an error to a learner.
        correct_answer = ""
    return QuestionResult(correct=correct, correct_answer=correct_answer)


def build_state_assignment(*, course, homework, questions, submission, answers, lip_cap):
    """The prompt-free descriptor the shared state needs, and the stepper builds on.

    ``answers`` are the submission's ``Answer`` rows. Navigation rows build
    this for many homework at once from batch-loaded rows, so it reads nothing
    from the database itself.
    """

    answer_by_question = {answer.question_id: answer for answer in answers}
    step_questions = tuple(_step_question(question) for question in questions)
    existing_answers = {
        question_key(question): _submitted_answer(question, answer_by_question.get(question.pk))
        for question in questions
    }
    if lip_cap:
        step_questions += (_learning_in_public_question(),)
        existing_answers[LEARNING_IN_PUBLIC_KEY] = "\n".join(
            (submission.learning_in_public_links or []) if submission else []
        )
    fields = _final_fields(homework, course)
    existing_final_fields = _existing_final_fields(submission, fields)
    return Assignment(
        key=assignment_key(homework),
        title=homework.title,
        questions=step_questions,
        final_fields=fields,
        existing_answers=existing_answers,
        existing_final_fields=existing_final_fields,
        has_submission=submission is not None,
        availability=homework_availability(homework),
        accepted_submission=(
            AcceptedSubmission(
                answers=existing_answers,
                final_fields=existing_final_fields,
                submitted_at=submission.submitted_at,
            )
            if submission is not None
            else None
        ),
    )


def build_assignment(*, request, course, homework, questions):
    """Bind one server-resolved cohort assignment and its current options."""
    submission = Submission.objects.filter(homework=homework, student=request.user).first()
    enrollment = Enrollment.objects.filter(student=request.user, course=course).first()
    context = homework_detail_build_context_authenticated(
        type(
            "ContextData",
            (),
            {
                "course": course,
                "homework": homework,
                "questions": questions,
                "submission": submission,
                "enrollment": enrollment,
            },
        )()
    )
    context["stepper_integer_keys"] = [
        question_key(question)
        for question in questions
        if question.answer_type == AnswerTypes.INTEGER.value
    ]
    context["stepper_float_keys"] = [
        question_key(question)
        for question in questions
        if question.answer_type == AnswerTypes.FLOAT.value
    ]
    context["stepper_instructions_html"] = (
        mark_safe(render_markdown(homework.instructions_markdown))
        if homework.instructions_markdown
        else ""
    )
    lip_cap = learning_in_public_cap(homework, context["disable_learning_in_public"])
    context["learning_in_public_cap"] = lip_cap
    answers = (
        list(Answer.objects.filter(submission=submission, question__homework=homework))
        if submission
        else []
    )
    state_assignment = build_state_assignment(
        course=course,
        homework=homework,
        questions=questions,
        submission=submission,
        answers=answers,
        lip_cap=lip_cap,
    )
    results = None
    if homework.is_scored() and submission is not None:
        answer_by_question = {answer.question_id: answer for answer in answers}
        results = {
            question_key(question): question_result(question, answer_by_question.get(question.pk))
            for question in questions
        }
    return replace(
        state_assignment,
        introduction=homework.description,
        instructions=homework.instructions_markdown,
        context=context,
        question_results=results,
    )


class DtcHomeworkStepsAdapter:
    def __init__(self, course, homework, questions):
        self.course = course
        self.homework = homework
        self.questions = questions

    def eligibility(self, request, assignment):
        current = type(self.homework).objects.get(pk=self.homework.pk)
        is_open = current.state == HomeworkState.OPEN.value
        if registration_wall_blocks_user(current.course, request.user):
            return Eligibility(
                read=True, write=False, submit=False, reason="Registration approval is required."
            )
        if current.state == HomeworkState.SCORED.value:
            reason = "This homework has been scored."
        elif not is_open:
            reason = "This homework is not open for submissions."
        else:
            reason = ""
        return Eligibility(read=True, write=is_open, submit=is_open, reason=reason)

    def submit(self, request, assignment, answers, final_fields):
        current_homework = type(self.homework).objects.get(pk=self.homework.pk)
        require_registration_approval(current_homework.course, request.user)
        if current_homework.state != HomeworkState.OPEN.value:
            raise ValidationError("This homework is not open for submissions.")
        post = QueryDict(mutable=True)
        for question in self.questions:
            value = answers.get(
                question_key(question),
                []
                if question.has_choice_answers()
                and question.question_type == QuestionTypes.CHECKBOXES.value
                else "",
            )
            if question.has_choice_answers():
                selected = value if isinstance(value, list) else [value]
                indices = [
                    str(index)
                    for key in selected
                    if key
                    for index in range(1, len(question.get_possible_answers()) + 1)
                    if option_key(question, index) == key
                ]
                post.setlist(f"answer_{question.pk}", indices)
            else:
                post[f"answer_{question.pk}"] = value
        for key, value in final_fields.items():
            post[key] = value
        context = assignment.context if isinstance(assignment.context, dict) else {}
        if context.get("learning_in_public_cap"):
            post.setlist(
                "learning_in_public_links[]",
                learning_in_public_links_from_answer(answers.get(LEARNING_IN_PUBLIC_KEY, "")),
            )
        elif self.homework.learning_in_public_cap > 0:
            # A learner without the step keeps the links already on record.
            submission = Submission.objects.filter(
                homework=self.homework, student=request.user
            ).first()
            post.setlist(
                "learning_in_public_links[]",
                submission.learning_in_public_links or [] if submission else [],
            )
        synthetic_request = copy(request)
        synthetic_request._post = post
        submission = Submission.objects.filter(homework=self.homework, student=request.user).first()
        data = HomeworkPostData(
            request=synthetic_request,
            course=self.course,
            homework=current_homework,
            questions=self.questions,
            submission=submission,
            enrollment=Enrollment.objects.filter(student=request.user, course=self.course).first(),
        )
        response = process_homework_submission(data)
        if response.status_code in (301, 302, 303):
            # The legacy service owns writes, scoring, messages and callbacks;
            # keep its successful result but return the canonical Review path.
            return redirect(homework_step_url(self.course, current_homework, "review"))
        return response
