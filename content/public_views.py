from __future__ import annotations

import json
import re
from functools import wraps
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from core.sponsors import public_events_hub_sponsors
from courses.models import Cohort
from events.identity import (
    EventIdentityNotFound,
    canonical_detail_path,
    event_projection_record,
    redirect_for_supplied_slug,
    resolve_legacy_path,
    resolve_public_id,
    resolve_uuid,
)
from events.models import EventQnaSession
from events.services import public_registration_total

from . import wiki_content
from .article_content import article_view
from .article_faq import ArticleFaq, article_faq
from .faq_data import faq_courses
from .pagination import (
    PublicPagination,
    paginate_public_request,
    pagination_title,
    public_page_url,
)
from .person_content import person_view
from .podcast_content import (
    episode_navigation,
    episode_view,
    listening_platform_phrase,
    podcast_platform_links,
    season_episodes,
)
from .public_data import (
    PROJECTION_ROOT,
    event_date_groups,
    event_groups,
    podcast_seasons,
    public_projection,
)
from .public_query import selector_query
from .sitemap_contract import EXPECTED_SITEMAP_LOCATIONS

WIKI_SPECIAL_CATEGORIES = {
    "guides": "guide",
    "comparisons": "comparison",
    "roadmaps": "roadmap",
    "transitions": "transition",
    "how-tos": "how-to",
}
PODCAST_SEASON_QUERY = re.compile(r"season=([1-9][0-9]{0,8})\Z", re.ASCII)
PODCAST_SEASON_SELECTORS = frozenset({"season"})
# `/events?filter=past[&page=N]` is the one legacy spelling that still redirects into the
# archive, so the hub keeps a parser for it.  The archive itself no longer has a parser of
# its own: past events, books and the Wiki catalogue all read `content.pagination`.
EVENT_PAST_PAGE_QUERY = re.compile(r"filter=past(?:&page=([1-9][0-9]{0,2}))?\Z", re.ASCII)
EVENT_FILTER_SELECTORS = frozenset({"filter", "page"})


def _canonical(path: str) -> str:
    return f"{settings.CANONICAL_ORIGIN.rstrip('/')}{path}"


def _json_ld(
    entity: dict,
    breadcrumbs: tuple[tuple[str, str], ...] = (),
    *,
    extra: tuple[dict, ...] = (),
) -> str:
    graph = [{"@id": entity["url"], **entity}]
    graph.extend(extra)
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


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@csrf_exempt
def permanent_public_redirect(
    request: HttpRequest, *, target: str, preserve_query: bool = True
) -> HttpResponse:
    """Serve one explicit public redirect with a bounded unsafe-method response.

    Public aliases are safe read-only routes.  Handling the method boundary here (instead of
    relying on ``require_safe``) lets the response policy reject unsafe requests before any
    redirect work and attach the required no-store cache directive.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    query = request.META.get("QUERY_STRING", "") if preserve_query else ""
    response = HttpResponsePermanentRedirect(f"{target}?{query}" if query else target)
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_safe
def legacy_events_redirect(request: HttpRequest) -> HttpResponse:
    """Redirect the legacy event hub directly to its canonical filter path."""

    if request.GET.get("filter") == "past" and len(request.GET) == 1:
        return permanent_public_redirect(request, target="/events/past", preserve_query=False)
    return permanent_public_redirect(request, target="/events")


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
    # Every wiki surface carries the wiki social card, including a catalogue page
    # beyond the first — `/wiki?page=6` is the same surface as `/wiki`.
    if path == "/wiki" or path.startswith(("/wiki/", "/wiki?")):
        page_context["og_image_url"] = _canonical("/wiki/assets/og-default.png")
    return render(
        request,
        template,
        page_context,
    )


def _pagination_context(pagination: PublicPagination) -> dict:
    """The context a paginated catalogue adds: the controls and the head relations.

    `previous_url`/`next_url` are the absolute canonical URLs the head's `rel=prev`
    and `rel=next` publish, which is the same pair of keys the podcast hub's season
    navigation already uses.  The visible controls read `pagination` itself.
    """

    return {
        "pagination": pagination,
        "previous_url": _canonical(pagination.previous_url) if pagination.previous_url else "",
        "next_url": _canonical(pagination.next_url) if pagination.next_url else "",
    }


def _public_not_found(request: HttpRequest) -> HttpResponse:
    response = render(request, "404.html", status=404)
    response["Cache-Control"] = "max-age=0"
    return response


def _public_event_route(view):
    """Apply the event route method and anonymous error cache contract.

    ``require_safe`` raises a response before a view can classify the unsafe request, and Django's
    default 404 handler does not know that an event path is a public cacheable miss.  Event routes
    therefore use this small explicit boundary: read-only methods are allowed, unsafe methods are
    no-store 405s, and identity/alias misses become bounded public 404s.
    """

    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.method not in {"GET", "HEAD"}:
            return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
        try:
            response = view(request, *args, **kwargs)
        except Http404:
            response = _public_not_found(request)
        # Redirect aliases preserve their raw query under the explicit redirect cache policy.
        # A terminal detail/error carrying any query is not a query-keyed public object.
        if request.META.get("QUERY_STRING", "") and response.status_code != 301:
            return _no_store(response)
        return response

    return csrf_exempt(wrapped)


def _podcast_season_number(request: HttpRequest) -> tuple[bool, int | None]:
    raw_query = selector_query(
        request.META.get("QUERY_STRING", ""), selectors=PODCAST_SEASON_SELECTORS
    )
    if raw_query is None:
        return False, None
    if not raw_query:
        return True, None
    if len(raw_query) > 32:
        return False, None
    match = PODCAST_SEASON_QUERY.fullmatch(raw_query)
    return (True, int(match.group(1))) if match else (False, None)


def _podcast_season_path(number: int, *, latest: int) -> str:
    return "/podcast" if number == latest else f"/podcast?season={number}"


@csrf_exempt
def events(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))

    raw_query = selector_query(
        request.META.get("QUERY_STRING", ""), selectors=EVENT_FILTER_SELECTORS
    )
    if raw_query is None:
        return _no_store(HttpResponseBadRequest("Bad request."))
    if raw_query:
        match = EVENT_PAST_PAGE_QUERY.fullmatch(raw_query)
        if match is None:
            return _no_store(HttpResponseBadRequest("Bad request."))
        page_number = int(match.group(1) or "1")
        target = public_page_url("/events/past", page_number)
        return permanent_public_redirect(request, target=target, preserve_query=False)
    return _render_events(request, past=False, pagination=None)


@csrf_exempt
def events_past(request: HttpRequest) -> HttpResponse:
    """The past-events archive, on the shared public paginator (issues #177, #178).

    The archive owns its order and its date grouping; it owns nothing about pages.
    The deterministic recent sequence is sliced first and grouped afterwards, so a
    local date that spans the boundary appears as a heading on both pages while every
    event still appears exactly once.
    """

    pagination = paginate_public_request(
        request,
        event_groups().recent,
        clean_base_path="/events/past",
        catalogue_label="Past event pages",
    )
    if isinstance(pagination, HttpResponse):
        return pagination
    return _render_events(request, past=True, pagination=pagination)


def _render_events(
    request: HttpRequest, *, past: bool, pagination: PublicPagination | None
) -> HttpResponse:
    groups = event_groups()
    upcoming_groups = groups.upcoming_groups or event_date_groups(list(groups.upcoming))
    if past:
        assert pagination is not None
        timeline_groups = event_date_groups(list(pagination.items), descending=True)
        canonical_path = pagination.canonical_path
        title = pagination_title("Past events", pagination.page_number)
        extra_context = _pagination_context(pagination)
        count = pagination.total_items
    else:
        timeline_groups = upcoming_groups
        canonical_path = "/events"
        title = "Events — DataTalks.Club"
        extra_context = {}
        count = len(groups.upcoming)

    response = _render(
        request,
        "public/events.html",
        path=canonical_path,
        title=title,
        description=(
            "Join our data science events including webinars, live podcasts, workshops, and "
            "conferences. Connect with experts and learn about the latest trends in data, ML, "
            "and AI."
        ),
        context={
            "upcoming_events": groups.upcoming,
            "recent_events": groups.recent,
            "timeline_groups": timeline_groups,
            "event_mode": "past" if past else "upcoming",
            "is_past_events": past,
            "upcoming_path": "/events",
            "past_path": "/events/past",
            "count": count,
            "events_hub_sponsors": () if past else public_events_hub_sponsors(),
            **extra_context,
        },
    )
    # Anonymous event catalogues are public but query-bounded.  Keep browser revalidation
    # explicit; ResponsePolicyMiddleware still overrides this for authenticated requests.
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response


@_public_event_route
def event_detail(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    try:
        identity = resolve_public_id(event_id)
        redirect_path = redirect_for_supplied_slug(identity.id, slug)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    if redirect_path is not None:
        return permanent_public_redirect(request, target=redirect_path)
    try:
        projected = event_projection_record(identity)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    grouped = event_groups()
    event = next(
        (
            item
            for item in (*grouped.upcoming, *grouped.recent)
            if item.get("identity_id") == str(identity.id)
        ),
        {**projected, "identity_id": str(identity.id)},
    )
    event["public_path"] = canonical_detail_path(identity.id)
    # The page states whether the event has happened in a word, using the same split the
    # events index draws its rows from.  An event the grouped catalogue cannot place —
    # the projected fallback record above — reports no state rather than an invented one.
    if any(item.get("identity_id") == str(identity.id) for item in grouped.recent):
        event_state = "past"
    elif any(item.get("identity_id") == str(identity.id) for item in grouped.upcoming):
        event_state = "upcoming"
    else:
        event_state = ""
    entity = {
        "@type": "Event",
        "url": _canonical(canonical_detail_path(identity.id)),
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
    qna_url = ""
    qna_session = EventQnaSession.objects.filter(event_id=identity.id).first()
    if qna_session is not None and qna_session.state in {
        EventQnaSession.State.OPEN,
        EventQnaSession.State.CLOSED,
    }:
        qna_url = f"{event['public_path']}/qna/"
    response = _render(
        request,
        "public/event_detail.html",
        path=event["public_path"],
        title=f"{event['title']} — DataTalks.Club Events",
        description=f"{event['type'].title()} on {event['display_time']}.",
        context={
            "event": event,
            "event_state": event_state,
            "registration_total": registration_total,
            "qna_url": qna_url,
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


@_public_event_route
def event_detail_without_slug(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        target = canonical_detail_path(resolve_public_id(event_id).id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    return permanent_public_redirect(request, target=target)


@_public_event_route
def event_detail_legacy_uuid(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    try:
        target = canonical_detail_path(resolve_uuid(event_id).id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    return permanent_public_redirect(request, target=target)


@_public_event_route
def event_detail_legacy_uuid_without_slug(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        target = canonical_detail_path(resolve_uuid(event_id).id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    return permanent_public_redirect(request, target=target)


@_public_event_route
def event_legacy_redirect(request: HttpRequest, legacy_path: str) -> HttpResponse:
    del legacy_path
    try:
        event = resolve_legacy_path(request.path_info)
        target = canonical_detail_path(event.id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    return permanent_public_redirect(request, target=target)


# The two collections behind `public/collection_hub.html`.  Both are dated archives
# that grew past one screen — 55 articles, 98 books — so both page, through the one
# shared paginator (issues #174, #178).  Each entry is the hub's heading, its clean
# canonical base path, its catalogue's accessible label and its description.
COLLECTION_HUBS = {
    "articles": (
        "Articles",
        "/blog",
        "Article pages",
        (
            "Explore the latest articles on data science, machine learning, and AI from "
            "the DataTalks.Club community. Insights, tutorials, and best practices from "
            "industry experts."
        ),
    ),
    "books": (
        "Book of the Week",
        "/books",
        "Book archive pages",
        (
            "Discover the latest books in data science, machine learning, and AI. Join our "
            "weekly book discussions with authors at DataTalks.Club and win free copies of "
            "featured books."
        ),
    ),
}


@csrf_exempt
def collection_hub(request: HttpRequest, *, collection: str) -> HttpResponse:
    """The blog index and the Book of the Week archive, on the shared paginator.

    The projections are already in their published order — `(published, slug)`
    descending — and that is the order the pages are cut from.  Nothing here sorts,
    filters or renumbers; the collection differs only by which records, which base
    path and which words.
    """

    heading, base_path, catalogue_label, description = COLLECTION_HUBS[collection]
    pagination = paginate_public_request(
        request,
        public_projection()[collection],
        clean_base_path=base_path,
        catalogue_label=catalogue_label,
    )
    if isinstance(pagination, HttpResponse):
        return pagination
    response = _render(
        request,
        "public/collection_hub.html",
        path=pagination.canonical_path,
        title=pagination_title(heading, pagination.page_number),
        description=description,
        context={
            "heading": heading,
            "records": pagination.items,
            "collection": collection,
            "collection_total": pagination.total_items,
            **_pagination_context(pagination),
        },
    )
    # Anonymous catalogue hubs are public but query-bounded (issue #174 keeps the
    # books archive on the same class as `/events/past`).  Keep browser
    # revalidation explicit; ResponsePolicyMiddleware still overrides this for
    # authenticated requests.
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response


# CSRF middleware runs before method decorators. This read-only callback is exempt so
# unsafe methods reach the explicit 405 response; it performs no mutation.
@csrf_exempt
def podcast_hub(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))

    valid_query, requested_season = _podcast_season_number(request)
    if not valid_query:
        return _no_store(HttpResponseBadRequest("Bad request."))

    projection = public_projection()
    seasons = podcast_seasons(projection["podcasts"])
    latest_season = seasons[0].number
    selected_number = latest_season if requested_season is None else requested_season
    season_index = next(
        (index for index, season in enumerate(seasons) if season.number == selected_number),
        None,
    )
    if season_index is None:
        return _no_store(HttpResponse("Page not found.", status=404))

    selected_season = seasons[season_index]
    episodes = season_episodes(selected_season.episodes)
    newer_season = seasons[season_index - 1] if season_index > 0 else None
    older_season = seasons[season_index + 1] if season_index + 1 < len(seasons) else None
    canonical_path = _podcast_season_path(selected_number, latest=latest_season)
    title = "DataTalks.Club Podcast"
    if selected_number != latest_season:
        title = f"{title} — Season {selected_number}"
    response = _render(
        request,
        "public/podcast_hub.html",
        path=canonical_path,
        title=f"{title} — DataTalks.Club",
        description=(
            "DataTalks.Club weekly podcast episodes with data science experts, ML "
            "engineers, and AI researchers. Listen on Apple Podcasts, Spotify, YouTube."
        ),
        context={
            "season": selected_season,
            "episodes": episodes,
            "episode_total": sum(len(available.episodes) for available in seasons),
            "listening_platforms": listening_platform_phrase(episodes),
            "podcast_platform_links": podcast_platform_links(projection["podcast_platforms"]),
            "season_links": tuple(
                {
                    "number": available.number,
                    "path": _podcast_season_path(available.number, latest=latest_season),
                }
                for available in seasons
            ),
            "newer_season": newer_season,
            "newer_path": (
                _podcast_season_path(newer_season.number, latest=latest_season)
                if newer_season
                else ""
            ),
            "older_season": older_season,
            "older_path": (
                _podcast_season_path(older_season.number, latest=latest_season)
                if older_season
                else ""
            ),
            "previous_url": (
                _canonical(_podcast_season_path(newer_season.number, latest=latest_season))
                if newer_season
                else ""
            ),
            "next_url": (
                _canonical(_podcast_season_path(older_season.number, latest=latest_season))
                if older_season
                else ""
            ),
        },
    )
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response


def _article_faq_structured_data(article: dict, faq: ArticleFaq | None) -> tuple[dict, ...]:
    """Return the FAQPage node one article's recovered section publishes.

    Written the way `content.review_views._faq_structured_data` writes the course
    FAQ node, so the two agree: one `FAQPage` whose questions carry their own
    canonical fragment and the plain text of the answer the page shows.
    """

    if faq is None:
        return ()
    canonical = _canonical(article["public_path"])
    questions = []
    for question in faq.questions:
        question_url = f"{canonical}#{question.id}"
        questions.append(
            {
                "@type": "Question",
                "@id": question_url,
                "url": question_url,
                "name": question.question,
                "acceptedAnswer": {"@type": "Answer", "text": question.answer_text},
            }
        )
    return (
        {
            "@type": "FAQPage",
            "@id": f"{canonical}#{faq.heading_id}",
            "url": f"{canonical}#{faq.heading_id}",
            "name": f"{article['title']} — frequently asked questions",
            "mainEntity": questions,
        },
    )


@require_safe
def article_detail(request: HttpRequest, slug: str) -> HttpResponse:
    projection = public_projection()
    article = projection["articles_by_slug"].get(slug)
    if article is None:
        raise Http404
    # Ten articles closed with a frequently-asked-questions section the projected
    # body never carried; `content.article_faq` holds that recovered half.  An
    # article without one gets `None`, and the page then draws no FAQ region.
    faq = article_faq(slug)
    return _render(
        request,
        "public/article_detail.html",
        path=article["public_path"],
        title=f"{article['title']} — DataTalks.Club",
        description=article["description"],
        context={
            "record": article,
            # The design 5a reading page (issue #179) renders this composed value:
            # the byline joined to the people records, the publication date and
            # reading estimate the design writes, and the body as prose sections.
            "article": article_view(article, projection["people_by_slug"], faq),
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
                # The legacy accordion published FAQPage data with every one of
                # these ten sections, and the course FAQ pages publish the same
                # node today (`review_views._faq_structured_data`).  Restoring it
                # here keeps the rich result the article already earned; an
                # article without a recovered FAQ adds no node at all.
                extra=_article_faq_structured_data(article, faq),
            ),
        },
    )


def _render_podcast_detail(
    request: HttpRequest,
    episode: dict,
    *,
    projection: dict | None = None,
) -> HttpResponse:
    projection = projection or public_projection()
    image_path = (
        episode["image_path"]
        if episode.get("media_available") and episode.get("image_path")
        else ""
    )
    previous_episode, next_episode, related_episodes = episode_navigation(
        episode,
        projection["podcasts"],
        people_by_slug=projection["people_by_slug"],
    )
    try:
        episode_graph = wiki_content.episode_graph(episode, projection=projection)
    except ImproperlyConfigured:
        # A checked projection failure must not make an otherwise useful episode
        # page fail or expose graph-contract details.  The next projection build
        # can repair the data while the page keeps its explicit unavailable state.
        episode_graph = wiki_content.unavailable_episode_graph(episode)
    episode_entity = {
        "@type": "PodcastEpisode",
        "url": _canonical(episode["public_path"]),
        "name": episode["title"],
        "description": episode["description"],
        "episodeNumber": episode["episode"],
        "partOfSeason": {
            "@type": "PodcastSeason",
            "seasonNumber": episode["season"],
        },
        "partOfSeries": {
            "@type": "PodcastSeries",
            "name": "DataTalks.Club Podcast",
            "url": _canonical("/podcast"),
        },
        **({"datePublished": episode["published"]} if episode["published"] else {}),
        **({"image": _canonical(image_path)} if image_path else {}),
    }
    response = _render(
        request,
        "public/podcast_detail.html",
        path=episode["public_path"],
        title=f"{episode['title']} — DataTalks.Club Podcast",
        description=episode["description"],
        context={
            "record": episode,
            "episode": episode_view(episode, people_by_slug=projection["people_by_slug"]),
            "previous_episode": previous_episode,
            "next_episode": next_episode,
            "related_episodes": related_episodes,
            "episode_graph": episode_graph,
            "og_type": "article",
            "og_image_url": _canonical(image_path) if image_path else "",
            "published_time": episode["published"] or "",
            "structured_data": _json_ld(
                episode_entity,
                (
                    ("Home", "/"),
                    ("Podcast", "/podcast"),
                    (episode["title"], episode["public_path"]),
                ),
            ),
        },
    )
    response["Cache-Control"] = (
        "no-store, max-age=0"
        if request.META.get("QUERY_STRING", "")
        else "max-age=0, must-revalidate"
    )
    return response


@csrf_exempt
def podcast_detail(request: HttpRequest, slug: str) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    projection = public_projection()
    episode = projection["podcasts_by_slug"].get(slug)
    if episode is None:
        raise Http404
    return _render_podcast_detail(request, episode, projection=projection)


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
                    # The book's own byline: names, and the profile address for an
                    # author this site publishes one for.  It used to emit the
                    # record's raw author keys as if they were names.
                    "author": [
                        {
                            "@type": "Person",
                            "name": author["name"],
                            **(
                                {"url": _canonical(author["public_path"])}
                                if author["public_path"]
                                else {}
                            ),
                        }
                        for author in book["author_profiles"]
                    ],
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
            "person": person_view(person),
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


@csrf_exempt
def wiki_hub(request: HttpRequest) -> HttpResponse:
    """The Wiki landing page: four ways in, the last of which is a paged catalogue.

    `q` is the mode switch and is read first, so a search request never reaches the
    paginator and search results are never paged (issue #175).  Without `q` the hub is
    the A–Z catalogue in the projection's `(title.casefold(), slug)` order, cut into
    pages by the shared paginator (issue #178).
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    if "q" in request.GET:
        return wiki_search(request)
    pagination = paginate_public_request(
        request,
        public_projection()["wiki"],
        clean_base_path="/wiki",
        catalogue_label="Wiki catalogue pages",
    )
    if isinstance(pagination, HttpResponse):
        return pagination
    return _render(
        request,
        "public/wiki_hub.html",
        path=pagination.canonical_path,
        title=pagination_title("Podcast Wiki", pagination.page_number),
        description="Find wiki pages, guides, summaries, people, and books.",
        context={
            "records": pagination.items,
            "catalogue_total": pagination.total_items,
            **_pagination_context(pagination),
        },
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
    return _render(
        request,
        "public/wiki_graph.html",
        path="/wiki/graph",
        title="Podcast Graph — DataTalks.Club Wiki",
        description=(
            "Explore wiki topics, typed content pages, people, podcasts, and books across the "
            "DataTalks.Club podcast archive."
        ),
        context={
            "graph_groups": wiki_content.graph_groups(),
            "graph_totals": wiki_content.graph_totals(),
            "neighbourhood": wiki_content.busiest_neighbourhood(),
        },
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
        "main": (
            ("/", ""),
            ("/terms", ""),
            ("/privacy", ""),
            ("/impressum", ""),
            ("/slack", ""),
        ),
        "docs": (
            ("/docs/", ""),
            ("/docs/courses/ai-dev-tools-zoomcamp/getting-started/", ""),
        ),
    }
    if section in static_sections:
        return static_sections[section]
    if section == "faq":
        return (("/faq/", ""),) + tuple((course["public_path"], "") for course in faq_courses())
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
        return (("/events", ""), ("/events/past", "")) + tuple(
            (record["public_path"], record["starts_at"][:10]) for record in projection["events"]
        )
    if section == "courses":
        return (
            ("/courses", ""),
            *(
                (f"/courses/{slug}", "")
                for slug in Cohort.objects.filter(visible=True, course__visible=True)
                .order_by("course__slug")
                .values_list("course__slug", flat=True)
                .distinct()
            ),
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
