"""Test-only URL surface for development SEO and preview policy."""

from pathlib import Path

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.template import Context, Engine
from django.urls import include, path
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from core import views as core_views
from core.preview import staff_preview_required

_TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"
_ENGINE = Engine(
    dirs=[str(_TEMPLATE_DIRECTORY)],
    debug=False,
    libraries={"seo": "core.templatetags.seo"},
)


def html_fixture(request: HttpRequest, *, canonical_url: object = None) -> HttpResponse:
    del request
    template = _ENGINE.get_template("core/tests/seo_fixture.html")
    return HttpResponse(
        template.render(Context({"canonical_url": canonical_url, "heading": "SEO policy fixture"})),
        content_type="text/html; charset=utf-8",
    )


def exact_canonical(request: HttpRequest) -> HttpResponse:
    return html_fixture(request, canonical_url="https://datatalks.club/Fixture/Exact.html")


def unmapped(request: HttpRequest) -> HttpResponse:
    return html_fixture(request)


@staff_preview_required
def preview(request: HttpRequest) -> HttpResponse:
    del request
    template = _ENGINE.get_template("core/tests/seo_fixture.html")
    return HttpResponse(
        template.render(Context({"heading": "Private staff preview"})),
        content_type="text/html; charset=utf-8",
    )


def json_fixture(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"fixture": "json"})


def status_fixture(_request: HttpRequest, status: int) -> HttpResponse:
    return HttpResponse(f"Safe {status}", status=status, content_type="text/plain")


def redirect_fixture(_request: HttpRequest) -> HttpResponse:
    response = HttpResponse(status=302)
    response["Location"] = "/Fixture/Unmapped.html"
    return response


def conflicting_header(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("Conflict", headers={"X-Robots-Tag": "index, follow"})


def conflicting_private_cache(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        "Private",
        headers={"Cache-Control": "public, max-age=3600, s-maxage=3600"},
    )


def raised_error(_request: HttpRequest) -> HttpResponse:
    raise RuntimeError("safe fixture failure")


@require_POST
def post_only(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("posted")


@csrf_protect
def csrf_failure(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("posted")


def static_fixture(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("body{}", content_type="text/css")


urlpatterns = [
    path("Fixture/Exact.html", exact_canonical, name="seo-exact"),
    path("Fixture/Unmapped.html", unmapped, name="seo-unmapped"),
    path("private/preview/", preview, name="seo-preview"),
    path("fixture/json", json_fixture, name="seo-json"),
    path("fixture/redirect", redirect_fixture, name="seo-redirect"),
    path("fixture/400", lambda request: status_fixture(request, 400), name="seo-400"),
    path("fixture/401", lambda request: status_fixture(request, 401), name="seo-401"),
    path("fixture/403", lambda request: status_fixture(request, 403), name="seo-403"),
    path("fixture/conflict", conflicting_header, name="seo-conflict"),
    path("api/conflicting-cache", conflicting_private_cache, name="seo-private-cache"),
    path("fixture/error", raised_error, name="seo-error"),
    path("fixture/method", post_only, name="seo-method"),
    path("fixture/csrf", csrf_failure, name="seo-csrf"),
    path("fixture/asset.css", static_fixture, name="seo-static"),
    path("robots.txt", core_views.robots, name="development-robots"),
    path("sitemap.xml", core_views.sitemap, name="development-sitemap"),
    path("health/live", core_views.liveness, name="health-live"),
    path("health/ready", core_views.readiness, name="health-ready"),
    path("studio/", include("studio.urls")),
    path("api/v1/admin/", include("website.admin_api_urls")),
    path("accounts/", include("accounts.urls")),
    path("", include("courses.urls")),
]


def handler404(_request: HttpRequest, exception: Exception) -> HttpResponse:
    del exception
    return HttpResponse("Safe fixture not found", status=404, content_type="text/plain")


def handler500(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("Safe server error", status=500, content_type="text/plain")
