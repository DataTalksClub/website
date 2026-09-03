from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.services.social_connections import disconnect_social_connection
from course_management.observability import record_event


SIGN_IN_METHODS_FRAGMENT = "#sign-in-methods"


@login_required
@require_POST
def disconnect_social_account(request):
    """Disconnect one provider and return to the settings section it lives in."""

    outcome = disconnect_social_connection(request, request.POST.get("account", ""))
    if outcome.removed:
        record_event(
            "account.social_connection_removed",
            request=request,
            properties={"provider": outcome.provider_id},
        )
    else:
        # allauth adds its own success message; only the refusal needs one, and
        # it carries allauth's wording for why the account cannot be unlinked.
        messages.error(request, outcome.message)

    settings_url = reverse("account_settings")
    response = redirect(f"{settings_url}{SIGN_IN_METHODS_FRAGMENT}")
    return response


@login_required
def social_connections_moved(request):
    """``/accounts/3rdparty/`` is account settings now.

    allauth reverses this path itself after a provider is connected, and the
    inventory in ``accounts/identity_inventory.py`` still names the route, so
    the path keeps working instead of being deleted — it just lands on the one
    surface that owns sign-in methods.
    """

    del request
    settings_url = reverse("account_settings")
    return redirect(f"{settings_url}{SIGN_IN_METHODS_FRAGMENT}")
