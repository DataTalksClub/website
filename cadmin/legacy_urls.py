from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.urls import URLPattern, path

from .urls import ROUTE_DEFINITIONS
from .views.helpers import staff_required


def _legacy_redirect(destination_name: str) -> Callable[..., HttpResponse]:
    @staff_required
    def redirect_to_studio(request: HttpRequest, **kwargs: object) -> HttpResponse:
        destination = resolve_url(destination_name, **kwargs)
        query_string = request.META.get("QUERY_STRING", "")
        if query_string:
            destination = f"{destination}?{query_string}"
        preserve_request = request.method not in {"GET", "HEAD"}
        return HttpResponseRedirect(
            destination,
            preserve_request=preserve_request,
        )

    return redirect_to_studio


def _patterns() -> list[URLPattern]:
    return [
        path(
            route,
            _legacy_redirect(f"studio_courses_{name}"),
            name=f"legacy_studio_courses_{name}",
        )
        for route, _view, name in ROUTE_DEFINITIONS
    ]


legacy_course_list_redirect = _legacy_redirect("studio_courses_course_list")
urlpatterns = _patterns()
