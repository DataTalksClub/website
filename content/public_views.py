from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from functools import wraps
from io import BytesIO
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
    JsonResponse,
)
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from core.breadcrumbs import Trail, trail
from core.home_content import wiki_graph as home_wiki_graph
from core.runtime_config import get_str_setting
from core.sensitive_query import has_sensitive_query_key
from core.sponsors import public_events_hub_sponsors
from course_management.observability import record_event
from courses.models import Cohort, SharedLesson, SharedModule
from event_qna.models import EventQnaSession
from event_registrants.registrant_mail import send_registration_confirmed
from events.identity import (
    EventIdentityNotFound,
    canonical_detail_path,
    merged_canonical_path,
    redirect_for_supplied_slug,
    resolve_public_id,
)
from events.lifecycle import accepts_registrations
from events.queries import event_public_record, published_event_records

from . import catalogue, wiki_content, wiki_reader
from . import wiki_hub as wiki_hub_content
from .article_content import article_view, render_body_markdown
from .article_faq import ArticleFaq, article_faq
from .book_content import book_threads
from .docs_reader import docs_pages
from .event_banners import event_banner_url
from .event_content import EventGroups, event_date_groups, event_groups
from .event_speakers import event_speaker_records
from .faq_data import faq_courses
from .media_store import (
    MediaStore,
    MediaStoreError,
    media_store,
    read_media_object,
    record_filename,
    record_key,
)
from .pagination import (
    PublicPagination,
    paginate_public_request,
    pagination_title,
    public_page_url,
)
from .person_content import person_view
from .podcast_content import (
    PlatformLink,
    episode_view,
    podcast_platform_links,
    podcast_seasons,
    related_episodes,
    transcript_text,
)
from .podcast_hub import (
    HUB_LISTEN_ORDER,
    guest_cards,
    hub_episodes,
    hub_season,
    podcast_season_path,
    topic_episodes,
)
from .podcast_routes import PODCAST_HIERARCHICAL_ONLY_SLUGS, podcast_public_id
from .public_query import selector_query
from .review_views import SLACK_PUBLIC_PATH
from .sitemap_contract import EXPECTED_SITEMAP_LOCATIONS
from .topic_reading import topic_reading
from .wiki_content import WIKI_ASSET_ROOT

WIKI_SPECIAL_CATEGORIES = {
    "guides": "guide",
    "comparisons": "comparison",
    "roadmaps": "roadmap",
    "transitions": "transition",
    "how-tos": "how-to",
}
# `/events?filter=past[&page=N]` is the one legacy spelling that still redirects into the
# archive, so the hub keeps a parser for it.  The archive itself no longer has a parser of
# its own: past events, books and the Wiki catalogue all read `content.pagination`.
EVENT_PAST_PAGE_QUERY = re.compile(r"filter=past(?:&page=([1-9][0-9]{0,2}))?\Z", re.ASCII)
EVENT_FILTER_SELECTORS = frozenset({"filter", "page"})


def _canonical(path: str) -> str:
    return f"{get_str_setting('site.origin.canonical')}{path}"


def _json_ld(
    entity: dict,
    breadcrumbs: Trail | None = None,
    *,
    extra: tuple[dict, ...] = (),
) -> str:
    """Serialise one page's entity graph, and its trail when it has one.

    The trail is the same ``core.breadcrumbs.Trail`` the page draws with
    ``{% breadcrumbs %}``, so the published ``BreadcrumbList`` and the visible
    crumbs cannot say different things: the levels are declared once, in the
    view, and the site root is added here and only here.
    """

    graph = [{"@id": entity["url"], **entity}]
    graph.extend(extra)
    if breadcrumbs is not None:
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
                    for position, (name, path) in enumerate(breadcrumbs.published_items(), start=1)
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

    A credential-shaped query key gets the same bounded treatment: the raw query is never
    copied into a publicly cacheable ``Location``, and the refusal itself is stored nowhere
    that could later hand the credential back.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    if has_sensitive_query_key(request):
        return _no_store(HttpResponse("Invalid request.", status=400, content_type="text/plain"))
    query = request.META.get("QUERY_STRING", "") if preserve_query else ""
    response = HttpResponsePermanentRedirect(f"{target}?{query}" if query else target)
    response["Cache-Control"] = "public, max-age=300"
    return response


@csrf_exempt
def editorial_detail_alias_redirect(
    request: HttpRequest,
    *,
    collection: str,
    slug: str,
) -> HttpResponse:
    """Redirect an existing editorial alias to its database-recorded canonical."""

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    resolver = {
        "article": catalogue.article,
        # A book's public slug is not its source slug (the week date is dropped),
        # so its bare alias resolves by the canonical address it shortens.
        "book": lambda public_slug: catalogue.book_at(f"/books/{public_slug}.html"),
        "person": catalogue.person,
        "podcast": catalogue.podcast,
    }[collection]
    source_slug = slug.removesuffix("/")
    podcast_html_alias = collection == "podcast" and slug.endswith(".html")
    if podcast_html_alias:
        source_slug = slug.removesuffix(".html")
    if (
        collection == "podcast"
        and not podcast_html_alias
        and source_slug in PODCAST_HIERARCHICAL_ONLY_SLUGS
    ):
        raise Http404
    if podcast_html_alias:
        # Source keys are recorded at ingestion and can differ from a repaired
        # public slug. Resolve the imported alias exactly, without guessing a
        # modern slug from its historical spelling or accepting a collision.
        matches = [
            episode
            for episode in catalogue.podcasts()
            if source_slug in {episode["slug"], episode.get("provenance", {}).get("source_key")}
        ]
        record = matches[0] if len(matches) == 1 else None
    else:
        record = resolver(source_slug)
    if record is None:
        raise Http404
    target = record["public_path"]
    clean_alias = (
        f"/podcast/{source_slug}" if collection == "podcast" else target.removesuffix(".html")
    )
    aliases = {clean_alias, f"{clean_alias}/"}
    if podcast_html_alias:
        aliases = {f"{clean_alias}.html"}
    if request.path_info not in aliases:
        raise Http404
    return permanent_public_redirect(request, target=target)


_PODWIKI_EXACT_REDIRECTS = {
    "": "/wiki",
    "wiki": "/wiki",
    "course-wiki": "/wiki",
    "search": "/wiki",
    "graph": "/wiki/graph",
    "graph/graph.json": "/wiki/graph/graph.json",
    "search/search-corpus.json": "/wiki/search-corpus.json",
    "feed.xml": "/wiki/feed.xml",
    "sitemap.xml": "/wiki/sitemap.xml",
    "robots.txt": "/wiki/robots.txt",
    "special-pages": "/wiki/special-pages",
}


@csrf_exempt
def podwiki_redirect(request: HttpRequest, rest: str = "") -> HttpResponse:
    """Redirect legacy ``/podwiki/*`` URLs to ``/wiki/*`` (or ``/wiki`` for retired pages)."""

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    normalized = rest.strip("/")
    target = _PODWIKI_EXACT_REDIRECTS.get(normalized)
    if target is None:
        if normalized.startswith("special-pages/"):
            category = normalized.removeprefix("special-pages/")
            target = (
                f"/wiki/special-pages/{category}"
                if category in WIKI_SPECIAL_CATEGORIES
                else "/wiki/special-pages"
            )
        else:
            slug = normalized
            for prefix in ("wiki/", "course-wiki/"):
                if normalized.startswith(prefix):
                    slug = normalized.removeprefix(prefix)
                    break
            page = wiki_reader.wiki_page(slug)
            target = page["public_path"] if page is not None else "/wiki"
    return permanent_public_redirect(request, target=target)


@require_safe
def legacy_events_redirect(request: HttpRequest) -> HttpResponse:
    """Redirect the legacy event hub directly to its canonical filter path."""

    if request.GET.get("filter") == "past" and len(request.GET) == 1:
        return permanent_public_redirect(request, target="/events/past", preserve_query=False)
    return permanent_public_redirect(request, target="/events")


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

    groups = event_groups()
    pagination = paginate_public_request(
        request,
        groups.recent,
        clean_base_path="/events/past",
        catalogue_label="Past event pages",
    )
    if isinstance(pagination, HttpResponse):
        return pagination
    return _render_events(request, past=True, pagination=pagination, groups=groups)


def _render_events(
    request: HttpRequest,
    *,
    past: bool,
    pagination: PublicPagination | None,
    groups: EventGroups | None = None,
) -> HttpResponse:
    if groups is None:
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
        redirect_path = redirect_for_supplied_slug(identity.content_id, slug)
    except EventIdentityNotFound as exc:
        merged_path = merged_canonical_path(event_id)
        if merged_path is None:
            raise Http404 from exc
        return permanent_public_redirect(request, target=merged_path)
    if redirect_path is not None:
        return permanent_public_redirect(request, target=redirect_path)
    try:
        projected = event_public_record(identity)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    grouped = event_groups()
    event = next(
        (
            {**item}
            for item in (*grouped.upcoming, *grouped.recent)
            if item.get("identity_id") == str(identity.content_id)
        ),
        {**projected, "identity_id": str(identity.content_id)},
    )
    event["public_path"] = canonical_detail_path(identity.content_id)
    event["banner_url"] = event_banner_url(event)
    # The credit says who spoke; the biography belongs to that person's own
    # profile and is joined in here, so the page never carries a copy that can
    # drift from the profile it came from.
    event["speakers"] = event_speaker_records(
        event["speakers"],
        people_by_slug=catalogue.people_by_slug(),
        people_by_path=catalogue.people_by_path(),
    )
    # The page states whether the event has happened in a word, using the same split the
    # events index draws its rows from.  An event the grouped catalogue cannot place —
    # the projected fallback record above — reports no state rather than an invented one.
    if any(item.get("identity_id") == str(identity.content_id) for item in grouped.recent):
        event_state = "past"
    elif any(item.get("identity_id") == str(identity.content_id) for item in grouped.upcoming):
        event_state = "upcoming"
    else:
        event_state = ""
    entity = {
        "@type": "Event",
        "url": _canonical(canonical_detail_path(identity.content_id)),
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
    if event["banner_url"]:
        entity["image"] = _canonical(event["banner_url"])
    if event.get("sponsors"):
        entity["sponsor"] = [
            {
                "@type": "Organization",
                "name": sponsor["name"],
                **(
                    {"url": sponsor["url"] if sponsor["external"] else _canonical(sponsor["url"])}
                    if sponsor["url"]
                    else {}
                ),
            }
            for sponsor in event["sponsors"]
        ]
    if event["ends_at"]:
        entity["endDate"] = event["ends_at"]
    qna_url = ""
    qna_session = EventQnaSession.objects.filter(event_id=identity.pk).first()
    if qna_session is not None and qna_session.state in {
        EventQnaSession.State.OPEN,
        EventQnaSession.State.CLOSED,
    }:
        qna_url = f"{event['public_path']}/qna/"
    # A signed-in visitor registers in one click with the account's address, so
    # the card needs to know whether that account already holds a seat.
    viewer = request.user
    viewer_registered = False
    if event_state == "upcoming" and viewer.is_authenticated and viewer.email:
        from community_base.events.models import EventRegistration
        from django.db.models import Q

        viewer_registered = EventRegistration.objects.filter(
            Q(user=viewer) | Q(normalized_email=viewer.email.strip().lower()),
            event=identity,
            status=EventRegistration.Status.CONFIRMED,
        ).exists()
    # A signed-out visitor logs in inside the card: each provider's sign-in
    # runs in a pop-up that lands on `login_popup_done`, and without a pop-up
    # (blocked, or no JavaScript) the same form returns to this page.
    inline_login_providers: list[dict[str, str]] = []
    # The email form's outcome for this browser: "pending" until the emailed
    # link is followed, then "confirmed".
    email_registration = None
    if not viewer.is_authenticated:
        from community_base.events.models import EventRegistration

        remembered = _remembered_email_registration(request, identity)
        if remembered is not None and remembered.status in {
            EventRegistration.Status.PENDING_VERIFICATION,
            EventRegistration.Status.CONFIRMED,
        }:
            email_registration = {
                "email": remembered.original_email,
                "pending": remembered.status == EventRegistration.Status.PENDING_VERIFICATION,
            }
            # Just confirmed through the emailed link: offer the one-click
            # account, or, when the address already has one, the login.
            if not email_registration["pending"]:
                grant = _quick_account_grant(request, identity)
                if grant is not None:
                    email_registration["quick_account"] = (
                        "exists"
                        if _account_exists_for_email(str(grant.get("email") or ""))
                        else "offer"
                    )
                elif request.session.get(QUICK_ACCOUNT_EXISTS_SESSION_KEY) == str(identity.pk):
                    email_registration["quick_account"] = "exists"
    # The Zoom join link is private: only a viewer whose seat is confirmed --
    # signed in, or this browser's emailed registration once its link was
    # followed -- gets it, and only while the event is still ahead.
    zoom_join_url = ""
    if (
        event_state == "upcoming"
        and event.get("online_on_zoom")
        and (viewer_registered or (email_registration and not email_registration["pending"]))
    ):
        zoom_join_url = identity.zoom_join_url
    if event_state == "upcoming" and not viewer.is_authenticated:
        from django.urls import reverse

        from accounts.views.login import get_available_providers

        popup_next = reverse("login_popup_done")
        page_urls = {
            provider["id"]: provider["login_url"]
            for provider in get_available_providers(event["public_path"])
        }
        inline_login_providers = [
            {
                "id": provider["id"],
                "name": provider["name"],
                "popup_url": provider["login_url"],
                "page_url": page_urls[provider["id"]],
            }
            for provider in get_available_providers(popup_next)
        ]
    response = _render(
        request,
        "public/event_detail.html",
        path=event["public_path"],
        title=f"{event['title']} — DataTalks.Club Events",
        description=f"{event['type'].title()} on {event['display_time']}.",
        context={
            "event": event,
            # The longer speakers section with biographies only exists when a
            # speaker has one; otherwise the side column's list is the credit.
            "speaker_bios": any(speaker.get("bio_blocks") for speaker in event["speakers"]),
            "event_state": event_state,
            "viewer_registered": viewer_registered,
            "zoom_join_url": zoom_join_url,
            "inline_login_providers": inline_login_providers,
            "email_registration": email_registration,
            "account_ready": viewer.is_authenticated
            and request.session.get(QUICK_ACCOUNT_READY_SESSION_KEY) == str(identity.pk),
            "newsletter_invalid": request.GET.get("registered") == "newsletter_required",
            # Email + password sign-in, next to the providers, as on the
            # login page.
            "inline_password_login": True,
            "qna_url": qna_url,
            "og_type": "event",
            "og_image_url": (_canonical(event["banner_url"]) if event["banner_url"] else ""),
            "structured_data": _json_ld(
                entity,
                trail(("Events", "/events"), (event["title"], event["public_path"])),
            ),
        },
    )
    # An upcoming event's page takes registrations, so its count must move on
    # the next view whatever it is now (a zero must not sit at the edge either).
    if event_state == "upcoming" or event["registration_count"]:
        response["Cache-Control"] = "no-store, max-age=0, s-maxage=0"
    return response


@_public_event_route
def event_detail_without_slug(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        target = canonical_detail_path(resolve_public_id(event_id).content_id)
    except EventIdentityNotFound as exc:
        merged_path = merged_canonical_path(event_id)
        if merged_path is None:
            raise Http404 from exc
        target = merged_path
    return permanent_public_redirect(request, target=target)


@_public_event_route
def event_detail_slash_alias(request: HttpRequest, event_id: str, slug: str = "") -> HttpResponse:
    """Answer the trailing-slash spelling of an Event detail in one permanent hop.

    Links carrying a CDN-added trailing slash (``/events/<id>/<slug>/`` and ``/events/<id>/``)
    exist in the wild.  The public ID still selects the Event and the slug stays cosmetic, so a
    stale slug plus slash reaches the numeric/current-slug canonical directly, never via a second
    hop.  An ID retired by a reviewed merge answers the same single hop to the event it was
    merged into; any other unknown ID is the bounded event-route 404.
    """

    try:
        target = canonical_detail_path(resolve_public_id(event_id).content_id)
    except EventIdentityNotFound as exc:
        merged_path = merged_canonical_path(event_id)
        if merged_path is None:
            raise Http404 from exc
        target = merged_path
    return permanent_public_redirect(request, target=target)


@_public_event_route
def event_banner(request: HttpRequest, event_id: str) -> HttpResponse:
    from event_registrants.models import EventOperatorConfig

    try:
        identity = resolve_public_id(event_id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    config = EventOperatorConfig.objects.filter(event=identity).first()
    if config is None or not config.banner_image:
        raise Http404
    content_type = config.banner_content_type or "image/png"
    response = HttpResponse(bytes(config.banner_image), content_type=content_type)
    response["Cache-Control"] = "public, max-age=300"
    return response


@_public_event_route
def podcast_episode_picture(request: HttpRequest, slug: str, route: str) -> HttpResponse:
    """Serve one generated episode picture (``content.podcast_pictures``)."""

    from .models import PodcastEpisodePicture
    from .podcast_pictures import VARIANT_BY_ROUTE

    picture = PodcastEpisodePicture.objects.filter(
        episode_slug=slug, variant=VARIANT_BY_ROUTE[route]
    ).first()
    if picture is None or not picture.image:
        raise Http404
    response = HttpResponse(bytes(picture.image), content_type=picture.content_type or "image/png")
    response["Cache-Control"] = "public, max-age=300"
    return response


def event_register(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    from community_base.events.models import EventRegistration
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    from django.shortcuts import redirect
    from django.utils import timezone

    from event_registrants.models import EventOperatorConfig, EventRegistrationMeta

    if request.method != "POST":
        try:
            return redirect(canonical_detail_path(resolve_public_id(event_id).content_id))
        except EventIdentityNotFound as exc:
            merged_path = merged_canonical_path(event_id)
            if merged_path is None:
                raise Http404 from exc
            return redirect(merged_path)
    try:
        identity = resolve_public_id(event_id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    canonical = canonical_detail_path(identity.content_id)
    if not accepts_registrations(identity):
        return redirect(f"{canonical}?registered=closed")

    # A signed-in visitor registers in one click: the account supplies the
    # address and the name, and the seat is tied to the account.  Everyone else
    # posts the one-off email form.
    #
    # Registering means agreeing to the newsletter: the email form always asks,
    # and the one-click form asks only an account that is not subscribed (an
    # account's subscription is its consent on record, so its registration does
    # not record a second one).
    account = request.user if request.user.is_authenticated and request.user.email else None
    agreed = request.POST.get("newsletter_consent") in {"on", "true", "1", "yes"}
    if account is not None:
        email = account.email.strip()
        display_name = (account.get_full_name() or account.username).strip()[:200]
        consent_required = not account.newsletter_subscribed
    else:
        email = str(request.POST.get("email") or "").strip()
        display_name = str(request.POST.get("display_name") or "").strip()[:200]
        consent_required = True
    normalized_email = email.lower()
    try:
        validate_email(normalized_email)
    except ValidationError:
        return redirect(f"{canonical}?registered=invalid_email")
    if consent_required and not agreed:
        return redirect(f"{canonical}?registered=newsletter_required")
    newsletter_consent: bool | None = True if consent_required else None
    consent_version = settings.EVENT_NEWSLETTER_CONSENT_VERSION if consent_required else ""
    if account is not None and consent_required:
        account.newsletter_subscribed = True
        account.save(update_fields=["newsletter_subscribed"])

    company = str(request.POST.get("company") or "").strip()[:200]
    config = EventOperatorConfig.objects.filter(event=identity).first()
    sponsor_consent = (
        request.POST.get("sponsor_sharing_consent") in {"on", "true", "1", "yes"}
        if (config is not None and config.sponsor_sharing_enabled)
        else None
    )

    # An account registers at once: the address is the account's own.  The
    # email form only holds a pending seat; the link emailed to that address
    # confirms it (`event_registration_verify`).
    Status = EventRegistration.Status
    initial_status = Status.CONFIRMED if account is not None else Status.PENDING_VERIFICATION
    now = timezone.now()
    expires_at = None if account is not None else now + EVENT_VERIFICATION_TTL
    with transaction.atomic():
        confirmed_before = EventRegistration.objects.filter(
            event=identity, normalized_email=normalized_email, status=Status.CONFIRMED
        ).exists()
        registration, created = EventRegistration.objects.select_for_update().get_or_create(
            event=identity,
            normalized_email=normalized_email,
            defaults={
                "user": account,
                "original_email": email,
                "display_name": display_name,
                "status": initial_status,
                "verified_at": now if account is not None else None,
                "verification_expires_at": expires_at,
                "newsletter_consent": newsletter_consent,
                "newsletter_consent_version": consent_version,
                "newsletter_consent_source": "event-registration" if newsletter_consent else "",
                "newsletter_consented_at": now if newsletter_consent else None,
            },
        )
        if not created and registration.status in {Status.CANCELLED, Status.EXPIRED}:
            registration.version += 1
            registration.status = initial_status
            registration.original_email = email
            registration.display_name = display_name
            registration.cancelled_at = None
            registration.verified_at = now if account is not None else None
            registration.verification_expires_at = expires_at
            registration.newsletter_consent = newsletter_consent
            registration.newsletter_consent_version = consent_version
            registration.newsletter_consent_source = (
                "event-registration" if newsletter_consent else ""
            )
            registration.newsletter_consented_at = now if newsletter_consent else None
            registration.save()
        elif not created and registration.status == Status.PENDING_VERIFICATION:
            if account is not None:
                registration.status = Status.CONFIRMED
                registration.verified_at = now
                registration.verification_expires_at = None
            else:
                registration.display_name = display_name or registration.display_name
                registration.verification_expires_at = expires_at
            registration.save()
        if not created and newsletter_consent and not registration.newsletter_consent:
            registration.newsletter_consent = True
            registration.newsletter_consent_version = consent_version
            registration.newsletter_consent_source = "event-registration"
            registration.newsletter_consented_at = now
            registration.save()
        if account is not None and registration.user_id is None:
            registration.user = account
            registration.save(update_fields=["user", "updated_at"])

        EventRegistrationMeta.objects.update_or_create(
            registration=registration,
            defaults={
                "company": company if (config is not None and config.collect_company) else "",
                "sponsor_sharing_consent": sponsor_consent,
                "sponsor_sharing_consented_at": now if sponsor_consent is not None else None,
            },
        )
        if account is None and registration.status == Status.PENDING_VERIFICATION:
            _send_event_registration_verification(registration, identity, canonical)
        if confirmed_before is not True and registration.status == Status.CONFIRMED:
            send_registration_confirmed(registration)
    if account is not None:
        return redirect(f"{canonical}?registered=ok")
    _remember_email_registration(request, registration)
    return redirect(canonical)


#: How long the link in an email registration's confirmation message works.
EVENT_VERIFICATION_TTL = timedelta(hours=24)
#: Confirmation emails one pending registration may trigger (form submits and
#: "Resend email" together), so the form cannot be used to flood an inbox.
EVENT_VERIFICATION_MAX_SENDS = 5
EVENT_REGISTRATION_SESSION_KEY = "event_email_registrations"


def _send_event_registration_verification(registration, identity, canonical: str) -> bool:
    """Queue the "confirm your registration" email; False once the cap is hit."""

    from community_base.events.tokens import generate_registration_token

    from course_management.package_mail import mail_idempotency_key, send_package_mail

    sends = int((registration.abuse_metadata or {}).get("verification_sends", 0))
    if sends >= EVENT_VERIFICATION_MAX_SENDS:
        return False
    registration.abuse_metadata = {
        **(registration.abuse_metadata or {}),
        "verification_sends": sends + 1,
    }
    registration.save(update_fields=["abuse_metadata", "updated_at"])
    site = str(settings.COMMUNITY_BASE["SITE_URL"]).rstrip("/")
    token = generate_registration_token(
        registration,
        action="verify",
        expiry_hours=int(EVENT_VERIFICATION_TTL.total_seconds() // 3600),
    )
    send_package_mail(
        purpose="event-registration-verify",
        to=registration.normalized_email,
        context={
            "event_title": identity.title,
            "event_url": f"{site}{canonical}",
            "verify_url": f"{site}/events/registration/verify?{urlencode({'token': token})}",
        },
        idempotency_key=mail_idempotency_key(
            "event-registration-verify",
            str(registration.pk),
            str(registration.version),
            str(sends + 1),
        ),
        category="events",
        user=registration.user,
    )
    return True


def _remember_email_registration(request: HttpRequest, registration) -> None:
    """Let this browser's event card show the registration's state.

    The card never reads the address or the state from the URL: only the
    browser that submitted the form (or opened the confirmation link) sees it.
    """

    remembered = dict(request.session.get(EVENT_REGISTRATION_SESSION_KEY) or {})
    remembered[str(registration.event_id)] = {
        "registration": str(registration.pk),
        "email": registration.original_email,
    }
    request.session[EVENT_REGISTRATION_SESSION_KEY] = remembered


def _remembered_email_registration(request: HttpRequest, identity):
    from community_base.events.models import EventRegistration

    entry = (request.session.get(EVENT_REGISTRATION_SESSION_KEY) or {}).get(str(identity.pk))
    if not entry:
        return None
    return (
        EventRegistration.objects.filter(pk=entry.get("registration"), event=identity)
        .only("id", "status", "original_email", "event_id")
        .first()
    )


def event_register_resend(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    """Send the confirmation email again for this browser's pending registration."""

    from community_base.events.models import EventRegistration
    from django.shortcuts import redirect

    try:
        identity = resolve_public_id(event_id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    canonical = canonical_detail_path(identity.content_id)
    if request.method != "POST":
        return redirect(canonical)
    remembered = _remembered_email_registration(request, identity)
    outcome = "resent"
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update()
            .filter(pk=getattr(remembered, "pk", None))
            .first()
        )
        pending = EventRegistration.Status.PENDING_VERIFICATION
        if registration is None or registration.status != pending:
            return redirect(canonical)
        registration.verification_expires_at = timezone.now() + EVENT_VERIFICATION_TTL
        registration.save(update_fields=["verification_expires_at", "updated_at"])
        if not _send_event_registration_verification(registration, identity, canonical):
            outcome = "resend_limit"
    return redirect(f"{canonical}?registered={outcome}")


def event_registration_verify(request: HttpRequest) -> HttpResponse:
    """The link in the confirmation email: it, and only it, confirms the seat."""

    from community_base.events.models import EventRegistration
    from community_base.events.tokens import RegistrationTokenError, load_registration_token
    from django.shortcuts import redirect

    try:
        payload = load_registration_token(request.GET.get("token", ""), action="verify")
    except RegistrationTokenError as error:
        return _no_store(
            render(
                request,
                "public/event_registration_link.html",
                {"expired": error.code == "expired"},
                status=400,
            )
        )
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update()
            .select_related("event")
            .filter(pk=payload["registration_id"])
            .first()
        )
        if registration is None or registration.version != payload["registration_version"]:
            return _no_store(
                render(
                    request, "public/event_registration_link.html", {"expired": False}, status=400
                )
            )
        if registration.status == EventRegistration.Status.PENDING_VERIFICATION:
            registration.status = EventRegistration.Status.CONFIRMED
            registration.verified_at = timezone.now()
            registration.verification_expires_at = None
            registration.save(
                update_fields=["status", "verified_at", "verification_expires_at", "updated_at"]
            )
            send_registration_confirmed(registration)
    _remember_email_registration(request, registration)
    if registration.status == EventRegistration.Status.CONFIRMED:
        _grant_quick_account(request, registration)
    return redirect(canonical_detail_path(registration.event.content_id))


#: How long after following the confirmation link this browser may create an
#: account for the address in one click.
QUICK_ACCOUNT_TTL = timedelta(minutes=30)
QUICK_ACCOUNT_SESSION_KEY = "event_quick_account"
#: The event whose card offers the login because the address has an account.
QUICK_ACCOUNT_EXISTS_SESSION_KEY = "event_quick_account_exists"
#: The event whose page shows "Your account is ready" after a one-click create.
QUICK_ACCOUNT_READY_SESSION_KEY = "event_quick_account_ready"
#: One-click creates (successful or not) one network address may attempt per hour.
QUICK_ACCOUNT_MAX_ATTEMPTS = 10


def _grant_quick_account(request: HttpRequest, registration) -> None:
    """Following the emailed link proves this browser holds the address.

    That proof, and only that, lets this session create an account for the
    address in one click: the grant names the address, the event and the
    time, expires after ``QUICK_ACCOUNT_TTL`` and is spent by the first try.
    """

    request.session[QUICK_ACCOUNT_SESSION_KEY] = {
        "email": registration.normalized_email,
        "event": str(registration.event_id),
        "registration": str(registration.pk),
        "at": timezone.now().isoformat(),
    }


def _quick_account_grant(request: HttpRequest, identity) -> dict | None:
    from datetime import datetime

    grant = request.session.get(QUICK_ACCOUNT_SESSION_KEY)
    if not isinstance(grant, dict) or grant.get("event") != str(identity.pk):
        return None
    try:
        granted_at = datetime.fromisoformat(str(grant.get("at")))
    except ValueError:
        return None
    if timezone.now() - granted_at > QUICK_ACCOUNT_TTL:
        return None
    return grant


def _account_exists_for_email(email: str) -> bool:
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    if EmailAddress.objects.filter(email__iexact=email).exists():
        return True
    return (
        get_user_model()
        .objects.filter(Q(email__iexact=email) | Q(identity__normalized_email=email))
        .exists()
    )


def _quick_account_rate_limited(request: HttpRequest) -> bool:
    from django.core.cache import cache

    key = f"event-quick-account:{request.META.get('REMOTE_ADDR', 'unknown')}"
    cache.add(key, 0, timeout=3600)
    try:
        attempts = cache.incr(key)
    except ValueError:
        attempts = 1
    return attempts > QUICK_ACCOUNT_MAX_ATTEMPTS


def _unique_username(email: str) -> str:
    """``username`` is still a unique column; allauth does not fill it here."""

    import re
    import secrets

    from django.contrib.auth import get_user_model

    base = re.sub(r"[^a-z0-9._-]", "", email.split("@", 1)[0].lower())[:120] or "member"
    candidate = base
    while get_user_model().objects.filter(username__iexact=candidate).exists():
        candidate = f"{base}-{secrets.token_hex(3)}"
    return candidate


def _create_verified_account(request: HttpRequest, registration):
    """A site account for the address the confirmation link just proved.

    No password: members sign in with Google, GitHub or Slack, and the
    verified address row is what lets a provider's verified address find this
    account (``accounts.auth.ConsolidatingSocialAccountAdapter``).
    """

    from allauth.account.models import EmailAddress
    from community_base.events.models import EventRegistration
    from django.contrib.auth import get_user_model

    email = registration.normalized_email
    name = (registration.display_name or "").strip()
    first_name, _, last_name = name.partition(" ")
    subscribed = bool(registration.newsletter_consent)
    user = get_user_model()(
        email=email,
        username=_unique_username(email),
        first_name=first_name[:150],
        last_name=last_name.strip()[:150],
        newsletter_subscribed=subscribed,
        # The registration's newsletter answer is this person's own decision,
        # so an audience import must not overwrite it.
        newsletter_preference_changed_at=registration.newsletter_consented_at or timezone.now(),
    )
    user.set_unusable_password()
    user.save()
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    EventRegistration.objects.filter(normalized_email=email, user__isnull=True).update(user=user)
    return user


def event_quick_account(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    """Create an account in one click for the address this browser just confirmed.

    The page posts here with ``fetch`` and swaps its registration card and
    masthead in place; without JavaScript the form posts normally and lands
    back on the event page.  Outcomes: ``created`` (signed in), ``exists``
    (an account already holds the address: the visitor logs in with it; it is
    never signed in for them), ``expired`` (no valid grant in this session).
    """

    from community_base.events.models import EventRegistration
    from django.contrib.auth import login
    from django.http import JsonResponse
    from django.shortcuts import redirect

    try:
        identity = resolve_public_id(event_id)
    except EventIdentityNotFound as exc:
        raise Http404 from exc
    canonical = canonical_detail_path(identity.content_id)
    if request.method != "POST":
        return redirect(canonical)
    wants_json = "application/json" in request.headers.get("Accept", "")

    def done(outcome: str, status: int = 200) -> HttpResponse:
        if wants_json:
            return _no_store(JsonResponse({"outcome": outcome}, status=status))
        return redirect(canonical if outcome == "created" else f"{canonical}?account={outcome}")

    if request.user.is_authenticated:
        return done("created")
    if _quick_account_rate_limited(request):
        return done("rate_limited", status=429)
    grant = _quick_account_grant(request, identity)
    # Single use: whatever happens next, this grant cannot be tried again.
    request.session.pop(QUICK_ACCOUNT_SESSION_KEY, None)
    if grant is None:
        return done("expired", status=400)
    email = str(grant.get("email") or "")
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update()
            .filter(
                pk=grant.get("registration"),
                event=identity,
                normalized_email=email,
                status=EventRegistration.Status.CONFIRMED,
            )
            .first()
        )
        if registration is None:
            return done("expired", status=400)
        if _account_exists_for_email(email):
            request.session[QUICK_ACCOUNT_EXISTS_SESSION_KEY] = str(identity.pk)
            return done("exists", status=409)
        user = _create_verified_account(request, registration)
    login(request, user, backend="accounts.backends.DurableAccountBackend")
    request.session[QUICK_ACCOUNT_READY_SESSION_KEY] = str(identity.pk)
    return done("created")


# The two collections behind `public/collection_hub.html`.  Both are dated archives
# that grew past one screen — 55 articles, 98 books — so both page, through the one
# shared paginator (issues #174, #178).  Each entry is the hub's heading, its clean
# canonical base path, its catalogue's accessible label, its description, and the
# query the records come from.
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
        catalogue.articles,
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
        catalogue.books,
    ),
    "people": (
        "People",
        "/people",
        "People pages",
        (
            "Meet the authors, speakers, podcast guests, and community contributors of "
            "DataTalks.Club."
        ),
        catalogue.people,
    ),
}


@csrf_exempt
def collection_hub(request: HttpRequest, *, collection: str) -> HttpResponse:
    """The blog index and the Book of the Week archive, on the shared paginator.

    The records arrive in their published order — `(published, slug)`
    descending — and that is the order the pages are cut from.  Nothing here sorts,
    filters or renumbers; the collection differs only by which records, which base
    path and which words.
    """

    heading, base_path, catalogue_label, description, hub_records = COLLECTION_HUBS[collection]
    pagination = paginate_public_request(
        request,
        hub_records(),
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
def podcast_hub(request: HttpRequest, season: str | None = None) -> HttpResponse:
    """The podcast index: the newest season at ``/podcast``, any other at ``/podcast/s/<N>/``.

    One season per page, drawn as the /events timeline (``content.podcast_hub``).
    The query string carries nothing; the season lives in the path.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))

    description = (
        "DataTalks.Club weekly podcast episodes with data science experts, ML "
        "engineers, and AI researchers. Listen on Spotify, Apple Podcasts, YouTube."
    )
    platform_links = _hub_platform_links()
    records = catalogue.podcasts()
    if not records:
        # An empty catalogue (a fresh environment before the podcast ingest)
        # still draws the hub, with no season to list, rather than failing.
        if season is not None:
            return _no_store(HttpResponse("Page not found.", status=404))
        response = _render(
            request,
            "public/podcast_hub.html",
            path="/podcast",
            title="DataTalks.Club Podcast — DataTalks.Club",
            description=description,
            context={
                "season": None,
                "episode_total": 0,
                "podcast_platform_links": platform_links,
                "season_links": (),
            },
        )
        response["Cache-Control"] = "max-age=0, must-revalidate"
        return response
    seasons = podcast_seasons(records)
    latest_season = seasons[0].number
    selected_number = latest_season if season is None else int(season)
    season_index = next(
        (index for index, available in enumerate(seasons) if available.number == selected_number),
        None,
    )
    if season_index is None:
        return _no_store(HttpResponse("Page not found.", status=404))

    newer_season = seasons[season_index - 1] if season_index > 0 else None
    older_season = seasons[season_index + 1] if season_index + 1 < len(seasons) else None
    newer_path = (
        podcast_season_path(newer_season.number, latest=latest_season) if newer_season else ""
    )
    older_path = (
        podcast_season_path(older_season.number, latest=latest_season) if older_season else ""
    )
    title = "DataTalks.Club Podcast"
    if selected_number != latest_season:
        title = f"{title} — Season {selected_number}"
    response = _render(
        request,
        "public/podcast_hub.html",
        # The newest season's own path is the hub itself.
        path=podcast_season_path(selected_number, latest=latest_season),
        title=f"{title} — DataTalks.Club",
        description=description,
        context={
            "season": hub_season(seasons[season_index]),
            "episode_total": sum(len(available.episodes) for available in seasons),
            "podcast_platform_links": platform_links,
            "season_links": tuple(
                {
                    "number": available.number,
                    "path": podcast_season_path(available.number, latest=latest_season),
                }
                for available in seasons
            ),
            "newer_season": newer_season,
            "newer_path": newer_path,
            "older_season": older_season,
            "older_path": older_path,
            "previous_url": _canonical(newer_path) if newer_path else "",
            "next_url": _canonical(older_path) if older_path else "",
        },
    )
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response


def _hub_platform_links() -> tuple[PlatformLink, ...]:
    """The show's listener destinations in the hub's order (no creator dashboard)."""

    by_provider = {
        link.provider: link for link in podcast_platform_links(catalogue.podcast_platforms())
    }
    return tuple(by_provider[key] for key in HUB_LISTEN_ORDER if key in by_provider)


def _article_faq_structured_data(article: dict, faq: ArticleFaq | None) -> tuple[dict, ...]:
    """Return the FAQPage node one article's FAQ section publishes.

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
    article = catalogue.article(slug)
    if article is None:
        raise Http404
    # The article's FAQ section, if it publishes one, comes from its own
    # document row alongside the body.  An article without one gets `None`, and
    # the page then draws no FAQ region.
    faq = article_faq(slug)
    article_trail = trail(("Blog", "/blog"), (article["title"], article["public_path"]))
    social_image = article.get("preview_og_path") or article["image_path"]
    return _render(
        request,
        "public/article_detail.html",
        path=article["public_path"],
        title=f"{article['title']} — DataTalks.Club",
        description=article["description"],
        context={
            "record": article,
            "breadcrumbs": article_trail,
            # The design system reading page (issue #179) renders this composed value:
            # the byline joined to the people records, the publication date and
            # reading estimate the design writes, and the body as prose sections.
            "article": article_view(article, catalogue.people_by_slug(), faq),
            "og_type": "article",
            # The generated social card when the article has one, else its own image.
            "og_image_url": _canonical(social_image) if social_image else "",
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
                    **({"image": _canonical(social_image)} if social_image else {}),
                },
                article_trail,
                # The legacy accordion published FAQPage data with every one of
                # these ten sections, and the course FAQ pages publish the same
                # node today (`review_views._faq_structured_data`).  Restoring it
                # here keeps the rich result the article already earned; an
                # article without a recovered FAQ adds no node at all.
                extra=_article_faq_structured_data(article, faq),
            ),
        },
    )


def _render_podcast_detail(request: HttpRequest, episode: dict) -> HttpResponse:
    from .podcast_pictures import Variant, stored_path

    # The generated 1200 x 630 social picture is the page's preview image; the
    # episode's own artwork is only the fallback for an episode without one.
    image_path = stored_path(episode["slug"], Variant.SOCIAL) or (
        episode["image_path"]
        if episode.get("media_available") and episode.get("image_path")
        else ""
    )
    poster_path = stored_path(episode["slug"], Variant.POSTER) or (
        episode["image_path"]
        if episode.get("media_available") and episode.get("image_path")
        else ""
    )
    # The picture that sits beside the title in the hero, the way an event page
    # pairs its picture with its title: the generated 1000 x 1000 card, else
    # the episode's own (landscape) artwork, else none -- the hero is then one
    # column of words.
    hero_card_path = stored_path(episode["slug"], Variant.CARD)
    hero_picture_path = hero_card_path or (
        episode["image_path"]
        if episode.get("media_available") and episode.get("image_path")
        else ""
    )
    try:
        related_matches = wiki_content.related_episode_matches(episode)
    except ImproperlyConfigured:
        # The same rule as the graph band below: a graph the catalogue cannot
        # supply leaves the page up, and the band falls back to the season.
        related_matches = ()
    podcasts = catalogue.podcasts()
    related = related_episodes(episode, podcasts, related_matches)
    latest_season = max(item["season"] for item in podcasts)
    try:
        episode_graph = wiki_content.episode_graph(episode)
    except ImproperlyConfigured:
        # A graph the published catalogue cannot supply must not take an
        # otherwise useful episode page down or expose graph-contract details.
        # The next import can repair the data while the page keeps its explicit
        # unavailable state.
        episode_graph = wiki_content.unavailable_episode_graph(episode)
    connections = wiki_content.episode_connections(episode_graph, pages=wiki_reader.wiki_pages())
    related_cards = hub_episodes(related.records)
    # The way back is the episode's own season page: "Podcast / Season 25".
    # The season route serves every season, the newest included, so the trail
    # always names a real place to land.
    episode_trail = trail(
        ("Podcast", "/podcast"),
        (f"Season {episode['season']}", reverse("podcast-season", args=[episode["season"]])),
        (episode["title"], episode["public_path"]),
    )
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
    episode_page = episode_view(
        episode,
        people_by_slug=catalogue.people_by_slug(),
        resource_podcast_records=podcasts,
    )
    response = _render(
        request,
        "public/podcast_detail.html",
        path=episode["public_path"],
        title=f"{episode['title']} — DataTalks.Club Podcast",
        description=episode["description"],
        context={
            "record": episode,
            "episode": episode_page,
            "guest_cards": guest_cards(episode_page.guests),
            "related": related,
            "video_poster_path": poster_path,
            "hero_picture_path": hero_picture_path,
            "hero_picture_square": bool(hero_card_path),
            "related_cards": related_cards,
            "connections": connections,
            "topic_reading": topic_reading(card.url for card in connections.reading),
            "season_path": podcast_season_path(episode["season"], latest=latest_season),
            "episode_graph": episode_graph,
            "og_type": "article",
            "og_image_url": _canonical(image_path) if image_path else "",
            "published_time": episode["published"] or "",
            "structured_data": _json_ld(
                episode_entity,
                episode_trail,
            ),
            "breadcrumbs": episode_trail,
        },
    )
    response["Cache-Control"] = (
        "no-store, max-age=0"
        if request.META.get("QUERY_STRING", "")
        else "max-age=0, must-revalidate"
    )
    return response


def _podcast_by_public_id(episode_id: str) -> dict:
    matches = [
        episode
        for episode in catalogue.podcasts()
        if podcast_public_id(
            season=episode["season"],
            episode=episode["episode"],
        )
        == episode_id
    ]
    if len(matches) != 1:
        raise Http404
    return matches[0]


@csrf_exempt
def podcast_detail_by_id(
    request: HttpRequest,
    episode_id: str,
    slug: str,
) -> HttpResponse:
    """Resolve an episode by stable ID and treat its title slug as cosmetic."""

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    episode = _podcast_by_public_id(episode_id)
    if request.path_info != episode["public_path"]:
        return permanent_public_redirect(request, target=episode["public_path"])
    return _render_podcast_detail(request, episode)


@csrf_exempt
def podcast_transcript_text(
    request: HttpRequest,
    episode_id: str,
    slug: str,
) -> HttpResponse:
    """The episode transcript as a UTF-8 plain-text download, from the catalogue."""

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    episode = _podcast_by_public_id(episode_id)
    download_path = f"{episode['public_path']}/transcript.txt"
    if request.path_info != download_path:
        return permanent_public_redirect(request, target=download_path)
    view = episode_view(episode)
    if not view.transcript:
        raise Http404
    filename = "dtc-podcast-" + episode["public_path"].removeprefix("/podcast/").replace("/", "-")
    response = HttpResponse(transcript_text(view), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.txt"'
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response


@csrf_exempt
def podcast_detail_by_id_without_slug(request: HttpRequest, episode_id: str) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    episode = _podcast_by_public_id(episode_id)
    return permanent_public_redirect(request, target=episode["public_path"])


@csrf_exempt
def podcast_detail_slash_alias(
    request: HttpRequest, episode_id: str, slug: str = ""
) -> HttpResponse:
    """Redirect ``/podcast/<sNeM>[/<slug>]/`` to the episode canonical in one hop.

    The stable episode ID selects the record and the title slug stays cosmetic, exactly as on
    the slashless route; only the trailing slash differs.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    episode = _podcast_by_public_id(episode_id)
    return permanent_public_redirect(request, target=episode["public_path"])


@csrf_exempt
def book_dated_redirect(request: HttpRequest, source_slug: str) -> HttpResponse:
    """Redirect a book's dated address (``/books/<YYYYMMDD-slug>.html``, bare or with a
    trailing slash) permanently to its dateless canonical.

    The mapping is the catalogue's own: the dated spelling is the book's source slug,
    and the target is the public path the book's synced record publishes.  A row
    synced before the dateless path existed still names the dated path as its
    canonical; that page renders rather than redirecting to itself.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    book = catalogue.book(source_slug)
    if book is None:
        raise Http404
    if request.path_info != book["public_path"]:
        return permanent_public_redirect(request, target=book["public_path"])
    return _render_book_detail(request, book)


@require_safe
def book_detail(request: HttpRequest, slug: str) -> HttpResponse:
    book = catalogue.book_at(f"/books/{slug}.html")
    if book is None:
        raise Http404
    return _render_book_detail(request, book)


def _render_book_detail(request: HttpRequest, book: dict) -> HttpResponse:
    # The book's own summary is source Markdown, not plain text — it used to be
    # printed literally (asterisks and all).  It goes through the same
    # Markdown-then-shared-sanitizer path an article body uses
    # (content.article_content.render_body_markdown), so a source list becomes a
    # real `<ul>` and `**bold**` becomes `<strong>` without trusting the source.
    summary_html, summary_is_block = (
        render_body_markdown(book["summary"]) if book["summary"] else ("", False)
    )
    book_trail = trail(("Books", "/books"), (book["title"], book["public_path"]))
    return _render(
        request,
        "public/book_detail.html",
        path=book["public_path"],
        title=f"{book['title']} — DataTalks.Club Books",
        description=book["description"],
        context={
            "record": book,
            "breadcrumbs": book_trail,
            "summary_html": summary_html,
            "summary_is_block": summary_is_block,
            "threads": book_threads(book["archive"], book["author_profiles"]),
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
                book_trail,
            ),
        },
    )


@require_safe
def person_detail(request: HttpRequest, slug: str) -> HttpResponse:
    person = catalogue.person(slug)
    if person is None:
        raise Http404
    person_trail = trail(("People", "/people"), (person["title"], person["public_path"]))
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
                person_trail,
            ),
            "breadcrumbs": person_trail,
        },
    )


@csrf_exempt
def wiki_hub(request: HttpRequest) -> HttpResponse:
    """The Wiki landing page: search first, then the ways into a topic.

    The hub has no query state.  A search term sent here (an old `/wiki?q=`
    link) goes to the results page, and any other query, such as the retired
    catalogue's `?page=`, goes to the clean hub, since every topic is on it.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    query = request.GET.get("q", "").strip()[:200]
    if query:
        response = HttpResponseRedirect(f"/wiki/search?{urlencode({'q': query})}")
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response
    if request.GET:
        return HttpResponsePermanentRedirect("/wiki")
    stats = wiki_hub_content.stats()
    return _render(
        request,
        "public/wiki_hub.html",
        path="/wiki",
        title="Wiki — DataTalks.Club",
        description=(
            "Notes on data and AI topics, written from DataTalks.Club podcast episodes, "
            "events and books, with links to the sources."
        ),
        context={
            "stats": stats,
            "wiki_graph": home_wiki_graph(),
            "featured_roadmaps": wiki_hub_content.featured_roadmaps(),
            "featured_topics": wiki_hub_content.featured_topics(),
            "areas": wiki_hub_content.areas(),
            "kind_rows": wiki_hub_content.kind_rows(),
            "busiest": wiki_hub_content.busiest(),
            "start_goals": wiki_hub_content.start_goals(),
            "letter_groups": wiki_hub_content.letter_groups(),
        },
    )


#: How many related topics a wiki page names as chips under its summary.
WIKI_TOPIC_CHIPS = 8
#: How many episodes a wiki page lists before the rest fold behind "Show all".
WIKI_EPISODES_FOLD = 10


@require_safe
def wiki_detail(request: HttpRequest, slug: str) -> HttpResponse:
    page = wiki_reader.wiki_page(slug)
    if page is None:
        raise Http404
    wiki_trail = trail(("Wiki", "/wiki"), (page["title"], page["public_path"]))
    # The page's own "Related Pages" list gives way to the designed block drawn
    # from its curated ``related:`` list, so the body ends where that section
    # starts; anything after it (a later section) still renders.
    related_cards = wiki_content.wiki_related_cards(page, pages=wiki_reader.wiki_pages())
    blocks = list(page["blocks"])
    start = next(
        (
            index
            for index, block in enumerate(blocks)
            if block.get("kind") == "heading"
            and str(block.get("text", "")).strip().casefold() == "related pages"
        ),
        None,
    )
    closing: list = []
    if start is not None and related_cards:
        end = next(
            (
                index
                for index in range(start + 1, len(blocks))
                if blocks[index].get("kind") == "heading"
                and blocks[index].get("level", 2) <= blocks[start].get("level", 2)
            ),
            len(blocks),
        )
        blocks, closing = blocks[:start], blocks[end:]
    citations = wiki_content.wiki_citations(blocks, closing)
    further = wiki_content.wiki_relation_groups(page).further
    return _render(
        request,
        "public/wiki_detail.html",
        path=page["public_path"],
        title=f"{page['title']} — DataTalks.Club Wiki",
        description=page["summary"],
        context={
            "record": page,
            "body_blocks": citations.body,
            "closing_blocks": citations.closing,
            "wiki_sources": citations.sources,
            "related_cards": related_cards,
            "topic_chips": related_cards[:WIKI_TOPIC_CHIPS],
            "topic_reading": topic_reading(
                (item.url for item in further),
                event_titles=(
                    item.label for item in further if item.kind == "event" and not item.url
                ),
            ),
            "topic_episodes": topic_episodes(page["public_path"]),
            "episodes_fold": WIKI_EPISODES_FOLD,
            "breadcrumbs": wiki_trail,
            "og_type": "article",
            "structured_data": _json_ld(
                {
                    "@type": "Article",
                    "url": _canonical(page["public_path"]),
                    "headline": page["title"],
                    "description": page["summary"],
                },
                wiki_trail,
            ),
        },
    )


_WIKI_FIXED_SLASH_TARGETS = {
    "graph": "/wiki/graph",
    "special-pages": "/wiki/special-pages",
}


@csrf_exempt
def wiki_slash_alias(request: HttpRequest, slug: str) -> HttpResponse:
    """Redirect the trailing-slash spelling of a rendered Wiki page in one hop.

    Only a page the Wiki actually renders is a target: a published editorial slug, the graph,
    the special-pages index, or one of its known categories.  Exactly one trailing slash is
    removed; nothing else about the path is guessed, so any other spelling stays a 404.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    target = _WIKI_FIXED_SLASH_TARGETS.get(slug)
    if target is None and slug.startswith("special-pages/"):
        category = slug.removeprefix("special-pages/")
        if category in WIKI_SPECIAL_CATEGORIES:
            target = f"/wiki/special-pages/{category}"
    if target is None:
        page = wiki_reader.wiki_page(slug)
        if page is None:
            raise Http404
        target = page["public_path"]
    return permanent_public_redirect(request, target=target)


@csrf_exempt
def wiki_search(request: HttpRequest) -> HttpResponse:
    """`/wiki/search?q=` is the ranked results page; bare `/wiki/search` is a topic.

    The podwiki publishes an editorial page about search at this slug, and its
    graph links topic nodes to `/wiki/search?q=<term>`, so the term is the
    switch: without `q` the topic page renders, a blank term goes back to the
    hub, and a term renders every matching topic, best first.  The live
    dropdown on the hub reads the same ranking from `wiki_search_live`.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    if "q" not in request.GET:
        return wiki_detail(request, "search")
    query = " ".join(request.GET.get("q", "").split())[:200]
    if not query:
        return _no_store(HttpResponseRedirect("/wiki"))
    response = _render(
        request,
        "public/wiki_search.html",
        path="/wiki/search",
        title=f"{query} — Search — DataTalks.Club Wiki",
        description="Wiki topics matching your search.",
        context={
            "query": query,
            "results": wiki_hub_content.search(query),
            "stats": wiki_hub_content.stats(),
        },
    )
    # Search results are the spec's explicitly private surface.  The route
    # registry vetoes sharing a `q` request too; this keeps the contract true
    # at the view even if that entry ever moves.
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


@require_safe
def wiki_search_live(request: HttpRequest) -> JsonResponse:
    """The hub's live search: the top ranked topics for `q`, as JSON."""

    query = " ".join(request.GET.get("q", "").split())[:200]
    results = wiki_hub_content.search(query) if query else ()
    response = JsonResponse(
        {
            "query": query,
            "total": len(results),
            "results": [topic.as_json() for topic in results[: wiki_hub_content.LIVE_RESULTS]],
            "all_url": f"/wiki/search?{urlencode({'q': query})}" if query else "",
        }
    )
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


@require_safe
def wiki_graph(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "public/wiki_graph.html",
        path="/wiki/graph",
        title="Wiki — DataTalks.Club",
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
    return JsonResponse(catalogue.wiki_graph())


@require_safe
def wiki_search_json(request: HttpRequest) -> JsonResponse:
    return JsonResponse(catalogue.wiki_search())


@require_safe
def wiki_special(request: HttpRequest, category: str = "all") -> HttpResponse:
    pages = wiki_reader.wiki_pages()
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
        for page in wiki_reader.wiki_pages()[-30:]
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
    if public_path not in catalogue.wiki_asset_paths():
        raise Http404
    path = WIKI_ASSET_ROOT / asset
    if not path.is_file() or path.is_symlink():
        raise Http404
    return FileResponse(path.open("rb"), content_type="image/png")


@require_safe
def media(request: HttpRequest, media_path: str) -> HttpResponseBase:
    """Serve one recorded public image through the configured media store.

    The public URL, the response status, and the response header shape are unchanged
    from the era when the bytes lived in the git working tree.  An unrecognised path is
    still an ordinary ``404``; a *recorded* object that cannot be retrieved and verified
    fails closed as an uncacheable ``502`` so an edge cache can never memorise an origin
    outage as "not found".
    """

    public_path = f"/images/{media_path}"
    record = catalogue.media_at(public_path)
    if record is None:
        raise Http404
    store = media_store()
    try:
        payload = read_media_object(store, record)
    except MediaStoreError as error:
        _record_media_failure(request, store=store, record=record, error=error)
        return _media_unavailable()
    return FileResponse(
        BytesIO(payload),
        content_type=record["content_type"],
        filename=record_filename(record),
    )


def _media_unavailable() -> HttpResponse:
    """Return the fail-closed media response: no bytes, no detail, no caching."""

    response = HttpResponse(
        "Image temporarily unavailable.\n",
        status=502,
        content_type="text/plain; charset=utf-8",
    )
    response["Cache-Control"] = "no-store"
    return response


def _record_media_failure(
    request: HttpRequest,
    *,
    store: MediaStore,
    record: dict[str, Any],
    error: MediaStoreError,
) -> None:
    """Emit exactly one redacted structured event for a failed media read."""

    try:
        key = record_key(record)
    except MediaStoreError:
        key = ""
    record_event(
        "public_media_object_unavailable",
        request=request,
        properties={
            "media_store_backend": store.name,
            "media_failure_reason": error.reason,
            # The record key is a public URL fragment, but it is still reduced to a
            # digest so no filename, bucket, or key ever reaches the log stream.
            "media_record_digest": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        },
    )


def _section_records(section: str) -> tuple[tuple[str, str], ...]:
    # /slack is a database-owned page: it is listed when a row publishes it and
    # absent when none does, rather than being a permanent entry that 404s.
    slack_entries = ((SLACK_PUBLIC_PATH, ""),) if catalogue.slack_page() is not None else ()
    static_sections = {
        "main": (
            ("/", ""),
            ("/newsletter", ""),
            ("/terms", ""),
            ("/privacy", ""),
            ("/impressum", ""),
            *slack_entries,
        ),
    }
    if section in static_sections:
        return static_sections[section]
    if section == "docs":
        # The docs read model is the single source of the published tree, so the
        # sitemap enumerates every synced page instead of a hand-picked sample
        # that drifts as pages are added, renamed or retired.
        seen_paths: set[str] = set()
        docs_records: list[tuple[str, str]] = []
        for page in docs_pages():
            public_path = str(page["public_path"])
            if public_path in seen_paths:
                continue
            seen_paths.add(public_path)
            docs_records.append((public_path, ""))
        return tuple(docs_records)
    if section == "faq":
        return (("/faq/", ""),) + tuple((course["public_path"], "") for course in faq_courses())
    if section == "blog":
        return (("/blog", ""),) + tuple(
            (record["public_path"], record["published"][:10]) for record in catalogue.articles()
        )
    if section == "podcast":
        return (("/podcast", ""),) + tuple(
            (record["public_path"], record["published"][:10]) for record in catalogue.podcasts()
        )
    if section == "books":
        return (("/books", ""),) + tuple(
            (record["public_path"], record["published"][:10]) for record in catalogue.books()
        )
    if section == "people":
        return (("/people", ""),) + tuple(
            (record["public_path"], "") for record in catalogue.people()
        )
    if section == "events":
        return (("/events", ""), ("/events/past", "")) + tuple(
            (record["public_path"], record["starts_at"][:10])
            for record in published_event_records()
        )
    if section == "courses":
        family_slugs = (
            Cohort.objects.filter(visible=True, course__visible=True)
            .order_by("course__slug")
            .values_list("course__slug", flat=True)
            .distinct()
        )
        entries = [("/courses", "")]
        entries.extend((f"/courses/{slug}", "") for slug in family_slugs)
        # Families with a shared current curriculum also publish their
        # cohort-free module and lesson paths.  Query-context URLs, cohort
        # landings, homework/operations, and GitHub archives stay out.
        shared_modules = (
            SharedModule.objects.filter(
                published=True,
                curriculum__course__visible=True,
            )
            .order_by("curriculum__course__slug", "position", "id")
            .values_list("curriculum__course__slug", "slug")
        )
        entries.extend((f"/courses/{course}/{module}", "") for course, module in shared_modules)
        shared_lessons = (
            SharedLesson.objects.filter(
                published=True,
                module__published=True,
                module__curriculum__course__visible=True,
            )
            .order_by("module__curriculum__course__slug", "module__slug", "position", "id")
            .values_list("module__curriculum__course__slug", "module__slug", "slug")
        )
        entries.extend(
            (f"/courses/{course}/{module}/{lesson}", "")
            for course, module, lesson in shared_lessons
        )
        return tuple(entries)
    if section == "wiki":
        discovery = (
            ("/wiki", ""),
            ("/wiki/graph", ""),
            ("/wiki/special-pages", ""),
            *((f"/wiki/special-pages/{category}", "") for category in WIKI_SPECIAL_CATEGORIES),
        )
        return discovery + tuple((record["public_path"], "") for record in wiki_reader.wiki_pages())
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
