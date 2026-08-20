from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_safe

from courses.models import Cohort
from courses.views.url_utils import cohort_url_kwargs


@require_safe
def legacy_course_redirect(request: HttpRequest, course_slug: str) -> HttpResponse:
    cohort = get_object_or_404(Cohort, slug=course_slug)
    target = reverse("course", kwargs=cohort_url_kwargs(cohort))
    query = request.META.get("QUERY_STRING", "")
    if query:
        target = f"{target}?{query}"
    return HttpResponsePermanentRedirect(target)
