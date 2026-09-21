"""DTC's site-owned bridge from cohort homework to shared draft steps."""

from copy import copy

from community_base.homework_steps.types import (
    Assignment,
    Eligibility,
    FinalField,
    Option,
)
from community_base.homework_steps.types import (
    Question as StepQuestion,
)
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.utils.safestring import mark_safe

from courses.models.cohort import Enrollment
from courses.models.homework import (
    Answer,
    AnswerTypes,
    HomeworkState,
    QuestionTypes,
    Submission,
)
from courses.registration import render_markdown
from courses.views.homework_context import homework_detail_build_context_authenticated
from courses.views.homework_submission import HomeworkPostData, process_homework_submission


def assignment_key(homework):
    # A homework is owned by an exact cohort, even when slugs are reused.
    return f"dtc:cohort:{homework.course_id}:homework:{homework.pk}"


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


def _final_fields(homework, course, disable_learning_in_public):
    fields = []
    if homework.homework_url_field:
        # DTC also accepts git:// homework links, so the legacy model remains
        # the URL validator at final submission rather than a browser URL input.
        fields.append(FinalField("homework_url", "Homework URL", required=True))
    if homework.learning_in_public_cap > 0 and not disable_learning_in_public:
        fields.extend(
            FinalField(
                f"learning_in_public_link_{index}", f"Learning in public link {index}", "url"
            )
            for index in range(1, homework.learning_in_public_cap + 1)
        )
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
    for index, link in enumerate(submission.learning_in_public_links or [], start=1):
        values[f"learning_in_public_link_{index}"] = link
    return {field.key: values.get(field.key, "") for field in fields}


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
    fields = _final_fields(homework, course, context["disable_learning_in_public"])
    saved_answers = (
        Answer.objects.filter(submission=submission, question__homework=homework)
        if submission
        else ()
    )
    answer_by_question = {answer.question_id: answer for answer in saved_answers}
    return Assignment(
        key=assignment_key(homework),
        title=homework.title,
        questions=tuple(_step_question(question) for question in questions),
        introduction=homework.description,
        instructions=homework.instructions_markdown,
        final_fields=fields,
        existing_answers={
            question_key(question): _submitted_answer(question, answer_by_question.get(question.pk))
            for question in questions
        },
        existing_final_fields=_existing_final_fields(submission, fields),
        context=context,
    )


class DtcHomeworkStepsAdapter:
    def __init__(self, course, homework, questions):
        self.course = course
        self.homework = homework
        self.questions = questions

    def eligibility(self, request, assignment):
        current = type(self.homework).objects.get(pk=self.homework.pk)
        is_open = current.state == HomeworkState.OPEN.value
        return Eligibility(
            read=True,
            write=is_open,
            submit=is_open,
            reason="This homework is not open for submissions." if not is_open else "",
        )

    def submit(self, request, assignment, answers, final_fields):
        if not self.eligibility(request, assignment).submit:
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
            if not key.startswith("learning_in_public_link_"):
                post[key] = value
        link_keys = sorted(
            (key for key in final_fields if key.startswith("learning_in_public_link_")),
            key=lambda key: int(key.rsplit("_", 1)[-1]),
        )
        if link_keys:
            post.setlist("learning_in_public_links[]", [final_fields[key] for key in link_keys])
        elif self.homework.learning_in_public_cap > 0:
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
            homework=self.homework,
            questions=self.questions,
            submission=submission,
            enrollment=Enrollment.objects.filter(student=request.user, course=self.course).first(),
        )
        return process_homework_submission(data)
