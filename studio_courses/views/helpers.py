from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.navigation import can_access_course_studio


STUDIO_COURSES_PAGE_SIZE = 25


def paginate_queryset(request, queryset, per_page=STUDIO_COURSES_PAGE_SIZE):
    paginator = Paginator(queryset, per_page)
    page_number = request.GET.get("page")
    return paginator.get_page(page_number)


def pagination_querystring(request):
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    if encoded:
        querystring = f"&{encoded}"
        return querystring
    return ""


def redirect_after_action(request, default_view_name, **kwargs):
    next_url = request.POST.get("next")
    allowed_hosts = {request.get_host()}
    require_https = request.is_secure()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts=allowed_hosts,
        require_https=require_https,
    ):
        response = redirect(next_url)
        return response
    response = redirect(default_view_name, **kwargs)
    return response


def first_form_error(form):
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return "Invalid form data"


def is_course_studio_operator(user):
    return can_access_course_studio(user)


def staff_required(function):
    """Require one of the two roles authorized for Studio course operations."""
    actual_decorator = user_passes_test(
        is_course_studio_operator,
        login_url="/accounts/login/",
    )
    return actual_decorator(function)


def count_by(queryset, field):
    count_annotation = Count("id")
    rows = (
        queryset.values(field)
        .annotate(count=count_annotation)
        .order_by("-count", field)
    )
    return list(rows)
