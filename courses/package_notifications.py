"""Score, peer-review and submission notifications through the package mail.

D1.2ca moved the last Datamailer-sent mail here. Each recipient gets one
durable ``EmailDelivery`` with a replay-safe key derived from the business
objects, so a Studio retry of the same score run never duplicates mail.
The context assembly ports the pure builders the Datamailer payloads used.
"""

from __future__ import annotations

from accounts.services.timezones import format_deadline_for_user
from course_management.public_urls import (
    cohort_route_kwargs,
    public_route_url,
    public_url,
)

from courses.models.project import PeerReview

SUBMISSION_RESULTS_CATEGORY = "submission-results"

ASSIGNED_REVIEWS_ATTR = "assigned_reviews"


def _normalized_email(user) -> str:
    return (user.email or "").strip().lower()


def latest_submissions_per_student(submissions):
    """Latest submission per student, the Datamailer member-selection rule."""

    seen_students = set()
    latest = []
    for submission in submissions.order_by("student_id", "-submitted_at", "-id"):
        if submission.student_id in seen_students:
            continue
        seen_students.add(submission.student_id)
        latest.append(submission)
    return latest


def score_notification_urls(course, assignment, route_name, slug_kwarg):
    assignment_kwargs = {
        **cohort_route_kwargs(course),
        slug_kwarg: assignment.slug,
    }
    assignment_url = public_route_url(route_name, assignment_kwargs)
    course_kwargs = cohort_route_kwargs(course)
    return {
        "course_url": public_route_url("cohort", course_kwargs),
        "assignment_url": assignment_url,
        "leaderboard_url": public_route_url("cohort_leaderboard", course_kwargs),
        "profile_url": public_route_url("account_settings"),
    }


def score_notification_footer(course, assignment, profile_url):
    return {
        "notification_footer": (
            f"You are receiving this because you submitted {assignment.title} "
            f"for {course.title} and homework/project submission emails "
            "are enabled in your profile."
        ),
        "notification_footer_text": (
            "If you don't want to receive homework/project submission "
            "and score emails, turn off homework and project submission "
            f"emails in your profile: {profile_url}"
        ),
    }


def _send_notification(*, purpose, submission, context, key_suffix):
    from course_management.package_mail import send_package_mail

    email = _normalized_email(submission.student)
    if not email:
        return None
    return send_package_mail(
        purpose=purpose,
        to=email,
        context=context,
        idempotency_key=f"{key_suffix}:{submission.pk}",
        category=SUBMISSION_RESULTS_CATEGORY,
        user=submission.student,
    )


def homework_score_shared_context(homework) -> dict:
    course = homework.course
    urls = score_notification_urls(
        course,
        homework,
        "cohort_homework",
        "homework_slug",
    )
    homework_url = urls["assignment_url"]
    context = {
        "course_slug": course.slug,
        "course_title": course.title,
        "homework_slug": homework.slug,
        "homework_title": homework.title,
        "course_url": urls["course_url"],
        "homework_url": homework_url,
        "scores_url": homework_url,
        "leaderboard_url": urls["leaderboard_url"],
        "profile_url": urls["profile_url"],
    }
    context.update(score_notification_footer(course, homework, urls["profile_url"]))
    return context


def send_homework_score_notification(homework) -> int:
    context = homework_score_shared_context(homework)
    sent = 0
    submissions = homework.submission_set.select_related(
        "student", "homework__course",
    )
    for submission in latest_submissions_per_student(submissions):
        if submission.total_score is None:
            continue
        learner_context = context | {
            "questions_score": submission.questions_score,
            "learning_in_public_score": submission.learning_in_public_score,
            "faq_score": submission.faq_score,
            "total_score": submission.total_score,
        }
        if _send_notification(
            purpose="homework-score-notification",
            submission=submission,
            context=learner_context,
            key_suffix=f"homework-score:{homework.course.slug}:{homework.slug}",
        ):
            sent += 1
    return sent


def project_score_shared_context(project) -> dict:
    course = project.course
    urls = score_notification_urls(
        course,
        project,
        "cohort_project",
        "project_slug",
    )
    project_results_url = public_route_url(
        "cohort_project_results",
        cohort_route_kwargs(course) | {"project_slug": project.slug},
    )
    context = {
        "course_slug": course.slug,
        "course_title": course.title,
        "project_slug": project.slug,
        "project_title": project.title,
        "course_url": urls["course_url"],
        "project_url": urls["assignment_url"],
        "project_results_url": project_results_url,
        "scores_url": project_results_url,
        "leaderboard_url": urls["leaderboard_url"],
        "profile_url": urls["profile_url"],
    }
    context.update(score_notification_footer(course, project, urls["profile_url"]))
    return context


def send_project_score_notification(project) -> int:
    context = project_score_shared_context(project)
    sent = 0
    submissions = project.projectsubmission_set.select_related(
        "student", "project__course",
    )
    for submission in latest_submissions_per_student(submissions):
        if submission.total_score is None:
            continue
        learner_context = context | {
            "project_score": submission.project_score,
            "project_learning_in_public_score": (
                submission.project_learning_in_public_score
            ),
            "project_faq_score": submission.project_faq_score,
            "peer_review_score": submission.peer_review_score,
            "peer_review_learning_in_public_score": (
                submission.peer_review_learning_in_public_score
            ),
            "total_score": submission.total_score,
            "github_link": getattr(submission, "github_link", "") or "",
            "commit_id": getattr(submission, "commit_id", "") or "",
        }
        if _send_notification(
            purpose="project-score-notification",
            submission=submission,
            context=learner_context,
            key_suffix=f"project-score:{project.course.slug}:{project.slug}",
        ):
            sent += 1
    return sent


def _assigned_reviews(submission):
    prefetched = getattr(submission, ASSIGNED_REVIEWS_ATTR, None)
    if prefetched is not None:
        return prefetched
    return (
        submission.reviewers.filter(optional=False)
        .select_related("submission_under_evaluation")
        .order_by("id")
    )


def assigned_reviews_prefetch():
    from django.db.models import Prefetch

    return Prefetch(
        "reviewers",
        queryset=PeerReview.objects.filter(optional=False)
        .select_related("submission_under_evaluation")
        .order_by("id"),
        to_attr=ASSIGNED_REVIEWS_ATTR,
    )


def assigned_review_links(submission, course, project) -> list[dict]:
    items = []
    for review in _assigned_reviews(submission):
        target = review.submission_under_evaluation
        eval_url = public_route_url(
            "cohort_projects_eval_submit",
            cohort_route_kwargs(course)
            | {"project_slug": project.slug, "review_id": review.id},
        )
        items.append(
            {
                "review_id": review.id,
                "eval_url": eval_url,
                "submission_github_link": getattr(target, "github_link", "") or "",
            }
        )
    return items


def peer_review_assignment_context(submission) -> dict:
    project = submission.project
    course = project.course
    reviews = assigned_review_links(submission, course, project)
    evaluations_url = public_route_url(
        "cohort_projects_eval",
        cohort_route_kwargs(course) | {"project_slug": project.slug},
    )
    profile_url = public_route_url("account_settings")
    deadline = format_deadline_for_user(
        project.peer_review_due_date,
        submission.student,
    )
    num_peers = project.number_of_peers_to_evaluate
    context = {
        "course_slug": course.slug,
        "course_title": course.title,
        "project_slug": project.slug,
        "project_title": project.title,
        "submission_id": submission.pk,
        "submitted_at": (
            submission.submitted_at.isoformat() if submission.submitted_at else ""
        ),
        "deadline_summary": deadline["deadline_summary"],
        "number_of_peers_to_evaluate": num_peers,
        "assigned_reviews": reviews,
        "assigned_reviews_count": len(reviews),
        "evaluations_url": evaluations_url,
        "profile_url": profile_url,
        "email_subject": f"Peer review is open: {project.title}",
        "intro_text": (
            f"Thanks for submitting {project.title} in {course.title}. "
            f"Peer review is now open - you have {num_peers} projects to "
            "evaluate before the deadline."
        ),
        "notification_footer": (
            f"You are receiving this because you submitted {project.title} "
            f"in {course.title} and homework/project submission emails are "
            "enabled in your profile."
        ),
        "notification_footer_text": (
            "If you don't want to receive homework/project submission "
            f"emails, turn them off in your profile: {profile_url}"
        ),
    }
    return context


def send_peer_review_assignment_notification(project) -> int:
    sent = 0
    submissions = (
        project.projectsubmission_set.select_related(
            "student", "project__course",
        )
        .prefetch_related(assigned_reviews_prefetch())
    )
    for submission in latest_submissions_per_student(submissions):
        if _send_notification(
            purpose="peer-review-assignment",
            submission=submission,
            context=peer_review_assignment_context(submission),
            key_suffix=f"peer-review-assignment:{project.course.slug}:{project.slug}",
        ):
            sent += 1
    return sent
