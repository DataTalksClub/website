from .helpers import paginate_queryset, pagination_querystring
from .view_models import (
    ENROLLMENT_SORTS,
    enrollment_list_data,
    normalize_enrollment_sort,
)


def enrollment_list_filters(request):
    raw_search_query = request.GET.get("q", "")
    search_query = raw_search_query.strip()
    status_filter = request.GET.get("status", "all")
    sort = request.GET.get("sort", "")
    direction = request.GET.get("dir", "")
    return (
        search_query,
        status_filter,
        sort,
        direction,
    )


def _next_sort_directions(current_sort, current_direction):
    """The direction each column's control applies on its next click (UX-09).

    The active column toggles; any other column starts from its default.
    The template derives both the link target and its accessible name from
    this, so the name announces the action the link performs.
    """
    next_directions = {}
    for key, (_getter, default_direction) in ENROLLMENT_SORTS.items():
        if key == current_sort:
            next_directions[key] = "desc" if current_direction == "asc" else "asc"
        else:
            next_directions[key] = default_direction
    return next_directions


def enrollments_list_context(request, course):
    search_query, status_filter, raw_sort, raw_direction = enrollment_list_filters(
        request
    )
    current_sort, current_direction = normalize_enrollment_sort(
        raw_sort, raw_direction
    )
    enrollments, enrollment_filter_counts = enrollment_list_data(
        course,
        search_query,
        status_filter,
        sort=raw_sort,
        direction=raw_direction,
    )

    enrollments_page = paginate_queryset(request, enrollments)
    total_enrollments = len(enrollments)
    querystring = pagination_querystring(request)
    page_range = enrollments_page.paginator.get_elided_page_range(
        enrollments_page.number
    )

    return {
        "course": course,
        "enrollments": enrollments_page.object_list,
        "enrollments_page": enrollments_page,
        "page_range": page_range,
        "total_enrollments": total_enrollments,
        "enrollment_filter_counts": enrollment_filter_counts,
        "search_query": search_query,
        "status_filter": status_filter,
        "pagination_querystring": querystring,
        "current_sort": current_sort,
        "current_direction": current_direction,
        "sort_next": _next_sort_directions(current_sort, current_direction),
    }
