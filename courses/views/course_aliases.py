from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_safe

from courses.models import Cohort


@require_safe
def legacy_course_redirect(request: HttpRequest, course_slug: str) -> HttpResponse:
    get_object_or_404(Cohort.objects.only("pk"), slug=course_slug)
    target = reverse("course", kwargs={"course_slug": course_slug})
    query = request.META.get("QUERY_STRING", "")
    if query:
        target = f"{target}?{query}"
    return HttpResponsePermanentRedirect(target)
