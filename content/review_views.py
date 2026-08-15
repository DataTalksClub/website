from __future__ import annotations

import json
from typing import Any

from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_safe

from courses.models.course import Course

from .docs_projection import (
    DOCS_ROOT_PATH,
    DOCS_SEARCH_URL,
    docs_asset_path,
    docs_breadcrumbs,
    docs_children,
    docs_navigation_tree,
    docs_parent,
    docs_sequential_navigation,
    render_docs_markdown,
)
from .docs_projection import (
    docs_page as projected_docs_page,
)
from .faq_data import (
    faq_answer_text,
    faq_asset_content_type,
    faq_asset_path,
    faq_courses,
    faq_questions,
    render_faq_answer,
)
from .faq_data import (
    faq_course as faq_course_data,
)
from .review_projection import (
    SLACK_PUBLIC_PATH,
    event_groups,
    projected_events,
    projection_context,
    review_projection,
)


def _canonical(path: str) -> str:
    return f"https://datatalks.club{path}"


def _render(
    request: HttpRequest,
    template_name: str,
    *,
    path: str,
    title: str,
    description: str,
    context: dict[str, Any] | None = None,
) -> HttpResponse:
    page_context = {
        "canonical_url": _canonical(path),
        "seo_title": title,
        "seo_description": description,
        **(context or {}),
    }
    return render(request, template_name, page_context)


@require_safe
def events(request: HttpRequest) -> HttpResponse:
    groups = event_groups()
    return _render(
        request,
        "review/events.html",
        path="/events.html",
        title="Events — DataTalks.Club",
        description="Upcoming and recorded DataTalks.Club workshops, webinars, and conversations.",
        context={"upcoming_events": groups.upcoming, "recent_events": groups.recent},
    )


@require_safe
def articles(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "review/articles.html",
        path="/articles.html",
        title="Articles — DataTalks.Club",
        description="Practical articles and learning guides from the DataTalks.Club community.",
        context=projection_context("article"),
    )


@require_safe
def article_detail(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "review/article_detail.html",
        path="/blog/ai-dev-tools-zoomcamp.html",
        title="AI Dev Tools Zoomcamp 2026 — DataTalks.Club",
        description=review_projection()["article"]["description"],
        context=projection_context("article"),
    )


@require_safe
def podcast(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "review/podcast.html",
        path="/podcast.html",
        title="Podcast — DataTalks.Club",
        description="Conversations with people who build and operate data and AI systems.",
        context=projection_context("podcast"),
    )


@require_safe
def podcast_detail(request: HttpRequest) -> HttpResponse:
    projection = review_projection()
    episode = projection["podcast"]
    person = projection["people"][episode["guest"]]
    return _render(
        request,
        "review/podcast_detail.html",
        path=episode["public_path"],
        title=f"{episode['title']} — DataTalks.Club Podcast",
        description=episode["summary"],
        context={
            "podcast": episode,
            "person": person,
        },
    )


@require_safe
def person_detail(request: HttpRequest) -> HttpResponse:
    projection = review_projection()
    person = projection["people"]["aleksandrkim"]
    recorded_event = next(
        event for event in projected_events() if event["speaker"] == "aleksandrkim"
    )
    return _render(
        request,
        "review/person_detail.html",
        path=person["public_path"],
        title=f"{person['name']} — DataTalks.Club",
        description=person["bio"],
        context={
            "person": person,
            "recorded_event": recorded_event,
            "podcast": projection["podcast"],
        },
    )


@require_safe
def books(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "review/books.html",
        path="/books.html",
        title="Books — DataTalks.Club",
        description="DataTalks.Club book discussions with authors and community readers.",
        context=projection_context("book"),
    )


@require_safe
def book_detail(request: HttpRequest) -> HttpResponse:
    book = review_projection()["book"]
    return _render(
        request,
        "review/book_detail.html",
        path=book["public_path"],
        title=f"{book['title']} — DataTalks.Club Books",
        description=book["description"],
        context=projection_context("book"),
    )


@require_safe
def docs_home(request: HttpRequest) -> HttpResponse:
    document = projected_docs_page(DOCS_ROOT_PATH)
    if document is None:
        raise Http404("Documentation home is unavailable.")
    rendered, headings = render_docs_markdown(document)
    navigation = docs_navigation_tree()
    return _render(
        request,
        "review/docs_home.html",
        path="/docs/",
        title="Documentation — DataTalks.Club",
        description=document.get("description")
        or "Guides for DataTalks.Club courses and community learning.",
        context={
            "docs": document,
            "docs_html": rendered,
            "docs_headings": headings,
            "docs_navigation": navigation.root.children,
            "docs_search_url": DOCS_SEARCH_URL,
        },
    )


def _docs_detail_context(
    document: dict[str, Any], rendered: str, headings: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    previous, following = docs_sequential_navigation(document)
    return {
        "docs": document,
        "docs_html": rendered,
        "docs_headings": headings,
        "docs_breadcrumbs": docs_breadcrumbs(document),
        "docs_children": docs_children(document.get("public_path")),
        "docs_navigation": docs_navigation_tree().root.children,
        "docs_parent": docs_parent(document),
        "docs_previous": previous,
        "docs_next": following,
        "docs_search_url": DOCS_SEARCH_URL,
    }


@require_safe
def docs_getting_started(request: HttpRequest) -> HttpResponse:
    document = projected_docs_page("/docs/courses/ai-dev-tools-zoomcamp/getting-started/")
    if document is None:
        raise Http404("Documentation page is unavailable.")
    rendered, headings = render_docs_markdown(document)
    return _render(
        request,
        "review/docs_detail.html",
        path=document["public_path"],
        title=f"{document['title']} — AI Dev Tools Zoomcamp Docs",
        description=document.get("description") or "AI Dev Tools Zoomcamp documentation.",
        context=_docs_detail_context(document, rendered, headings),
    )


@require_safe
def docs_page(request: HttpRequest, doc_path: str) -> HttpResponse:
    public_path = f"/docs/{doc_path.lstrip('/')}"
    if not public_path.endswith("/"):
        public_path += "/"
    document = projected_docs_page(public_path)
    if document is None:
        raise Http404("Documentation page is unavailable.")
    rendered, headings = render_docs_markdown(document)
    return _render(
        request,
        "review/docs_detail.html",
        path=document["public_path"],
        title=f"{document['title']} — DataTalks.Club Documentation",
        description=document.get("description") or "DataTalks.Club documentation.",
        context=_docs_detail_context(document, rendered, headings),
    )


@require_safe
def docs_asset(request: HttpRequest, asset: str) -> FileResponse:
    resolved = docs_asset_path(asset)
    if resolved is None:
        raise Http404("Documentation asset is unavailable.")
    path, content_type = resolved
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_safe
def faq_home(request: HttpRequest) -> HttpResponse:
    courses = faq_courses()
    return _render(
        request,
        "review/faq_home.html",
        path="/faq/",
        title="Frequently Asked Questions — DataTalks.Club",
        description="Answers to common questions about DataTalks.Club courses.",
        context={
            "faq_courses": courses,
            "primary_navigation_current": "faq",
        },
    )


def _faq_sections(course: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            **section,
            "questions": tuple(
                {
                    **question,
                    "rendered_answer": render_faq_answer(question),
                }
                for question in section["questions"]
            ),
        }
        for section in course["sections"]
    )


def _faq_structured_data(course: dict[str, Any]) -> str:
    canonical = _canonical(course["public_path"])
    questions = []
    for question in faq_questions(course):
        question_url = f"{canonical}#{question['id']}"
        questions.append(
            {
                "@type": "Question",
                "@id": question_url,
                "url": question_url,
                "name": question["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": faq_answer_text(question),
                },
            }
        )
    graph = [
        {
            "@type": "FAQPage",
            "@id": canonical,
            "url": canonical,
            "name": f"{course['name']} FAQ",
            "mainEntity": questions,
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": _canonical("/"),
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": "FAQ",
                    "item": _canonical("/faq/"),
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": course["name"],
                    "item": canonical,
                },
            ],
        },
    ]
    return json.dumps(
        {"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False
    ).replace("<", "\\u003c")


@require_safe
def faq_course(request: HttpRequest, course_slug: str) -> HttpResponse:
    course = faq_course_data(course_slug)
    if course is None:
        raise Http404("FAQ course is unavailable.")
    return _render(
        request,
        "review/faq_detail.html",
        path=course["public_path"],
        title=f"{course['name']} FAQ — DataTalks.Club",
        description=f"Answers to common questions about {course['name']}.",
        context={
            "faq_course": course,
            "faq_sections": _faq_sections(course),
            "primary_navigation_current": "faq",
            "structured_data": _faq_structured_data(course),
        },
    )


@require_safe
def faq_ai_dev_tools(request: HttpRequest) -> HttpResponse:
    return faq_course(request, "ai-dev-tools-zoomcamp")


@require_safe
def faq_courses_json(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        [
            {
                "course": course["slug"],
                "course_name": course["name"],
                "path": f"/json/{course['slug']}.json",
                "questions_count": course["question_count"],
            }
            for course in faq_courses()
        ],
        safe=False,
        json_dumps_params={"ensure_ascii": False},
    )


@require_safe
def faq_course_json(request: HttpRequest, course_slug: str) -> JsonResponse:
    course = faq_course_data(course_slug)
    if course is None:
        raise Http404("FAQ course is unavailable.")
    return JsonResponse(
        [
            {
                "id": question["id"],
                "course": question["course"],
                "section": question["section"],
                "question": question["question"],
                "answer": question["answer"],
            }
            for question in faq_questions(course)
        ],
        safe=False,
        json_dumps_params={"ensure_ascii": False},
    )


@require_safe
def faq_asset(request: HttpRequest, course_slug: str, asset: str) -> FileResponse:
    path = faq_asset_path(course_slug, asset)
    if path is None:
        raise Http404("FAQ asset is unavailable.")
    return FileResponse(path.open("rb"), content_type=faq_asset_content_type(path))


@csrf_exempt
def slack(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        response = HttpResponseNotAllowed(("GET", "HEAD"))
        response["Cache-Control"] = "no-store, max-age=0"
        return response
    page = review_projection()["slack"]
    # Keep the rendered context canonical even while an older source projection is being
    # replaced.  The checked projection validator enforces the same path at load time.
    page = {**page, "public_path": SLACK_PUBLIC_PATH}
    context = projection_context("slack")
    context["slack"] = page
    return _render(
        request,
        "review/slack.html",
        path=page["public_path"],
        title="Join our Slack — DataTalks.Club",
        description=page["lead"],
        context=context,
    )


@require_safe
def course_family(request: HttpRequest) -> HttpResponse:
    course = review_projection()["course"]
    legacy_course = Course.objects.filter(slug=course["cohort"]["legacy_platform_slug"]).first()
    context = projection_context("course")
    context.update({"legacy_course": legacy_course})
    return _render(
        request,
        "review/course_family.html",
        path=course["public_path"],
        title=f"{course['title']} — DataTalks.Club",
        description=course["summary"],
        context=context,
    )


@require_safe
def course_cohort(request: HttpRequest) -> HttpResponse:
    course = review_projection()["course"]
    cohort = course["cohort"]
    legacy_course = Course.objects.filter(slug=cohort["legacy_platform_slug"]).first()
    path = f"{course['public_path']}/cohorts/{cohort['slug']}"
    return _render(
        request,
        "review/course_cohort.html",
        path=path,
        title=f"{cohort['title']} — DataTalks.Club",
        description=f"The 2026 cohort of {course['title']}, starting August 31, 2026.",
        context={
            "course": course,
            "cohort": cohort,
            "legacy_course": legacy_course,
        },
    )


@require_GET
def registration_preview(request: HttpRequest) -> HttpResponse:
    course = review_projection()["course"]
    cohort = course["cohort"]
    path = f"{course['public_path']}/cohorts/{cohort['slug']}/registration-preview/"
    return _render(
        request,
        "review/registration_preview.html",
        path=path,
        title=f"Registration — {cohort['title']}",
        description=f"Registration for {cohort['title']} opens soon.",
        context={"course": course, "cohort": cohort},
    )
