from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_safe

from courses.models.course import Course

from .review_projection import (
    event_groups,
    projected_events,
    projection_context,
    record_provenance,
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
        path="/blog/ai-dev-tools-zoomcamp",
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
            "provenance": record_provenance(episode),
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
            "provenance": record_provenance(person),
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
    return _render(
        request,
        "review/docs_home.html",
        path="/docs/",
        title="Documentation — DataTalks.Club",
        description="Guides for DataTalks.Club courses and community learning.",
        context=projection_context("docs"),
    )


@require_safe
def docs_getting_started(request: HttpRequest) -> HttpResponse:
    document = review_projection()["docs"]
    return _render(
        request,
        "review/docs_detail.html",
        path=document["public_path"],
        title=f"{document['title']} — AI Dev Tools Zoomcamp Docs",
        description=document["summary"],
        context=projection_context("docs"),
    )


@require_safe
def faq_home(request: HttpRequest) -> HttpResponse:
    return _render(
        request,
        "review/faq_home.html",
        path="/faq/",
        title="Frequently Asked Questions — DataTalks.Club",
        description="Answers to common questions about DataTalks.Club courses.",
        context=projection_context("faq"),
    )


@require_safe
def faq_ai_dev_tools(request: HttpRequest) -> HttpResponse:
    faq = review_projection()["faq"]
    return _render(
        request,
        "review/faq_detail.html",
        path=faq["public_path"],
        title=f"{faq['course']} FAQ — DataTalks.Club",
        description=faq["question"],
        context=projection_context("faq"),
    )


@require_safe
def slack(request: HttpRequest) -> HttpResponse:
    page = review_projection()["slack"]
    return _render(
        request,
        "review/slack.html",
        path=page["public_path"],
        title="Join our Slack — DataTalks.Club",
        description=page["lead"],
        context=projection_context("slack"),
    )


@require_safe
def course_family(request: HttpRequest) -> HttpResponse:
    course = review_projection()["course"]
    legacy_course = Course.objects.filter(slug=course["cohort"]["legacy_platform_slug"]).first()
    context = projection_context("course")
    context.update(
        {
            "legacy_course": legacy_course,
            "platform_provenance": record_provenance(course["platform_provenance"]),
        }
    )
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
            "platform_provenance": record_provenance(course["platform_provenance"]),
            "provenance": record_provenance(course),
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
        title=f"Registration preview — {cohort['title']}",
        description="A read-only preview of the future course registration path.",
        context={"course": course, "cohort": cohort},
    )
