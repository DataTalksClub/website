from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.urls import reverse
from django.views.decorators.http import require_safe

from content.public_views import permanent_public_redirect
from courses.models import Cohort, Course, SharedLesson, SharedModule
from courses.views.url_utils import canonical_cohort_url_kwargs


def _permanent_redirect(request: HttpRequest, target: str) -> HttpResponse:
    query = request.META.get("QUERY_STRING", "")
    if query:
        target = f"{target}?{query}"
    return HttpResponsePermanentRedirect(target)


@require_safe
def legacy_course_redirect(request: HttpRequest, course_slug: str) -> HttpResponse:
    """Answer the one-segment slashed course alias.

    The route this view serves is ``<slug:course_slug>/``, so two different
    shapes reach it:

    * a legacy edition slug -- ``/courses/de-zoomcamp-2025/`` and the root
      ``/de-zoomcamp-2025/`` -- which is why the alias exists, and
    * a course *family* slug carrying a trailing slash, ``/courses/ml-zoomcamp/``,
      which is the slashed form of the canonical slashless family page.

    The family shape used to be looked up as a cohort slug and answered
    "No Cohort matches the given query", so every family page 404ed with a
    trailing slash.  Cohorts keep priority, which leaves the legacy edition
    redirect exactly as it was; the family fallback applies the same
    slashless-canonical convention as ``course-list-slash-redirect`` in
    ``website/urls.py`` one path segment down.
    """

    cohort = Cohort.objects.filter(slug=course_slug).first()
    if cohort is not None:
        return _permanent_redirect(request, reverse("cohort", kwargs=canonical_cohort_url_kwargs(cohort)))

    if Course.objects.filter(slug=course_slug, visible=True).exists():
        return _permanent_redirect(
            request, reverse("course_family", kwargs={"course_slug": course_slug})
        )

    raise Http404("No course matches the given query.")


@require_safe
def course_page_slash_redirect(
    request: HttpRequest, course_slug: str, page_slug: str
) -> HttpResponse:
    """Answer ``/courses/<family>/<segment>/``, the slashed spelling of a two-segment page.

    The canonical ``/courses/<family>/<segment>`` renders either a cohort of the family or
    a published shared module of a visible family
    (``shared_course.dispatch_two_segment_path``).  The slashed spelling redirects there in
    one hop only when the canonical names one of those; any other segment is a real 404 --
    no target is ever inferred.
    """

    course = Course.objects.filter(slug=course_slug).first()
    if course is None:
        raise Http404("No course matches the given query.")
    names_page = Cohort.objects.filter(course=course, identifier=page_slug).exists() or (
        course.visible
        and SharedModule.objects.filter(
            curriculum__course=course, slug=page_slug, published=True
        ).exists()
    )
    if not names_page:
        raise Http404("No cohort or shared module matches the given query.")
    return permanent_public_redirect(
        request,
        target=reverse(
            "cohort", kwargs={"course_slug": course.slug, "cohort_identifier": page_slug}
        ),
    )


@require_safe
def shared_lesson_slash_redirect(
    request: HttpRequest, course_slug: str, module_slug: str, lesson_slug: str
) -> HttpResponse:
    """Answer ``/courses/<family>/<module>/<lesson>/`` for a published shared lesson."""

    lesson_exists = SharedLesson.objects.filter(
        module__curriculum__course__slug=course_slug,
        module__curriculum__course__visible=True,
        module__slug=module_slug,
        slug=lesson_slug,
        published=True,
    ).exists()
    if not lesson_exists:
        raise Http404("No shared lesson matches the given query.")
    return permanent_public_redirect(
        request,
        target=reverse(
            "shared_lesson",
            kwargs={
                "course_slug": course_slug,
                "module_slug": module_slug,
                "lesson_slug": lesson_slug,
            },
        ),
    )
