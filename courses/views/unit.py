import re

from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from courses.models import CurriculumFormat, Unit
from courses.registration import render_markdown, youtube_embed_url
from courses.services.unit_assets import (
    rewrite_unit_image_sources,
    unit_code_links,
    unit_repository,
)
from courses.services.unit_links import rewrite_unit_markdown_links
from courses.views.module import module_rail_context
from courses.views.url_utils import get_cohort_or_404


def _unit_url(unit: Unit) -> str:
    cohort = unit.module.cohort
    return reverse(
        "unit",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "module_slug": unit.module.slug,
            "unit_slug": unit.slug,
        },
    )


def unit_edit_on_github_url(unit: Unit) -> str:
    """Return the source edit URL for a unit when its provenance is public."""

    repository = unit_repository(unit)
    if repository is None:
        return ""
    return repository.edit_url()


def _adjacent_units(unit: Unit) -> tuple[Unit | None, Unit | None]:
    siblings = Unit.objects.filter(module=unit.module)
    previous_unit = (
        siblings.filter(Q(position__lt=unit.position) | Q(position=unit.position, id__lt=unit.id))
        .order_by("-position", "-id")
        .first()
    )
    next_unit = (
        siblings.filter(Q(position__gt=unit.position) | Q(position=unit.position, id__gt=unit.id))
        .order_by("position", "id")
        .first()
    )
    return previous_unit, next_unit


# The page already prints the unit title as its `h1`, so a body that opens by
# repeating it says the same thing twice.  Repositories write that opening
# heading at whichever level suits the file they came from: the LLM lessons use
# `#`, the ML lessons use `## 1.1 Introduction to Machine Learning`.  Both are
# the document's own title, so both are matched -- and only ever removed when
# the heading text is the unit title, never when it introduces a real section.
_LEADING_TITLE_HEADING_RE = re.compile(r"\A(?:\s*\n)*#{1,2}[ \t]+([^\n]+?)[ \t]*#*[ \t]*(?:\n|$)")


def _same_title(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    return normalize(left) == normalize(right)


def _unit_content(markdown: str, title: str) -> str:
    """Return the unit's body Markdown without its redundant leading heading."""

    body = markdown or ""
    heading_match = _LEADING_TITLE_HEADING_RE.match(body)
    if heading_match and _same_title(heading_match.group(1), title):
        body = body[heading_match.end() :]

    return body.lstrip("\n")


def unit_video_embed_url(unit: Unit) -> str:
    """Return the safe YouTube embed URL for a unit's declared lesson video."""

    embed_url = youtube_embed_url(unit.video_url)
    if embed_url.startswith("https://www.youtube.com/embed/"):
        return embed_url
    return ""


def unit_view(
    request: HttpRequest,
    course_slug: str,
    cohort_identifier: str,
    module_slug: str,
    unit_slug: str,
) -> HttpResponse:
    cohort = get_cohort_or_404(
        course_slug,
        cohort_identifier,
        curriculum_format=CurriculumFormat.MODULES,
    )
    unit = get_object_or_404(
        Unit.objects.select_related(
            "module",
            "module__cohort",
            "module__cohort__course",
            "module__terminal_homework",
        ),
        module__cohort=cohort,
        module__slug=module_slug,
        slug=unit_slug,
    )
    previous_unit, next_unit = _adjacent_units(unit)
    canonical_path = _unit_url(unit)
    unit_body_markdown = _unit_content(unit.content_markdown, unit.title)
    unit_body_markdown = rewrite_unit_markdown_links(unit_body_markdown, unit)
    unit_body_markdown = rewrite_unit_image_sources(unit_body_markdown, unit)
    # ``render_markdown`` applies the course allowlist, so the rendered body is
    # already sanitized HTML rather than trusted source text.
    rendered_content = mark_safe(render_markdown(unit_body_markdown))
    rail_context = module_rail_context(
        request,
        unit.module,
        current_unit=unit,
    )

    return render(
        request,
        "courses/unit.html",
        {
            "course_family": cohort.course,
            "cohort": cohort,
            "module": unit.module,
            "unit": unit,
            "unit_content_html": rendered_content,
            "previous_unit": previous_unit,
            "next_unit": next_unit,
            "video_embed_url": unit_video_embed_url(unit),
            "unit_code_links": unit_code_links(unit),
            "edit_on_github_url": unit_edit_on_github_url(unit),
            "canonical_url": f"https://datatalks.club{canonical_path}",
            **rail_context,
        },
    )
