from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from courses.homework_correct_answers import (
    clear_correct_answers,
    fill_correct_answers,
)
from courses.models.cohort import Cohort
from courses.models.homework import Homework, HomeworkState, Question
from courses.scoring_jobs import (
    enqueue_homework_notify,
    enqueue_homework_score,
)
from studio_courses.deadline_extension import extend_deadlines
from studio_courses.views.homework_submission_edit import (
    homework_submission_edit_response,
)
from studio_courses.views.homework_submission_list import (
    HomeworkSubmissionsContextData,
    homework_submissions_context,
    homework_submissions_queryset,
)

from .helpers import (
    paginate_queryset,
    redirect_after_action,
    staff_required,
)


@staff_required
def homework_score(request, course_slug, homework_slug):
    """Queue homework scoring on the background worker; returns immediately."""
    if request.method != "POST":
        response = redirect("studio_courses_course", course_slug=course_slug)
        return response
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )

    with transaction.atomic():
        enqueue_homework_score(homework.id)
    messages.success(
        request,
        f"Scoring queued for {homework.title}. Track progress in Admin > Job intents.",
    )

    return redirect_after_action(
        request, "studio_courses_course", course_slug=course_slug
    )


@staff_required
def homework_extend_deadline(request, course_slug, homework_slug):
    """Push the homework due date forward by a fixed amount."""
    if request.method != "POST":
        return redirect("studio_courses_course", course_slug=course_slug)
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )

    if homework.state != HomeworkState.OPEN.value:
        messages.warning(
            request,
            "Only open homework can have its deadline extended.",
        )
        return redirect_after_action(
            request, "studio_courses_course", course_slug=course_slug
        )

    return extend_deadlines(
        request,
        homework,
        ["due_date"],
        f"the deadline for {homework.title}",
        course_slug,
    )


@staff_required
def homework_rescore(request, course_slug, homework_slug):
    """Queue a re-score of an already scored homework.

    Resets the homework to OPEN so the normal scoring logic applies,
    then queues the work; the request returns immediately.
    """
    if request.method != "POST":
        return redirect("studio_courses_course", course_slug=course_slug)
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )

    if not homework.is_scored():
        messages.warning(
            request,
            f"{homework.title} is not scored yet. "
            "Use Score submissions instead.",
        )
        return redirect_after_action(
            request, "studio_courses_course", course_slug=course_slug
        )

    with transaction.atomic():
        homework.state = HomeworkState.OPEN.value
        homework.save(update_fields=["state"])
        enqueue_homework_score(homework.id, force=True)

    messages.success(
        request,
        f"Re-scoring queued for {homework.title}. Track progress in Admin > Job intents.",
    )

    return redirect_after_action(
        request, "studio_courses_course", course_slug=course_slug
    )


@staff_required
def homework_notify_scores(request, course_slug, homework_slug):
    """Queue score notification emails; returns immediately."""
    if request.method != "POST":
        response = redirect("studio_courses_course", course_slug=course_slug)
        return response
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )

    if not homework.is_scored():
        messages.warning(
            request,
            f"{homework.title} is not scored yet. "
            "Score it before notifying students.",
        )
        return redirect_after_action(
            request, "studio_courses_course", course_slug=course_slug
        )

    with transaction.atomic():
        enqueue_homework_notify(homework.id)
    messages.success(
        request,
        f"Score notifications for {homework.title} queued. Track progress in Admin > Job intents.",
    )

    return redirect_after_action(
        request, "studio_courses_course", course_slug=course_slug
    )


@staff_required
def homework_set_correct_answers(request, course_slug, homework_slug):
    """Set correct answers to most popular for a homework"""
    if request.method != "POST":
        response = redirect("studio_courses_course", course_slug=course_slug)
        return response
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )

    fill_correct_answers(homework)

    messages.success(
        request,
        f"Correct answers for {homework.title} set to most popular",
    )

    return redirect_after_action(
        request, "studio_courses_course", course_slug=course_slug
    )


@staff_required
def homework_clear_correct_answers(request, course_slug, homework_slug):
    """Clear correct answers for a homework"""
    if request.method != "POST":
        response = redirect("studio_courses_course", course_slug=course_slug)
        return response
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )

    updated_count = clear_correct_answers(homework)

    messages.success(
        request,
        f"Correct answers for {updated_count} questions in {homework.title} cleared",
    )

    return redirect_after_action(
        request, "studio_courses_course", course_slug=course_slug
    )


@staff_required
def homework_submissions(request, course_slug, homework_slug):
    """View all submissions for a homework"""
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )
    raw_search_query = request.GET.get("q", "")
    search_query = raw_search_query.strip()
    submissions = homework_submissions_queryset(homework, search_query)
    submissions_page = paginate_queryset(request, submissions)
    context_data = HomeworkSubmissionsContextData(
        request=request,
        course=course,
        homework=homework,
        submissions_page=submissions_page,
        search_query=search_query,
    )
    context = homework_submissions_context(context_data)
    questions = list(
        Question.objects.filter(homework=homework).order_by("id")
    )
    context["questions"] = questions
    response = render(request, "studio_courses/homework_submissions.html", context)
    return response


@staff_required
def homework_save_answers(request, course_slug, homework_slug):
    """Save correct answers from the submissions page"""
    if request.method != "POST":
        return redirect("studio_courses_course", course_slug=course_slug)
    course = get_object_or_404(Cohort, slug=course_slug)
    homework = get_object_or_404(
        Homework, course=course, slug=homework_slug
    )
    questions = list(
        Question.objects.filter(homework=homework).order_by("id")
    )
    for question in questions:
        field_name = f"correct_answer_{question.id}"
        if question.has_choice_answers():
            indices = request.POST.getlist(field_name)
            correct_answer = ",".join(indices)
        else:
            correct_answer = request.POST.get(field_name, "")
        answer_type = request.POST.get(
            f"answer_type_{question.id}", ""
        )
        question.correct_answer = correct_answer
        question.answer_type = answer_type or None
        question.save(update_fields=["correct_answer", "answer_type"])

    messages.success(
        request,
        f"Correct answers for {homework.title} updated.",
    )
    return redirect_after_action(
        request, "studio_courses_course", course_slug=course_slug
    )


@staff_required
def homework_submission_edit(
    request, course_slug, homework_slug, submission_id
):
    """Edit a homework submission"""
    response = homework_submission_edit_response(
        request, course_slug, homework_slug, submission_id
    )
    return response
