from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.urls import reverse
from django.views.decorators.http import require_safe

from courses.models import Cohort, Course
from courses.views.url_utils import cohort_url_kwargs


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
        return _permanent_redirect(request, reverse("course", kwargs=cohort_url_kwargs(cohort)))

    if Course.objects.filter(slug=course_slug, visible=True).exists():
        return _permanent_redirect(
            request, reverse("course_family", kwargs={"course_slug": course_slug})
        )

    raise Http404("No course matches the given query.")
