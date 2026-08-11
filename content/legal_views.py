"""Source-reviewed public legal pages for DataTalks.Club."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

LEGAL_PAGE_CONTEXT = {
    "terms": {
        "title": "Terms of Service",
        "description": (
            "Terms for using the free DataTalks.Club community, website, events, and courses."
        ),
        "path": "/terms",
    },
    "privacy": {
        "title": "Privacy Policy",
        "description": (
            "How DataTalks.Club handles account, community, course, event, and website data."
        ),
        "path": "/privacy",
    },
    "impressum": {
        "title": "Impressum",
        "description": "Legal notice and operator information for DataTalks.Club.",
        "path": "/impressum",
    },
}


def _legal_page(request: HttpRequest, *, page: str) -> HttpResponse:
    definition = LEGAL_PAGE_CONTEXT[page]
    path = definition["path"]
    return render(
        request,
        f"public/legal/{page}.html",
        {
            "canonical_url": f"https://datatalks.club{path}",
            "seo_title": f"{definition['title']} · DataTalks.Club",
            "seo_description": definition["description"],
            "legal_robots": "noindex,nofollow" if settings.NOINDEX else "index,follow",
        },
    )


@require_safe
def terms(request: HttpRequest) -> HttpResponse:
    return _legal_page(request, page="terms")


@require_safe
def privacy(request: HttpRequest) -> HttpResponse:
    return _legal_page(request, page="privacy")


@require_safe
def impressum(request: HttpRequest) -> HttpResponse:
    return _legal_page(request, page="impressum")
