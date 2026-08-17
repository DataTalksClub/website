"""The one page-number paginator the public catalogues share (issue #178).

Books, the Wiki catalogue and the past-events archive are three different collections
with three different orders, but they are the same control: twenty records, a numbered
window with the first and last page always reachable, and one clean first page that is
never spelled ``?page=1``.  Before this module each of them was free to invent that,
and ``/events/past`` already had.

Everything a page needs is computed here and handed to the template as a frozen value:
the parser that accepts exactly one query spelling, the slice, the numeric window, the
adjacent-page relationships and the two URLs the head's ``rel=prev``/``rel=next`` use.
No caller builds a ``?page=`` string, and no template sees a request or a query.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

PUBLIC_PAGE_SIZE = 20
PUBLIC_MAX_PAGE = 999

# The reviewed clean base paths a public page query may be built for.  A path that is
# not on this list cannot become a paginated URL, so a new catalogue is a decision made
# here (and in `core.seo`) rather than a string assembled at a call site.
PUBLIC_PAGINATION_PATHS = frozenset({"/blog", "/books", "/events/past", "/wiki"})

_PAGE_QUERY = re.compile(r"page=([1-9][0-9]{0,2})\Z", re.ASCII)
_MAX_RAW_QUERY_LENGTH = len("page=999")

ControlKind = Literal["page", "ellipsis"]


class InvalidPaginationQuery(ValueError):
    """The raw query is not the sole public page-selector grammar."""


class PaginationPageNotFound(LookupError):
    """The requested syntactically valid page does not exist."""


@dataclass(frozen=True, slots=True)
class PaginationControl:
    """One item in the rendered control row: a page number or an elided range."""

    kind: ControlKind
    page_number: int | None = None
    url: str | None = None
    is_current: bool = False
    accessible_label: str = ""


@dataclass(frozen=True, slots=True)
class PublicPagination[T]:
    """One page of a public catalogue, and every control that page renders."""

    items: tuple[T, ...]
    total_items: int
    page_number: int
    page_count: int
    clean_path: str
    canonical_path: str
    catalogue_label: str
    previous_url: str | None
    previous_page_number: int | None
    next_url: str | None
    next_page_number: int | None
    controls: tuple[PaginationControl, ...]

    @property
    def has_controls(self) -> bool:
        return self.page_count > 1

    @property
    def first_item_number(self) -> int:
        """The 1-based position of this page's first record in the whole catalogue."""

        return 0 if not self.items else (self.page_number - 1) * PUBLIC_PAGE_SIZE + 1

    @property
    def last_item_number(self) -> int:
        """The 1-based position of this page's last record in the whole catalogue."""

        return (self.page_number - 1) * PUBLIC_PAGE_SIZE + len(self.items)


def parse_page_query(raw_query: object) -> int:
    """Parse the exact bounded ASCII public catalogue query grammar.

    The raw query string is read, never `request.GET`: a dictionary view has already
    thrown away the duplicate, the ordering and the extra parameter that make a
    request something other than the one spelling this contract accepts.
    """

    if raw_query == "":
        return 1
    if not isinstance(raw_query, str) or len(raw_query) > _MAX_RAW_QUERY_LENGTH:
        raise InvalidPaginationQuery
    match = _PAGE_QUERY.fullmatch(raw_query)
    if match is None:
        raise InvalidPaginationQuery
    return int(match.group(1))


def _validate_page_number(page_number: int) -> None:
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        raise TypeError("Page number must be an integer.")
    if not 1 <= page_number <= PUBLIC_MAX_PAGE:
        raise ValueError("Page number is outside the supported public range.")


def public_page_url(clean_base_path: str, page_number: int) -> str:
    """Build the only reviewed public catalogue page URL spelling.

    Page one is the clean path, so a visitor who arrives at `/wiki` and a visitor who
    walks back to the first page end up on the same URL, and no internal link ever
    emits `?page=1`.  Nothing else is preserved: this builder takes a path and a
    number, so an unrelated parameter cannot ride along into a canonical.
    """

    if clean_base_path not in PUBLIC_PAGINATION_PATHS:
        raise ValueError("Pagination requires a reviewed clean public catalogue path.")
    _validate_page_number(page_number)
    return clean_base_path if page_number == 1 else f"{clean_base_path}?page={page_number}"


def pagination_title(base_title: str, page_number: int) -> str:
    """Return the shared public title spelling for a catalogue page."""

    if not isinstance(base_title, str) or not base_title.strip():
        raise ValueError("A public catalogue title is required.")
    _validate_page_number(page_number)
    suffix = "" if page_number == 1 else f" — Page {page_number}"
    return f"{base_title}{suffix} — DataTalks.Club"


def _page_numbers(page_number: int, page_count: int) -> tuple[int, ...]:
    """The bounded window: always the first and last page, plus the current one ±2."""

    selected = {1, page_count}
    selected.update(range(max(1, page_number - 2), min(page_count, page_number + 2) + 1))
    return tuple(sorted(selected))


def _controls(
    *, page_number: int, page_count: int, clean_base_path: str
) -> tuple[PaginationControl, ...]:
    controls: list[PaginationControl] = []
    previous_number: int | None = None
    for number in _page_numbers(page_number, page_count):
        if previous_number is not None and number - previous_number > 1:
            controls.append(PaginationControl(kind="ellipsis"))
        current = number == page_number
        controls.append(
            PaginationControl(
                kind="page",
                page_number=number,
                url=None if current else public_page_url(clean_base_path, number),
                is_current=current,
                accessible_label=(
                    f"Page {number}, current page" if current else f"Go to page {number}"
                ),
            )
        )
        previous_number = number
    return tuple(controls)


def build_public_pagination[T](
    items: Sequence[T],
    *,
    page_number: int,
    clean_base_path: str,
    catalogue_label: str,
    page_size: int = PUBLIC_PAGE_SIZE,
) -> PublicPagination[T]:
    """Slice an already ordered sequence without changing it or its domain order.

    The caller owns the order.  This function reads the sequence and copies a window
    out of it; it never sorts, filters, deduplicates or touches the objects inside.
    """

    if page_size != PUBLIC_PAGE_SIZE:
        raise ValueError("Public catalogue pagination uses exactly 20 items per page.")
    if not isinstance(catalogue_label, str) or not catalogue_label.strip():
        raise ValueError("An accessible catalogue label is required.")
    public_page_url(clean_base_path, page_number)

    total_items = len(items)
    page_count = max(1, (total_items + page_size - 1) // page_size)
    if page_count > PUBLIC_MAX_PAGE:
        raise ValueError("The public catalogue exceeds the supported page range.")
    if page_number > page_count:
        raise PaginationPageNotFound

    start = (page_number - 1) * page_size
    page_items = tuple(items[start : start + page_size])
    previous_number = page_number - 1 if page_number > 1 else None
    next_number = page_number + 1 if page_number < page_count else None
    return PublicPagination(
        items=page_items,
        total_items=total_items,
        page_number=page_number,
        page_count=page_count,
        clean_path=clean_base_path,
        canonical_path=public_page_url(clean_base_path, page_number),
        catalogue_label=catalogue_label,
        previous_url=(
            public_page_url(clean_base_path, previous_number)
            if previous_number is not None
            else None
        ),
        previous_page_number=previous_number,
        next_url=(
            public_page_url(clean_base_path, next_number) if next_number is not None else None
        ),
        next_page_number=next_number,
        controls=_controls(
            page_number=page_number,
            page_count=page_count,
            clean_base_path=clean_base_path,
        ),
    )


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def pagination_error(request: HttpRequest, *, status: Literal[400, 404]) -> HttpResponse:
    """Render the shared bounded public error for a rejected page request.

    Neither page repeats the query, the path or anything about the catalogue: a
    malformed selector is answered with the same words whatever it contained.
    """

    template = "public/bad_request.html" if status == 400 else "404.html"
    return _no_store(render(request, template, {"canonical_url": ""}, status=status))


def paginate_public_request[T](
    request: HttpRequest,
    items: Sequence[T],
    *,
    clean_base_path: str,
    catalogue_label: str,
    page_size: int = PUBLIC_PAGE_SIZE,
) -> PublicPagination[T] | HttpResponse:
    """Apply the shared public method/query/error boundary, then build the page.

    The unsafe method is rejected before the sequence is read at all, so a POST to a
    catalogue does no catalogue work.  A caller either receives the frozen page model
    or the finished response it must return unchanged.
    """

    if request.method not in {"GET", "HEAD"}:
        return _no_store(HttpResponseNotAllowed(("GET", "HEAD")))
    try:
        page_number = parse_page_query(request.META.get("QUERY_STRING", ""))
    except InvalidPaginationQuery:
        return pagination_error(request, status=400)
    try:
        return build_public_pagination(
            items,
            page_number=page_number,
            clean_base_path=clean_base_path,
            catalogue_label=catalogue_label,
            page_size=page_size,
        )
    except PaginationPageNotFound:
        return pagination_error(request, status=404)
