"""A synthetic catalogue that exercises the shared paginator on its own (issue #178).

The primitive has to be provable without a real projection: a books archive that
happens to hold 98 records cannot demonstrate a two-ellipsis window, and a catalogue
that happens to be non-empty cannot demonstrate the empty state.  These fixture routes
mount the same builder, include and CSS on the same reviewed base paths under a test
URLconf, with sequence lengths chosen to produce every state the contract names.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from content.pagination import paginate_public_request, pagination_title


@dataclass(frozen=True, slots=True)
class PaginationFixture:
    base_path: str
    catalogue_label: str
    base_title: str
    item_count: int


FIXTURES = {
    # 240 records is 12 pages: enough for a middle page to show both ellipses.
    "books": PaginationFixture("/books", "Book archive pages", "Books", 240),
    # A second caller with a different label, title and base path, on the same
    # builder and the same include: 55 records is 3 pages, so its window never
    # elides anything and every page is offered.
    "blog": PaginationFixture("/blog", "Article pages", "Articles", 55),
    # Exactly one full page, so the controls must not be rendered at all.
    "events": PaginationFixture("/events/past", "Past event pages", "Past events", 20),
    # Empty, so page one is a valid 200 with no controls and page two is a 404.
    "wiki": PaginationFixture("/wiki", "Wiki catalogue pages", "Podcast Wiki", 0),
}


def _canonical(path: str | None) -> str:
    if path is None:
        return ""
    return f"{settings.CANONICAL_ORIGIN.rstrip('/')}{path}"


@csrf_exempt
def pagination_fixture(request: HttpRequest, *, fixture: str) -> HttpResponse:
    configuration = FIXTURES[fixture]
    items = tuple(
        f"Synthetic catalogue item {number}" for number in range(1, configuration.item_count + 1)
    )
    pagination = paginate_public_request(
        request,
        items,
        clean_base_path=configuration.base_path,
        catalogue_label=configuration.catalogue_label,
    )
    if isinstance(pagination, HttpResponse):
        return pagination

    response = render(
        request,
        "public/catalogue_fixture.html",
        {
            "canonical_url": _canonical(pagination.canonical_path),
            "seo_title": pagination_title(configuration.base_title, pagination.page_number),
            "seo_description": "Synthetic public pagination fixture.",
            "pagination": pagination,
            "previous_url": _canonical(pagination.previous_url),
            "next_url": _canonical(pagination.next_url),
        },
    )
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response
