"""Synthetic public surface used only to prove generic compatibility mechanics."""

from __future__ import annotations

import base64
import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path, re_path


def _redirect(location: str, status: int = 301) -> HttpResponse:
    response = HttpResponse(status=status)
    response["Location"] = location
    return response


def fixture_page(_request: HttpRequest) -> HttpResponse:
    families = [
        "Event",
        "BlogPosting",
        "PodcastEpisode",
        "FAQPage",
        "Question",
        "Answer",
        "BreadcrumbList",
        "ListItem",
        "Organization",
        "WebSite",
        "SearchAction",
    ]
    structured = json.dumps(
        [{"@type": family, "@id": f"https://datatalks.club/schema/{family}"} for family in families]
    )
    return HttpResponse(
        f"""<!doctype html>
<html lang="en"><head>
<title>Compatibility fixture</title>
<meta name="description" content="Server-rendered parity fixture">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://datatalks.club/fixture/">
<meta property="og:image" content="https://datatalks.club/assets/logo.bin">
<meta name="twitter:image" content="https://datatalks.club/assets/logo.bin">
<script type="application/ld+json">{structured}</script>
</head><body><main>
<h1>Compatibility fixture</h1>
<p>Meaningful server-rendered body for crawler parity.</p>
<a href="/fixture/#Caf%C3%A9">Exact fragment</a>
<a href="/docs/Exact/">Exact docs path</a>
<a href="https://external.example/resource?utm_source=A+B&amp;x=%20&amp;x=">External</a>
<form action="/submit/?next=%2Fok"><button>Submit</button></form>
<img src="/assets/logo.bin" alt="Fixture logo">
<div id="Café">Fragment target</div>
</main></body></html>""",
        content_type="text/html; charset=utf-8",
        headers={"Content-Language": "en", "Last-Modified": "Sat, 08 Aug 2026 00:00:00 GMT"},
    )


def plain_page(request: HttpRequest, name: str = "page") -> HttpResponse:
    canonical = f"https://datatalks.club{request.path}"
    return HttpResponse(
        f'<html lang="en"><head><title>{name}</title><link rel="canonical" '
        f'href="{canonical}"></head><body><main><h1>{name}</h1>'
        f"<p>Visible {name}</p></main></body></html>",
        content_type="text/html",
    )


def asset(_request: HttpRequest) -> HttpResponse:
    # A deterministic, valid 1x1 PNG proves browser decode as well as HTTP parity.
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
        "AAAAASUVORK5CYII="
    )
    return HttpResponse(image, content_type="image/png")


def submit(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(status=405, headers={"Allow": "POST"})


def gone(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("Gone", status=410, content_type="text/plain")


def server_error(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("Error", status=500, content_type="text/plain")


def soft_404(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        "<html><head><title>Page not found</title></head><body><h1>404 error</h1></body></html>",
        content_type="text/html",
    )


def staging_canonical(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        '<html><head><title>Staging</title><link rel="canonical" '
        'href="https://web.dtcdev.click/staging/"></head><body><main><h1>Staging</h1>'
        "<p>Visible body</p></main></body></html>",
        content_type="text/html",
    )


def js_only(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        '<html><head><title>Shell</title></head><body><main id="app"></main>'
        '<script>document.querySelector("#app").textContent="Client-only body"</script>'
        "</body></html>",
        content_type="text/html",
    )


def robots(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        "User-agent: *\nAllow: /\nSitemap: https://datatalks.club/sitemap.xml\n",
        content_type="text/plain",
    )


def sitemap(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://datatalks.club/fixture/</loc><lastmod>2026-08-08</lastmod></url>"
        "</urlset>",
        content_type="application/xml",
    )


def echo(request: HttpRequest, value: str) -> JsonResponse:
    return JsonResponse(
        {
            "path": request.path,
            "path_info": request.path_info,
            "query": request.META.get("QUERY_STRING", ""),
            "raw_uri": request.META.get("RAW_URI", ""),
            "request_uri": request.META.get("REQUEST_URI", ""),
            "value": value,
        }
    )


urlpatterns = [
    path("fixture/", fixture_page),
    path("docs/Exact/", lambda request: plain_page(request, "Docs Exact")),
    path("faq/course.html", lambda request: plain_page(request, "FAQ")),
    path("podwiki/search/", lambda request: plain_page(request, "Podwiki")),
    path("courses/course/", lambda request: plain_page(request, "Course")),
    path("assets/logo.bin", asset),
    path("submit/", submit),
    path("legacy", lambda _request: _redirect("/fixture/")),
    path("fragment-redirect", lambda _request: _redirect("/fixture/#Caf%C3%A9")),
    path("legacy-308", lambda _request: _redirect("/fixture/", 308)),
    path("chain-a", lambda _request: _redirect("/chain-b")),
    path("chain-b", lambda _request: _redirect("/fixture/")),
    path("loop-a", lambda _request: _redirect("/loop-b")),
    path("loop-b", lambda _request: _redirect("/loop-a")),
    path("gone", gone),
    path("server-error", server_error),
    path("soft-404", soft_404),
    path("staging/", staging_canonical),
    path("js-only/", js_only),
    path("robots.txt", robots),
    path("sitemap.xml", sitemap),
    re_path(r"^echo/(?P<value>.*)$", echo),
]


def handler404(_request: HttpRequest, exception: Exception) -> HttpResponse:
    del exception
    return HttpResponse("Fixture not found", status=404, content_type="text/plain")
