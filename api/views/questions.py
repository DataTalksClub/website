from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from courses.models.cohort import Cohort
from courses.models.homework import Homework, Question

from api.safety import (
    require_staff_token,
)
from api.utils import parse_json_body, require_methods

from .question_mutations import (
    question_delete_response,
    question_patch_response,
    questions_create_response,
)
from .question_serializers import question_to_dict


def _get_course_and_homework(course_slug, homework_id):
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(Homework, course=course, id=homework_id)
    return course, homework


def _questions_list_response(homework):
    questions = (
        Question.objects.filter(homework=homework)
        .annotate(answer_count=Count("answer"))
        .order_by("id")
    )
    question_records = []
    for question in questions:
        question_record = question_to_dict(question)
        question_records.append(question_record)

    payload = {
        "homework_id": homework.id,
        "homework_title": homework.title,
        "questions": question_records,
    }
    response = JsonResponse(payload)
    return response


@token_required
@csrf_exempt
@require_methods("GET", "POST")
def questions_view(request, course_slug, homework_id):
    """
    GET /api/courses/<slug>/homeworks/<id>/questions/ - List questions.
    POST /api/courses/<slug>/homeworks/<id>/questions/ - Add questions.
    """
    # Read as well as write: a question record carries `correct_answer`, so
    # listing the questions of a homework is handing out the answer key.  A
    # `token_required` caller is only "some active account", which includes
    # every learner sitting the homework.
    staff_error = require_staff_token(request)
    if staff_error:
        return staff_error

    _, homework = _get_course_and_homework(course_slug, homework_id)

    if request.method == "GET":
        return _questions_list_response(homework)

    data, err = parse_json_body(request)
    if err:
        return err

    return questions_create_response(homework, data)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
def question_detail_view(request, course_slug, homework_id, question_id):
    """
    GET /api/courses/<slug>/homeworks/<id>/questions/<id>/ - Question detail.
    PATCH /api/courses/<slug>/homeworks/<id>/questions/<id>/ - Update question.
    DELETE /api/courses/<slug>/homeworks/<id>/questions/<id>/ - Delete question.
    """
    # `question_to_dict` includes `correct_answer`, so the read is as
    # privileged as the writes below it.
    staff_error = require_staff_token(request)
    if staff_error:
        return staff_error

    _, homework = _get_course_and_homework(course_slug, homework_id)
    question = get_object_or_404(Question, homework=homework, id=question_id)

    if request.method == "GET":
        question_record = question_to_dict(question)
        response = JsonResponse(question_record)
        return response

    if request.method == "DELETE":
        return question_delete_response(question)

    return question_patch_response(question, request)
