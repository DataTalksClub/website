"""Delivery-context resolution for shared course pages.

A shared module or lesson page is one canonical page for every delivery.  The
context service decides -- never infers -- which cohort's delivery panel the
reader sees, with this precedence:

1. an explicit ``?cohort=<identifier>`` query value (duplicates, empty,
   malformed, or unknown values never fall back);
2. a remembered course preference in the session, when it still resolves to a
   visible cohort of the same course;
3. the reader's sole enrollment, when there is exactly one;
4. chooser state for zero or multiple eligible choices -- never an implicit
   newest-cohort selection.

Context changes only the delivery panel and links.  It never changes the
shared lesson body or the canonical path, and any query/session/authenticated
variant marks the response private so it can never be shared-cached.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from django.http import HttpRequest

from courses.models import Cohort, Course, Enrollment

COHORT_QUERY_KEY = "cohort"
COHORT_SESSION_KEY_PREFIX = "course-context:"
MAX_QUERY_VALUE_CHARS = 80
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

ContextSource = Literal["explicit", "remembered", "sole_enrollment", "chooser", "invalid"]


@dataclass(frozen=True, slots=True)
class DeliveryContext:
    """The resolved delivery context for one request on one course."""

    course: Course
    cohort: Cohort | None
    enrollment: Enrollment | None
    source: ContextSource
    explicit: bool
    has_multiple_enrollments: bool
    invalid_identifier: str | None
    authenticated: bool = False
    authenticated: bool = False

    @property
    def needs_chooser(self) -> bool:
        return self.source == "chooser"

    @property
    def varies_response(self) -> bool:
        """Whether this context makes the response private/uncacheable.

        A clean, context-free anonymous lesson stays public-cache eligible;
        anything that varies the representation -- an explicit query, a
        remembered session preference, or authenticated enrollment state --
        must never be shared.
        """

        if self.invalid_identifier is not None:
            return True
        if self.source in {"explicit", "remembered", "sole_enrollment"}:
            return True
        return self.authenticated


def remembered_cohort_identifier(request: HttpRequest, course: Course) -> str | None:
    """Return the session-remembered cohort identifier for one course."""

    if not hasattr(request, "session"):
        return None
    value = request.session.get(f"{COHORT_SESSION_KEY_PREFIX}{course.slug}")
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value) and len(value) <= MAX_QUERY_VALUE_CHARS:
        return value
    return None


def remember_cohort(request: HttpRequest, course: Course, cohort: Cohort) -> None:
    """Record the reader's delivery preference for one course.

    Session state is keyed by course slug only -- never by email or any
    personal identifier.
    """

    if not hasattr(request, "session"):
        return
    request.session[f"{COHORT_SESSION_KEY_PREFIX}{course.slug}"] = cohort.identifier


def resolve_delivery_context(
    request: HttpRequest,
    course: Course,
    explicit_identifier: str | None = None,
) -> DeliveryContext:
    """Resolve the delivery context for a shared course page request."""

    authenticated = bool(getattr(request.user, "is_authenticated", False))

    authenticated = bool(getattr(request.user, "is_authenticated", False))

    raw_query_value: str | None = None
    if COHORT_QUERY_KEY in request.GET:
        raw_query_value = request.GET[COHORT_QUERY_KEY] or ""
        values = request.GET.getlist(COHORT_QUERY_KEY)
        if len(values) > 1 or len(raw_query_value) > MAX_QUERY_VALUE_CHARS:
            return DeliveryContext(
                course=course,
                cohort=None,
                enrollment=None,
                source="invalid",
                explicit=True,
                has_multiple_enrollments=False,
                invalid_identifier=raw_query_value[:MAX_QUERY_VALUE_CHARS],
                authenticated=authenticated,
            )
        if _IDENTIFIER.fullmatch(raw_query_value) is None:
            return DeliveryContext(
                course=course,
                cohort=None,
                enrollment=None,
                source="invalid",
                explicit=True,
                has_multiple_enrollments=False,
                invalid_identifier=raw_query_value[:MAX_QUERY_VALUE_CHARS],
                authenticated=authenticated,
            )

    explicit_identifier = explicit_identifier or raw_query_value
    enrolled_cohorts: list[Cohort] = []
    enrollment_by_cohort: dict[int, Enrollment] = {}
    has_multiple_enrollments = False
    if request.user.is_authenticated:
        enrollments = list(
            Enrollment.objects.filter(
                student=request.user, course__course=course
            ).select_related("course")
        )
        has_multiple_enrollments = len(enrollments) > 1
        for enrollment in enrollments:
            enrolled_cohorts.append(enrollment.course)
            enrollment_by_cohort[enrollment.course_id] = enrollment

    if explicit_identifier is not None:
        # An explicit cohort path or query always wins -- even when it is
        # closed, archived, or differs from the remembered preference.  An
        # unknown identifier never falls back to anything.
        cohort = Cohort.objects.filter(
            course=course, identifier=explicit_identifier
        ).select_related("course").first()
        if cohort is None:
            return DeliveryContext(
                course=course,
                cohort=None,
                enrollment=None,
                source="invalid",
                explicit=True,
                has_multiple_enrollments=has_multiple_enrollments,
                invalid_identifier=explicit_identifier,
                authenticated=authenticated,
            )
        return DeliveryContext(
            course=course,
            cohort=cohort,
            enrollment=enrollment_by_cohort.get(cohort.pk),
            source="explicit",
            explicit=True,
            has_multiple_enrollments=has_multiple_enrollments,
            invalid_identifier=None,
            authenticated=authenticated,
        )

    remembered = remembered_cohort_identifier(request, course)
    if remembered is not None:
        cohort = Cohort.objects.filter(course=course, identifier=remembered, visible=True).first()
        if cohort is not None:
            return DeliveryContext(
                course=course,
                cohort=cohort,
                enrollment=enrollment_by_cohort.get(cohort.pk),
                source="remembered",
                explicit=False,
                has_multiple_enrollments=has_multiple_enrollments,
                invalid_identifier=None,
                authenticated=authenticated,
            )

    if len(enrolled_cohorts) == 1:
        cohort = enrolled_cohorts[0]
        return DeliveryContext(
            course=course,
            cohort=cohort,
            enrollment=enrollment_by_cohort.get(cohort.pk),
            source="sole_enrollment",
            explicit=False,
            has_multiple_enrollments=False,
            invalid_identifier=None,
            authenticated=authenticated,
        )

    return DeliveryContext(
        course=course,
        cohort=None,
        enrollment=None,
        source="chooser",
        explicit=False,
        has_multiple_enrollments=has_multiple_enrollments,
        invalid_identifier=None,
        authenticated=authenticated,
    )


def delivery_choices(course: Course, request: HttpRequest) -> list[Cohort]:
    """The visible cohorts a chooser may offer for one course."""

    choices = list(
        Cohort.objects.filter(course=course, visible=True).order_by("identifier")
    )
    if request.user.is_authenticated:
        enrolled = set(
            Enrollment.objects.filter(
                student=request.user, course__course=course
            ).values_list("course_id", flat=True)
        )
        # Enrolled cohorts lead the chooser: they are the reader's own
        # deliveries, in identifier order.
        choices.sort(key=lambda cohort: cohort.pk not in enrolled)
    return choices


def context_query(cohort: Cohort | None) -> str:
    """The query string that preserves an explicit context in links."""

    if cohort is None:
        return ""
    return f"?{COHORT_QUERY_KEY}={cohort.identifier}"


__all__ = (
    "COHORT_QUERY_KEY",
    "COHORT_SESSION_KEY_PREFIX",
    "DeliveryContext",
    "context_query",
    "delivery_choices",
    "remember_cohort",
    "remembered_cohort_identifier",
    "resolve_delivery_context",
)
