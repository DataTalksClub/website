from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from loginas.utils import restore_original_login

from course_management.observability import record_event


@login_required
# The stop action only de-elevates back to the original staff user, so a CSRF
# rejection would lock the operator inside the impersonated account instead of
# protecting anything.  Accepting the pre-switch (stale) token after loginas
# rotates the session secret is pinned by
# studio_courses.tests.test_impersonation_stop_views.
@csrf_exempt
@require_POST
def stop_impersonating(request):
    record_event(
        "auth.impersonation_stopped",
        request=request,
        properties={
            "impersonated_user_id": request.user.id,
        },
    )
    restore_original_login(request)
    response = redirect("studio_courses_course_list")
    return response


@login_required
@require_POST
def admin_impersonation_exit(request):
    """POST-and-CSRF replacement for loginas' unguarded /admin/logout/ exit.

    Mounted ahead of the package include so both impersonation exits enforce
    the same method and CSRF contract as the package's entry view.
    """
    restore_original_login(request)
    return redirect("admin:index")
