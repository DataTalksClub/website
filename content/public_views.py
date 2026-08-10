from __future__ import annotations

import json
import re
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.http import require_safe

from courses.models.course import Course
from courses.views.course import course_view
from courses.views.course_list import course_list_context
from events.services import public_registration_total

from .public_data import PROJECTION_ROOT, event_groups, podcast_seasons, public_projection
from .sitemap_contract import EXPECTED_SITEMAP_LOCATIONS

WIKI_SPECIAL_CATEGORIES = {
    "guides": "guide",
    "comparisons": "comparison",
    "roadmaps": "roadmap",
    "transitions": "transition",
    "how-tos": "how-to",
}
PODCAST_SEASONS_PER_PAGE = 3
PODCAST_PAGE_QUERY = re.compile(r"page=([1-9][0-9]{0,8})\Z", re.ASCII)


def _canonical(path: str) -> str:
    return f"https://datatalks.club{path}"


def _json_ld(entity: dict, breadcrumbs: tuple[tuple[str, str], ...] = ()) -> str:
    graph = [{"@id": entity["url"], **entity}]
    if breadcrumbs:
        graph.append(
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": position,
                        "name": name,
                        "item": _canonical(path),
                    }
                    for position, (name, path) in enumerate(breadcrumbs, start=1)
                ],
            }
        )
    return json.dumps(
        {"@context": "https://schema.org", "@graph": graph},
        ensure_ascii=False,
        sort_keys=True,
    ).replace("<", "\\u003c")


@require_safe
def permanent_public_redirect(request: HttpRequest, *, target: str) -> HttpResponse:
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"{target}?{query}" if query else target)


@require_safe
def permanent_detail_redirect(
    request: HttpRequest,
    slug: str,
    *,
    collection: str,
) -> HttpResponse:
    projection_name = {
        "blog": "articles",
        "podcast": "podcasts",
        "books": "books",
        "people": "people",
    }[collection]
    redirect = public_projection()["editorial_route_aliases_by_path"].get(request.path_info)
    if (
        redirect is None
        or redirect["collection"] != projection_name
        or redirect["record_key"] != slug
    ):
        raise Http404
    return permanent_public_redirect(request, target=redirect["final_path"])


def _render(
    request: HttpRequest,
    template: str,
    *,
    path: str,
    title: str,
    description: str,
    context: dict | None = None,
) -> HttpResponse:
    page_context = {
        "canonical_url": _canonical(path),
        "seo_title": title,
        "seo_description": description,
        **(context or {}),
    }
    if path == "/wiki" or path.startswith("/wiki/"):
        page_context["og_image_url"] = _canonical("/wiki/assets/og-default.png")
    return render(
        request,
        template,
        page_context,
    )


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def _podcast_page_number(request: HttpRequest) -> int | None:
    raw_query = request.META.get("QUERY_STRING", "")
    if not raw_query:
        return 1
    if not isinstance(raw_query, str) or len(raw_query) > 32:
        return None
    match = PODCAST_PAGE_QUERY.fullmatch(raw_query)
    return int(match.group(1)) if match else None


@require_safe
def events(request: HttpRequest) -> HttpResponse:
    groups = event_groups()
    return _render(
        request,
        "public/events.html",
        path="/events",
        title="Events — DataTalks.Club",
        description=(
            "Join our data science events including webinars, live podcasts, workshops, and "
            "conferences. Connect with experts and learn about the latest trends in data, ML, "
            "and AI."
        ),
        context={"upcoming_events": groups.upcoming, "recent_events": groups.recent, "count": 421},
    )


@require_safe
def event_detail(request: HttpRequest, slug: str) -> HttpResponse:
    if slug not in public_projection()["events_by_slug"]:
        raise Http404
    grouped = event_groups()
    event = next(item for item in (*grouped.upcoming, *grouped.recent) if item["slug"] == slug)
    entity = {
        "@type": "Event",
        "url": _canonical(event["public_path"]),
        "name": event["title"],
        "startDate": event["starts_at"],
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "performer": [
            {
                "@type": "Person",
                "name": speaker["name"],
                "url": _canonical(speaker["public_path"]),
            }
            for speaker in event["speakers"]
        ],
    }
    if event["ends_at"]:
        entity["endDate"] = event["ends_at"]
    registration_total = public_registration_total(event)
    response = _render(
        request,
        "public/event_detail.html",
        path=event["public_path"],
        title=f"{event['title']} — DataTalks.Club Events",
        description=f"{event['type'].title()} on {event['display_time']}.",
        context={
            "event": event,
            "registration_total": registration_total,
            "og_type": "event",
            "structured_data": _json_ld(
                entity,
                (("Home", "/"), ("Events", "/events"), (event["title"], event["public_path"])),
            ),
        },
    )
    if registration_total is not None:
        response["Cache-Control"] = "no-store, max-age=0, s-maxage=0"
        response["X-Event-Registration-Total-Revision"] = str(registration_total.revision)
    return response


@require_safe
def collection_hub(request: HttpRequest, *, collection: str) -> HttpResponse:
    projection = public_projection()
    configuration = {
        "articles": (
            "Articles",
            "/blog",
            (
                "Explore the latest articles on data science, machine learning, and AI from "
                "the DataTalks.Club community. Insights, tutorials, and best practices from "
                "industry experts."
            ),
        ),
        "podcasts": (
            "DataTalks.Club Podcast",
            "/podcast",
            (
                "DataTalks.Club weekly podcast episodes with data science experts, ML "
                "engineers, and AI researchers. Listen on Apple Podcasts, Spotify, YouTube."
            ),
        ),
        "books": (
            "Book of the Week",
            "/books",
            (
                "Discover the latest books in data science, machine learning, and AI. Join our "
                "weekly book discussions with authors at DataTalks.Club and win free copies of "
                "featured books."
            ),
        ),
    }
    title, path, description = configuration[collection]
    return _render(
        request,
        "public/collection_hub.html",
        path=path,
        title=f"{title} — DataTalks.Club",
        description=description,
        context={"heading": title, "records": projection[collection], "collection": collection},
    )


@require_safe
def podcast_hub(request: HttpRequest) -> HttpResponse:
    page_number = _podcast_page_number(request)
    if page_number is None:
        return _no_store(HttpResponseBadRequest("Bad request."))

    seasons = podcast_seasons()
    total_pages = (len(seasons) + PODCAST_SEASONS_PER_PAGE - 1) // PODCAST_SEASONS_PER_PAGE
    if page_number > total_pages:
        return _no_store(HttpResponse("Page not found.", status=404))

    start = (page_number - 1) * PODCAST_SEASONS_PER_PAGE
    page_seasons = seasons[start : start + PODCAST_SEASONS_PER_PAGE]

    def page_path(number: int) -> str:
        return "/podcast" if number == 1 else f"/podcast?page={number}"

    canonical_path = page_path(page_number)
    title = "DataTalks.Club Podcast"
    if page_number > 1:
        title = f"{title} — Page {page_number}"
    return _render(
        request,
        "public/podcast_hub.html",
        path=canonical_path,
        title=f"{title} — DataTalks.Club",
        description=(
            "DataTalks.Club weekly podcast episodes with data science experts, ML "
            "engineers, and AI researchers. Listen on Apple Podcasts, Spotify, YouTube."
        ),
        context={
            "seasons": page_seasons,
            "page_number": page_number,
            "page_links": tuple(
                {"number": number, "path": page_path(number)}
                for number in range(1, total_pages + 1)
            ),
            "total_pages": total_pages,
            "previous_url": _canonical(page_path(page_number - 1)) if page_number > 1 else "",
            "previous_path": page_path(page_number - 1) if page_number > 1 else "",
            "next_url": (
                _canonical(page_path(page_number + 1)) if page_number < total_pages else ""
            ),
            "next_path": page_path(page_number + 1) if page_number < total_pages else "",
        },
    )


@require_safe
def article_detail(request: HttpRequest, slug: str) -> HttpResponse:
    article = public_projection()["articles_by_slug"].get(slug)
    if article is None:
        raise Http404
    return _render(
        request,
        "public/article_detail.html",
        path=article["public_path"],
        title=f"{article['title']} — DataTalks.Club",
        description=article["description"],
        context={
            "record": article,
            "og_type": "article",
            "og_image_url": _canonical(article["image_path"]) if article["image_path"] else "",
            "published_time": article["published"],
            "structured_data": _json_ld(
                {
                    "@type": "BlogPosting",
                    "url": _canonical(article["public_path"]),
                    "headline": article["title"],
                    "description": article["description"],
                    "datePublished": article["published"],
                    "author": [
                        {
                            "@type": "Person",
                            "name": author["name"],
                            "url": _canonical(author["public_path"]),
                        }
                        for author in article["author_profiles"]
                    ],
                    **(
                        {"image": _canonical(article["image_path"])}
                        if article["image_path"]
                        else {}
                    ),
                },
                (("Home", "/"), ("Blog", "/blog"), (article["title"], article["public_path"])),
            ),
        },
    )


@require_safe
def podcast_detail(request: HttpRequest, slug: str) -> HttpResponse:
    episode = public_projection()["podcasts_by_slug"].get(slug)
    if episode is None:
        raise Http404
    return _render(
        request,
        "public/podcast_detail.html",
        path=episode["public_path"],
        title=f"{episode['title']} — DataTalks.Club Podcast",
        description=episode["description"],
        context={
            "record": episode,
            "og_type": "article",
            "og_image_url": _canonical(episode["image_path"]) if episode["image_path"] else "",
            "published_time": episode["published"],
            "structured_data": _json_ld(
                {
                    "@type": "PodcastEpisode",
                    "url": _canonical(episode["public_path"]),
                    "name": episode["title"],
                    "description": episode["description"],
                    "datePublished": episode["published"],
                    "episodeNumber": episode["episode"],
                    "partOfSeries": {
                        "@type": "PodcastSeries",
                        "name": "DataTalks.Club Podcast",
                        "url": _canonical("/podcast"),
                    },
                    **(
                        {"image": _canonical(episode["image_path"])}
                        if episode["image_path"]
                        else {}
                    ),
                },
                (
                    ("Home", "/"),
                    ("Podcast", "/podcast"),
                    (episode["title"], episode["public_path"]),
                ),
            ),
        },
    )


@require_safe
def book_detail(request: HttpRequest, slug: str) -> HttpResponse:
    book = public_projection()["books_by_slug"].get(slug)
    if book is None:
        raise Http404
    return _render(
        request,
        "public/book_detail.html",
        path=book["public_path"],
        title=f"{book['title']} — DataTalks.Club Books",
        description=book["description"],
        context={
            "record": book,
            "og_type": "book",
            "og_image_url": _canonical(book["image_path"]) if book["image_path"] else "",
            "structured_data": _json_ld(
                {
                    "@type": "Book",
                    "url": _canonical(book["public_path"]),
                    "name": book["title"],
                    "description": book["description"] or book["summary"],
                    "author": [{"@type": "Person", "name": name} for name in book["authors"]],
                    **({"image": _canonical(book["image_path"])} if book["image_path"] else {}),
                },
                (("Home", "/"), ("Books", "/books"), (book["title"], book["public_path"])),
            ),
        },
    )


@require_safe
def person_detail(request: HttpRequest, slug: str) -> HttpResponse:
    person = public_projection()["people_by_slug"].get(slug)
    if person is None:
        raise Http404
    return _render(
        request,
        "public/person_detail.html",
        path=person["public_path"],
        title=f"{person['title']} — DataTalks.Club",
        description=person["summary"] or f"{person['title']} — DataTalks.Club",
        context={
            "record": person,
            "og_type": "profile",
            "og_image_url": _canonical(person["image_path"]) if person["image_path"] else "",
            "structured_data": _json_ld(
                {
                    "@type": "Person",
                    "url": _canonical(person["public_path"]),
                    "name": person["title"],
                    "sameAs": [link["url"] for link in person["links"]],
                    **({"image": _canonical(person["image_path"])} if person["image_path"] else {}),
                },
                (("Home", "/"), (person["title"], person["public_path"])),
            ),
        },
    )


@require_safe
def course_hub(request: HttpRequest) -> HttpResponse:
    if Course.objects.exists():
        return render(
            request,
            "courses/course_list.html",
            {
                **course_list_context(request.user),
                "canonical_url": _canonical("/courses"),
            },
        )
    courses = public_projection()["courses"]
    return _render(
        request,
        "public/course_hub.html",
        path="/courses",
        title="Courses — DataTalks.Club",
        description=(
            "Community-created courses with practical homework, projects, public "
            "leaderboards, and peer review."
        ),
        context={
            "active_records": tuple(record for record in courses if not record["finished"]),
            "archive_records": tuple(record for record in courses if record["finished"]),
        },
    )


@require_safe
def course_detail(request: HttpRequest, slug: str) -> HttpResponse:
    course = public_projection()["courses_by_slug"].get(slug)
    if course is None:
        if Course.objects.filter(slug=slug).exists():
            return course_view(request, course_slug=slug)
        raise Http404
    if Course.objects.filter(slug=slug).exists():
        return course_view(request, course_slug=slug)
    return _render(
        request,
        "public/course_detail.html",
        path=course["public_path"],
        title=f"{course['title']} — DataTalks.Club",
        description="Practical lessons, homework, projects, and peer review.",
        context={
            "record": course,
            "structured_data": _json_ld(
                {
                    "@type": "Course",
                    "url": _canonical(course["public_path"]),
                    "name": course["title"],
                    "description": (
                        f"{course['homework_count']} homework assignments and "
                        f"{course['project_count']} projects."
                    ),
                    "provider": {
                        "@type": "Organization",
                        "name": "DataTalks.Club",
                        "url": "https://datatalks.club/",
                    },
                },
                (("Home", "/"), ("Courses", "/courses"), (course["title"], course["public_path"])),
            ),
        },
    )


@require_safe
def wiki_hub(request: HttpRequest) -> HttpResponse:
    if "q" in request.GET:
        return wiki_search(request)
    return _render(
        request,
        "public/wiki_hub.html",
        path="/wiki",
        title="Podcast Wiki — DataTalks.Club",
        description="Find wiki pages, guides, summaries, people, and books.",
        context={"records": public_projection()["wiki"]},
    )


@require_safe
def wiki_detail(request: HttpRequest, slug: str) -> HttpResponse:
    page = public_projection()["wiki_by_slug"].get(slug)
    if page is None:
        raise Http404
    return _render(
        request,
        "public/wiki_detail.html",
        path=page["public_path"],
        title=f"{page['title']} — DataTalks.Club Wiki",
        description=page["summary"],
        context={
            "record": page,
            "og_type": "article",
            "structured_data": _json_ld(
                {
                    "@type": "Article",
                    "url": _canonical(page["public_path"]),
                    "headline": page["title"],
                    "description": page["summary"],
                },
                (("Home", "/"), ("Wiki", "/wiki"), (page["title"], page["public_path"])),
            ),
        },
    )


def _wiki_search_results(query: str) -> tuple[dict, ...]:
    if not query:
        return ()
    terms = query.casefold().split()
    results: list[dict] = []
    seen: set[str] = set()
    for document in public_projection()["wiki_search"].get("docs", []):
        url = document.get("url", "")
        haystack = " ".join(
            str(document.get(field, ""))
            for field in ("title", "page_title", "segment_title", "text", "related_terms")
        ).casefold()
        if (
            url.startswith("/wiki/")
            and url != "/wiki/search"
            and url not in seen
            and all(term in haystack for term in terms)
        ):
            seen.add(url)
            results.append(document)
            if len(results) == 100:
                break
    return tuple(results)


@require_safe
def wiki_search(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:200]
    return _render(
        request,
        "public/wiki_search.html",
        path="/wiki",
        title="Search — DataTalks.Club Wiki",
        description="Find wiki pages, guides, summaries, people, and books.",
        context={"query": query, "results": _wiki_search_results(query)},
    )


@require_safe
def wiki_graph(request: HttpRequest) -> HttpResponse:
    nodes = public_projection()["wiki_graph"].get("nodes", [])
    return _render(
        request,
        "public/wiki_graph.html",
        path="/wiki/graph",
        title="Podcast Graph — DataTalks.Club Wiki",
        description=(
            "Explore wiki topics, typed content pages, people, podcasts, and books across the "
            "DataTalks.Club podcast archive."
        ),
        context={"nodes": nodes},
    )


@require_safe
def wiki_graph_json(request: HttpRequest) -> JsonResponse:
    return JsonResponse(public_projection()["wiki_graph"])


@require_safe
def wiki_search_json(request: HttpRequest) -> JsonResponse:
    return JsonResponse(public_projection()["wiki_search"])


@require_safe
def wiki_special(request: HttpRequest, category: str = "all") -> HttpResponse:
    pages = public_projection()["wiki"]
    special_tags = set(WIKI_SPECIAL_CATEGORIES.values())
    if category == "all":
        pages = tuple(page for page in pages if special_tags.intersection(page["tags"]))
    else:
        tag = WIKI_SPECIAL_CATEGORIES.get(category)
        if tag is None:
            raise Http404
        pages = tuple(page for page in pages if tag in page["tags"])
    return _render(
        request,
        "public/wiki_special.html",
        path="/wiki/special-pages" if category == "all" else f"/wiki/special-pages/{category}",
        title="Special pages — DataTalks.Club Wiki",
        description=(
            "Browse the guides, comparisons, roadmaps, transitions, and how-tos in the wiki."
        ),
        context={
            "records": pages,
            "category": category,
            "categories": WIKI_SPECIAL_CATEGORIES,
        },
    )


def _xml_response(body: str) -> HttpResponse:
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


@require_safe
def wiki_feed(request: HttpRequest) -> HttpResponse:
    items = "".join(
        "<item><title>{}</title><link>{}</link><description>{}</description></item>".format(
            xml_escape(page["title"]),
            xml_escape(_canonical(page["public_path"])),
            xml_escape(page["summary"]),
        )
        for page in public_projection()["wiki"][-30:]
    )
    return _xml_response(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>DataTalks.Club Wiki</title><link>https://datatalks.club/wiki</link>"
        f"{items}</channel></rss>"
    )


@require_safe
def wiki_sitemap(request: HttpRequest) -> HttpResponse:
    return section_sitemap(request, section="wiki")


@require_safe
def wiki_robots(request: HttpRequest) -> HttpResponse:
    body = "User-agent: *\nDisallow: /\n" if settings.NOINDEX else "User-agent: *\nAllow: /wiki/\n"
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


@require_safe
def wiki_asset(request: HttpRequest, asset: str) -> FileResponse:
    public_path = f"/wiki/assets/{asset}"
    if public_path not in public_projection()["manifest"].get("wiki_assets", {}):
        raise Http404
    path = PROJECTION_ROOT / "wiki_assets" / asset
    if not path.is_file() or path.is_symlink():
        raise Http404
    return FileResponse(path.open("rb"), content_type="image/png")


@require_safe
def media(request: HttpRequest, media_path: str) -> FileResponse:
    public_path = f"/images/{media_path}"
    record = public_projection()["media_by_path"].get(public_path)
    if record is None:
        raise Http404
    path = PROJECTION_ROOT / "media" / Path(media_path)
    if not path.is_file() or path.is_symlink():
        raise Http404
    return FileResponse(path.open("rb"), content_type=record["content_type"])


def _section_records(section: str) -> tuple[tuple[str, str], ...]:
    projection = public_projection()
    static_sections = {
        "main": (("/", ""), ("/slack.html", "")),
        "docs": (
            ("/docs/", ""),
            ("/docs/courses/ai-dev-tools-zoomcamp/getting-started/", ""),
        ),
        "faq": (("/faq/", ""), ("/faq/ai-dev-tools-zoomcamp.html", "")),
    }
    if section in static_sections:
        return static_sections[section]
    if section == "blog":
        return (("/blog", ""),) + tuple(
            (record["public_path"], record["published"][:10]) for record in projection["articles"]
        )
    if section == "podcast":
        return (("/podcast", ""),) + tuple(
            (record["public_path"], record["published"][:10]) for record in projection["podcasts"]
        )
    if section == "books":
        return (("/books", ""),) + tuple(
            (record["public_path"], record["published"][:10]) for record in projection["books"]
        )
    if section == "people":
        return tuple((record["public_path"], "") for record in projection["people"])
    if section == "events":
        return (("/events", ""),) + tuple(
            (record["public_path"], record["starts_at"][:10]) for record in projection["events"]
        )
    if section == "courses":
        return (
            ("/courses", ""),
            *((record["public_path"], "") for record in projection["courses"]),
            ("/courses/ai-dev-tools-zoomcamp", ""),
            ("/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026", ""),
        )
    if section == "wiki":
        discovery = (
            ("/wiki", ""),
            ("/wiki/graph", ""),
            ("/wiki/special-pages", ""),
            *((f"/wiki/special-pages/{category}", "") for category in WIKI_SPECIAL_CATEGORIES),
        )
        return discovery + tuple((record["public_path"], "") for record in projection["wiki"])
    raise Http404


@require_safe
def section_sitemap(request: HttpRequest, section: str) -> HttpResponse:
    del request
    urls = []
    for path, lastmod in _section_records(section):
        lastmod_xml = f"<lastmod>{xml_escape(lastmod)}</lastmod>" if lastmod else ""
        urls.append(f"<url><loc>{xml_escape(_canonical(path))}</loc>{lastmod_xml}</url>")
    return _xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{''.join(urls)}</urlset>"
    )


def production_sitemap() -> str:
    sitemaps = "".join(
        f"<sitemap><loc>{xml_escape(location)}</loc></sitemap>"
        for location in EXPECTED_SITEMAP_LOCATIONS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{sitemaps}</sitemapindex>"
    )
