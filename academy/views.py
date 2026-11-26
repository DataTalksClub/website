"""The DataTalks.Club Academy pages, calendars and sitemap.

Every response reads the synced Academy rows (``content.catalogue``).  A
database without them -- an empty one, or a content repository that has no
``academy/`` -- answers 404 everywhere: there is no file, build or upstream to
fall back to.

The pages render through the site's own shell (``core/content_page.html``).
Their forms post from the browser straight to the Academy signup backend, so
the responses carry a content security policy that admits that backend's two
endpoints and nothing else beyond the site's own policy.  Confirmation links
carry a token in the query, so no Academy response is ever shared or stored.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import render
from django.views.decorators.http import require_safe

from content import catalogue
from core.middleware import SECURITY_HEADERS

from . import presentation
from .calendars import calendar_for

ACADEMY_ROOT = presentation.ACADEMY_ROOT

#: Layouts the Academy draws with the site's shared templates rather than one of
#: its own: an article reads like a blog post, and the article index lists like
#: the blog index.  Every other layout is ``academy/<layout>.html``.
SHARED_TEMPLATES = {
    "article": "public/article_detail.html",
    "articles": "public/collection_hub.html",
}


def _backend_connect_sources() -> str:
    origin = settings.ACADEMY_API_ORIGIN.rstrip("/")
    return f"{origin}/signup {origin}/confirm"


def academy_csp() -> str:
    """The site policy, plus the signup backend's two endpoints in ``connect-src``."""

    policy = SECURITY_HEADERS["Content-Security-Policy"]
    return policy.replace(
        "connect-src 'self'", f"connect-src 'self' {_backend_connect_sources()}", 1
    )


def _private(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _shared_or_404() -> dict:
    shared = catalogue.academy_shared()
    if not shared:
        raise Http404
    return shared["data"]


@require_safe
def page(request: HttpRequest, path: str = "") -> HttpResponse:
    record = catalogue.academy_page(f"{ACADEMY_ROOT}{path}")
    if record is None:
        raise Http404
    shared = _shared_or_404()
    try:
        context = presentation.page_context(record, shared, catalogue.academy_pages())
    except LookupError as error:
        raise Http404 from error
    layout = record["layout"]
    response = render(request, SHARED_TEMPLATES.get(layout, f"academy/{layout}.html"), context)
    response["Content-Security-Policy"] = academy_csp()
    return _private(response)


@require_safe
def calendar(request: HttpRequest, name: str) -> HttpResponse:
    sessions_page = presentation.sessions_page_of(catalogue.academy_pages())
    if sessions_page is None:
        raise Http404
    data = sessions_page["data"]
    if name == data["series"]["calendar_slug"]:
        selected = data["sessions"]
    else:
        selected = [session for session in data["sessions"] if session["id"] == name]
    if not selected:
        raise Http404
    response = HttpResponse(
        calendar_for(data, selected), content_type="text/calendar; charset=utf-8"
    )
    return _private(response)


@require_safe
def sitemap(request: HttpRequest) -> HttpResponse:
    pages = [
        record
        for record in catalogue.academy_pages()
        if isinstance(record.get("sitemap"), int) and not record.get("noindex")
    ]
    if not pages:
        raise Http404
    pages.sort(key=lambda record: (record["sitemap"], record["public_path"]))
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(
            f"  <url><loc>{escape(presentation.absolute(record['public_path']))}</loc></url>"
            for record in pages
        )
        + "\n</urlset>\n"
    )
    return _private(HttpResponse(body, content_type="application/xml; charset=utf-8"))


@require_safe
def layoff_proof(request: HttpRequest) -> HttpResponse:
    """The series' first address; old links keep working and keep their campaign."""

    target = presentation.sessions_page_of(catalogue.academy_pages())
    if target is None:
        raise Http404
    query = request.META.get("QUERY_STRING", "")
    location = target["public_path"] + (f"?{query}" if query else "")
    return _private(HttpResponsePermanentRedirect(location))


#: The images the retired Astro build served under /academy/, at the address
#: the media parser now publishes them.  Social caches hold the Open Graph cards
#: and the session slides printed the QR codes, so exactly these files -- and no
#: other path -- move permanently.
LEGACY_IMAGES = frozenset(
    {
        "og/home.png",
        "og/agentic-engineering.png",
        "og/ai-engineering.png",
        "og/sessions.png",
        *(f"qr/session-{number}.{suffix}" for number in (1, 2, 3) for suffix in ("png", "svg")),
    }
)


@require_safe
def legacy_image(request: HttpRequest, name: str) -> HttpResponse:
    if name not in LEGACY_IMAGES:
        raise Http404
    return HttpResponsePermanentRedirect(f"/images/academy/{name}")
