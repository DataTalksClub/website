"""Fail-closed HTTP boundary for local content-review browsing."""

from __future__ import annotations

import re
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_SESSION_PATHS = frozenset(
    {
        "/admin/login/",
        "/admin/logout/",
        "/auth/logout/",
    }
)
PROVIDER_AUTH_PREFIXES = (
    "/accounts/github/",
    "/accounts/google/",
    "/accounts/slack/",
)
ISSUE_237_REGISTRATION_PATH = "/courses/register/streaming-systems-lab-spring-2027/"
ISSUE_237_HOMEWORK_PATH = (
    "/courses/data-reliability-zoomcamp/2026/homework/service-level-objectives"
)
ISSUE_237_UNIT_READ_PATH = (
    "/courses/streaming-systems-lab/autumn-2026/modules/module-03/unit-04/read"
)
ISSUE_237_CSRF_FIELD = "csrfmiddlewaretoken"
ISSUE_237_REGISTRATION_FIELDS = frozenset(
    {
        "accepted_newsletter",
        "comment",
        "company_name",
        "country",
        "email",
        "name",
        "role",
    }
)
ISSUE_237_HOMEWORK_FIELDS = frozenset(
    {
        "faq_contribution_url",
        "homework_url",
        "learning_in_public_links[]",
        "problems_comments",
        "time_spent_homework",
        "time_spent_lectures",
    }
)
ISSUE_237_COMPLAINT_FIELDS = frozenset({"description", "issue_type"})
ISSUE_237_UNIT_READ_VALUES = frozenset({"0", "1", "issue-237-invalid"})


def _plain_response(message: str, *, status: int) -> HttpResponse:
    response = HttpResponse(message, status=status, content_type="text/plain; charset=utf-8")
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _review_post_fields(request: HttpRequest) -> dict[str, list[str]]:
    """Return posted review fields without the ordinary CSRF transport value."""

    return {key: values for key, values in request.POST.lists() if key != ISSUE_237_CSRF_FIELD}


def _blank_review_post(
    request: HttpRequest,
    *,
    fields: frozenset[str],
    allow_answer_fields: bool = False,
) -> bool:
    posted = _review_post_fields(request)
    for key, values in posted.items():
        is_answer = allow_answer_fields and re.fullmatch(r"answer_[1-9][0-9]*", key)
        if key not in fields and not is_answer:
            return False
        if any(value.strip() for value in values):
            return False
    return True


def _issue_237_review_mutation(request: HttpRequest) -> bool:
    """Allow only bounded synthetic interactions in the opt-in design-review runtime."""

    if not getattr(settings, "ISSUE_237_SYNTHETIC_INTERACTIONS_ENABLED", False):
        return False
    if request.method != "POST":
        return False

    path = request.path
    if path == ISSUE_237_UNIT_READ_PATH:
        posted = _review_post_fields(request)
        if set(posted) - {"is_read"}:
            return False
        values = posted.get("is_read", [])
        return not values or (len(values) == 1 and values[0] in ISSUE_237_UNIT_READ_VALUES)
    if path == ISSUE_237_REGISTRATION_PATH:
        return _blank_review_post(
            request,
            fields=ISSUE_237_REGISTRATION_FIELDS,
        )
    if path == ISSUE_237_HOMEWORK_PATH:
        if not _blank_review_post(
            request,
            fields=ISSUE_237_HOMEWORK_FIELDS,
            allow_answer_fields=True,
        ):
            return False
        # A blank homework is otherwise accepted by the adopted platform and
        # schedules delivery callbacks.  In this isolated design-review runtime,
        # add one invalid preview value so the real view's atomic block rolls back
        # and renders its existing validation callout without durable effects.
        post = request.POST.copy()
        post["time_spent_homework"] = "issue-237-invalid-hours"
        request.POST = post
        return True
    complaint_path = getattr(settings, "ISSUE_237_SYNTHETIC_COMPLAINT_PATH", "")
    if complaint_path and path == complaint_path:
        return _blank_review_post(
            request,
            fields=ISSUE_237_COMPLAINT_FIELDS,
        )
    return False


class LocalReviewNoNetworkMiddleware:
    """Stop provider-backed and mutating requests before copied views run."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED", False):
            return self.get_response(request)

        path = request.path
        if path == "/studio/courses/cloudwatch/":
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated or not user.is_staff:
                return self.get_response(request)
            return _plain_response(
                "CloudWatch is disabled in local content review.",
                status=200,
            )
        if path.startswith(PROVIDER_AUTH_PREFIXES):
            return _plain_response(
                "External sign-in providers are disabled in local content review.",
                status=403,
            )
        if _issue_237_review_mutation(request):
            return self.get_response(request)
        if request.method not in SAFE_METHODS and path not in LOCAL_SESSION_PATHS:
            return _plain_response(
                "Mutating requests are disabled in local content review.",
                status=403,
            )
        return self.get_response(request)
