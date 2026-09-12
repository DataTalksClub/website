from django.core.exceptions import ValidationError
from django.http import HttpRequest

from courses.models.homework import Answer, Homework, Question, Submission
from courses.views.homework_answers import process_question_options
from courses.views.homework_context import (
    homework_navigation_context,
    homework_state_context,
)
from courses.views.homework_post_fields import (
    apply_homework_post_preview_fields,
)
from courses.views.homework_submission import HomeworkPostData


def answer_from_post(
    request: HttpRequest, question: Question
) -> Answer:
    answer_values = []
    post_values = request.POST.getlist(f"answer_{question.id}")
    for value in post_values:
        answer_value = value.strip()
        answer_values.append(answer_value)
    answer_text = ",".join(answer_values)
    return Answer(question=question, answer_text=answer_text)


def bound_homework_submission_from_post(
    data: HomeworkPostData,
) -> Submission:
    bound_submission = data.submission or Submission(
        homework=data.homework,
        student=data.request.user,
        enrollment=data.enrollment,
    )
    apply_homework_post_preview_fields(
        data.request,
        data.course,
        data.homework,
        bound_submission,
    )
    return bound_submission


def question_answers_from_post(
    request: HttpRequest,
    homework: Homework,
    questions: list[Question],
) -> list[tuple[Question, dict]]:
    question_answers = []
    for question in questions:
        answer = answer_from_post(request, question)
        processed_answer = process_question_options(
            homework,
            question,
            answer,
        )
        question_answer = (question, processed_answer)
        question_answers.append(question_answer)
    return question_answers


def homework_detail_build_context_from_post(
    data: HomeworkPostData,
) -> dict:
    bound_submission = bound_homework_submission_from_post(data)
    question_answers = question_answers_from_post(
        data.request,
        data.homework,
        data.questions,
    )
    disable_learning_in_public = data.enrollment.disable_learning_in_public
    state_context = homework_state_context(data.homework)
    context = {
        "course": data.course,
        "course_family": data.course.course,
        "homework": data.homework,
        "question_answers": question_answers,
        "submission": bound_submission,
        "is_authenticated": True,
        "disable_learning_in_public": disable_learning_in_public,
    }
    context.update(state_context)
    context.update(homework_navigation_context(data.course, data.homework))
    return context


HOMEWORK_ERROR_FIELD_MAP = {
    "homework_link": "homework_url",
    "learning_in_public_links": "learning_in_public_links",
    "time_spent_lectures": "time_spent_lectures",
    "time_spent_homework": "time_spent_homework",
    "problems_comments": "problems_comments",
    "faq_contribution_url": "faq_contribution_url",
}

# A handful of the fields above are validated by a plain function call
# (`parse_time_spent_hours`, the learning-in-public link checks) rather than
# by a model validator, so they raise a single-message `ValidationError` with
# no `message_dict` to key off of.  These phrases are stable substrings of
# the messages those call sites actually raise (see
# `courses.views.submission_formatting.parse_time_spent_hours` and
# `courses.views.homework_learning_links`), used only to point the shared
# error summary/per-field marker at the right control -- the accept/reject
# decision and the message text are unchanged.
HOMEWORK_ERROR_MESSAGE_MARKERS = (
    ("time spent on lectures", "time_spent_lectures"),
    ("time spent on homework", "time_spent_homework"),
    ("learning in public link", "learning_in_public_links"),
)


def homework_error_fields(error: ValidationError) -> set[str]:
    if not hasattr(error, "message_dict"):
        return set()

    fields = set()
    error_field_names = error.message_dict
    for field_name in error_field_names:
        if field_name in HOMEWORK_ERROR_FIELD_MAP:
            field = HOMEWORK_ERROR_FIELD_MAP[field_name]
            fields.add(field)
    return fields


def _field_for_message(message: str, message_field_by_text: dict[str, str]) -> str | None:
    field = message_field_by_text.get(message)
    if field:
        return field
    lowered = message.lower()
    for marker, mapped_field in HOMEWORK_ERROR_MESSAGE_MARKERS:
        if marker in lowered:
            return mapped_field
    return None


def homework_error_field_messages(
    error: ValidationError,
) -> dict[str, list[str]]:
    """Attribute every rendered error message to the field it names.

    Feeds both the shared `.a11y-error-summary` (one linked item per error)
    and the per-field `.a11y-error` mark, without changing what gets
    accepted, rejected, or said -- only which field a message is attached
    to for presentation.
    """

    message_field_by_text: dict[str, str] = {}
    if hasattr(error, "message_dict"):
        for model_field, messages_for_field in error.message_dict.items():
            template_field = HOMEWORK_ERROR_FIELD_MAP.get(model_field)
            if not template_field:
                continue
            for message in messages_for_field:
                message_field_by_text[message] = template_field

    messages_by_field: dict[str, list[str]] = {}
    for message in error.messages:
        field = _field_for_message(message, message_field_by_text)
        if field:
            messages_by_field.setdefault(field, []).append(message)
    return messages_by_field


def homework_error_summary_items(
    error: ValidationError,
) -> list[dict[str, str | None]]:
    """One error-summary row per message: its text, and the field it names."""

    message_field_by_text: dict[str, str] = {}
    if hasattr(error, "message_dict"):
        for model_field, messages_for_field in error.message_dict.items():
            template_field = HOMEWORK_ERROR_FIELD_MAP.get(model_field)
            if not template_field:
                continue
            for message in messages_for_field:
                message_field_by_text[message] = template_field

    items = []
    for message in error.messages:
        field = _field_for_message(message, message_field_by_text)
        items.append({"field": field, "message": message})
    return items


def homework_validation_context(
    data: HomeworkPostData,
    error: ValidationError,
) -> dict:
    context = homework_detail_build_context_from_post(data)
    context["errors"] = error.messages
    context["error_fields"] = homework_error_fields(error)
    context["error_field_messages"] = homework_error_field_messages(error)
    context["error_summary_items"] = homework_error_summary_items(error)
    return context
