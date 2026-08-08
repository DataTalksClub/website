from __future__ import annotations

from dataclasses import dataclass

from django.http import QueryDict

from management_auth.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

from .errors import APIError


@dataclass(frozen=True, slots=True)
class PageQuery:
    page: int
    page_size: int
    sort: tuple[str, ...]
    filters: dict[str, str]


def parse_page_query(
    query: QueryDict,
    *,
    filter_fields: tuple[str, ...],
    sort_fields: tuple[str, ...],
) -> PageQuery:
    allowed = set(filter_fields) | {"page", "page_size", "sort"}
    repeated = {key for key, values in query.lists() if len(values) != 1}
    unknown = set(query) - allowed
    if repeated or unknown:
        raise APIError(400, "invalid_query", "Query parameters are invalid.")
    try:
        page = int(query.get("page", "1"))
        page_size = int(query.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except ValueError as error:
        raise APIError(400, "invalid_query", "Pagination parameters are invalid.") from error
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise APIError(400, "invalid_query", "Pagination parameters are invalid.")
    raw_sort = query.get("sort", "")
    selected_sort = tuple(part for part in raw_sort.split(",") if part) if raw_sort else ()
    if any(part.removeprefix("-") not in sort_fields for part in selected_sort):
        raise APIError(400, "invalid_query", "Sort fields are invalid.")
    filters: dict[str, str] = {}
    for key in filter_fields:
        value = query.get(key)
        if isinstance(value, str):
            filters[key] = value
    return PageQuery(
        page=page,
        page_size=page_size,
        sort=selected_sort,
        filters=filters,
    )
