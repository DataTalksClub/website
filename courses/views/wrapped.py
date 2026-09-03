from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render, get_object_or_404

from courses.views.wrapped_context import (
    get_user_wrapped_statistics,
    shareable_user_wrapped_context,
    visible_wrapped_statistics,
    wrapped_page_context,
    wrapped_page_display_name,
)


def wrapped_view(request: HttpRequest, year: int) -> HttpResponse:
    """
    Main view for DataTalks.Club Wrapped - only shows pre-calculated statistics.

    Args:
        year: The year to display wrapped statistics for

    Note: Only displays data if WrappedStatistics exists with is_visible=True
    """
    wrapped_stats = visible_wrapped_statistics(year)
    if wrapped_stats is None:
        context = {
            "year": year,
            "no_data": True,
        }
        response = render(
            request,
            "courses/wrapped.html",
            context,
        )
        return response

    context = wrapped_page_context(request, year, wrapped_stats)
    response = render(
        request,
        "courses/wrapped.html",
        context,
    )
    return response


@login_required
def user_wrapped_view(
    request: HttpRequest, year: int, student_id: int
) -> HttpResponse:
    """One member's Wrapped, readable by that member and by staff only.

    `student_id` is the sequential account primary key, so this route is
    enumerable by construction.  A member's own year in review is member data:
    it is shown to its owner, and to staff who can already see the same figures
    in Studio.  Anyone else gets the same 404 as a member id that does not
    exist, so the route cannot be used to probe which ids are real.

    The page carries share buttons because a member may share their own
    Wrapped.  Making the page itself readable by the recipient would be a new
    product decision with its own design — an opted-in public identity, not an
    account identifier — and is deliberately not assumed here.
    """

    if not _may_view_wrapped(request, student_id):
        raise Http404("No wrapped statistics found.")

    User = get_user_model()
    user = get_object_or_404(User, id=student_id)

    user_wrapped = get_user_wrapped_statistics(year, user)
    if user_wrapped is None:
        context = {
            "year": year,
            "display_name": wrapped_page_display_name(user),
            "no_activity": True,
        }
        response = render(
            request,
            "courses/user_wrapped.html",
            context,
        )
        return response

    context = shareable_user_wrapped_context(year, user, user_wrapped)
    response = render(
        request,
        "courses/user_wrapped.html",
        context,
    )
    return response


def _may_view_wrapped(request: HttpRequest, student_id: int) -> bool:
    viewer = request.user
    if viewer.pk == student_id:
        return True
    return bool(viewer.is_staff or viewer.is_superuser)
