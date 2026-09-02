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
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_safe

from courses.models.cohort import Cohort
from courses.services.public_course_catalog import latest_visible_cohort_for_families

from .docs_presentation import (
    docs_body_without_primary_heading,
    docs_context_items,
    docs_context_root,
    docs_curriculum,
    docs_home_areas,
    docs_home_course_groups,
    docs_local_sequence,
)
from .docs_projection import (
    DOCS_ROOT_PATH,
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
def docs_home(request: HttpRequest) -> HttpResponse:
    document = projected_docs_page(DOCS_ROOT_PATH)
    if document is None:
        raise Http404("Documentation home is unavailable.")
    rendered, headings = render_docs_markdown(document)
    navigation = docs_navigation_tree()
    heading_id, rendered_body = docs_body_without_primary_heading(rendered)
    course_families, course_support = docs_home_course_groups(navigation)
    return _render(
        request,
        "review/docs_home.html",
        path="/docs/",
        title="Documentation — DataTalks.Club",
        description=document.get("description")
        or "Guides for DataTalks.Club courses and community learning.",
        context={
            "docs": document,
            "docs_heading_id": heading_id,
            "docs_heading_title": headings[0]["title"] if headings else document["title"],
            "docs_html": rendered_body,
            "docs_headings": headings,
            "docs_navigation": navigation.root.children,
            "docs_courses_root": navigation.by_path.get("/docs/courses/"),
            "docs_course_families": course_families,
            "docs_course_support": course_support,
            "docs_areas": docs_home_areas(navigation),
            "primary_navigation_current": "docs",
        },
    )


def _docs_detail_context(
    document: dict[str, Any], rendered: str, headings: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    previous, following = docs_sequential_navigation(document)
    public_path = str(document["public_path"])
    navigation = docs_navigation_tree()
    heading_id, rendered_body = docs_body_without_primary_heading(rendered)
    context_root = docs_context_root(navigation, public_path)
    local_previous, local_following = docs_local_sequence(navigation, public_path)
    return {
        "docs": document,
        "docs_heading_id": heading_id,
        "docs_heading_title": headings[0]["title"] if headings else document["title"],
        "docs_html": rendered_body,
        "docs_headings": headings,
        "docs_breadcrumbs": docs_breadcrumbs(document),
        "docs_children": docs_children(public_path),
        "docs_context_root": context_root,
        "docs_context_items": docs_context_items(navigation, public_path),
        "docs_curriculum": docs_curriculum(rendered_body)
        if public_path.endswith("/curriculum/")
        else None,
        "docs_parent": docs_parent(document),
        "docs_previous": previous,
        "docs_next": following,
        "docs_local_previous": local_previous,
        "docs_local_next": local_following,
        "primary_navigation_current": "docs",
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
    page = {
        **page,
        "public_path": SLACK_PUBLIC_PATH,
        "title": "DataTalks.Club on Slack",
        "lead": (
            "See where DataTalks.Club members talk, and contact the community team "
            "if you need help with the next step."
        ),
    }
    context = projection_context("slack")
    context["slack"] = page
    return _render(
        request,
        "review/slack.html",
        path=page["public_path"],
        title="DataTalks.Club on Slack",
        description=page["lead"],
        context=context,
    )


# The AI Dev Tools Zoomcamp cohort page reads ``courses.Cohort`` for its facts.  Two
# visible ``Course`` rows currently hold that course (issue #308), so both family slugs
# are named here and the newest visible cohort across them wins; a family-keyed read of
# the older slug alone would keep showing the 2025 edition.
AI_DEV_TOOLS_FAMILY_SLUGS = ("ai-dev-tools-zoomcamp", "ai-dev-tools")

# Page-owned editorial copy.  ``Cohort`` has no format, price or notice field, so these
# have never been database facts; format, price and notice carry the exact strings this
# page showed while it read ``content/review_projection.json``.  ``Cohort.description`` is
# generated boilerplate and ``Course.description`` is raw README markup with external
# image tags and courses.datatalks.club links, so neither is rendered in their place.  The
# title and start values below are only the fallback for a database that holds no such
# cohort: the page is a preserved public path and must still render rather than 404 or
# 500.
#
# The lede is written from the course's own curriculum page,
# /docs/courses/ai-dev-tools-zoomcamp/curriculum/.  It replaces the projection's sentence,
# which claimed the course runs "over four modules" and produces "a specification and a
# groomed backlog"; the curriculum says six modules plus a final project and describes
# neither artefact, so that sentence was false.
AI_DEV_TOOLS_COHORT_COPY: dict[str, str] = {
    "description": (
        "Six modules and a final project on AI-native software engineering: build and "
        "deploy a full app with a coding assistant, build your own coding agent, and "
        "automate everyday work with low-code AI tools."
    ),
    "format": "Online",
    "price": "Free",
    "notice": (
        "The 2026 materials are still being finalized. Some videos, homework, "
        "deadlines, and module details may change before the course starts."
    ),
}
AI_DEV_TOOLS_COURSE_TITLE = "AI Dev Tools Zoomcamp"
AI_DEV_TOOLS_COHORT_TITLE = "AI Dev Tools Zoomcamp 2026"
AI_DEV_TOOLS_COHORT_YEAR = "2026"
AI_DEV_TOOLS_COHORT_START_DISPLAY = "August 31, 2026"
# The public URL segment for this cohort, unchanged by the source-of-truth move.
AI_DEV_TOOLS_COHORT_URL_SLUG = "ai-dev-tools-2026"


def _ai_dev_tools_cohort_page() -> tuple[dict[str, Any], dict[str, Any], Cohort | None]:
    """Compose the cohort page from database facts and page-owned copy."""

    row = latest_visible_cohort_for_families(AI_DEV_TOOLS_FAMILY_SLUGS)
    course = {
        "title": row.course.title if row is not None else AI_DEV_TOOLS_COURSE_TITLE,
    }
    start_date = row.start_date if row is not None else None
    cohort = {
        "slug": AI_DEV_TOOLS_COHORT_URL_SLUG,
        "title": row.title if row is not None else AI_DEV_TOOLS_COHORT_TITLE,
        "year": str(row.year) if row is not None else AI_DEV_TOOLS_COHORT_YEAR,
        "start_display": (
            f"{start_date:%B} {start_date.day}, {start_date:%Y}"
            if start_date is not None
            else AI_DEV_TOOLS_COHORT_START_DISPLAY
        ),
        **AI_DEV_TOOLS_COHORT_COPY,
    }
    return course, cohort, row


@require_safe
def course_cohort(request: HttpRequest) -> HttpResponse:
    course, cohort, legacy_course = _ai_dev_tools_cohort_page()
    return _render(
        request,
        "review/course_cohort.html",
        path=reverse("course-cohort-ai-dev-tools-2026"),
        title=f"{cohort['title']} — DataTalks.Club",
        description=(
            f"The {cohort['year']} cohort of {course['title']}, starting {cohort['start_display']}."
        ),
        context={
            "course": course,
            "cohort": cohort,
            "legacy_course": legacy_course,
        },
    )


@require_GET
def registration_preview(request: HttpRequest) -> HttpResponse:
    course, cohort, _legacy_course = _ai_dev_tools_cohort_page()
    return _render(
        request,
        "review/registration_preview.html",
        path=reverse("course-registration-preview-ai-dev-tools-2026"),
        title=f"Registration — {cohort['title']}",
        description=f"Registration for {cohort['title']} opens soon.",
        context={"course": course, "cohort": cohort},
    )
