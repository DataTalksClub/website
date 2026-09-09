"""The account email-category preferences API (D1.2ca).

The three opt-out categories are fields on the user; this view reads and
writes them. Unset counts as allowed, so GET reports every field as an
explicit boolean.
"""

from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from accounts.email_preferences import EMAIL_PREFERENCE_FIELDS
from course_management.observability import record_event


@dataclass(frozen=True)
class EmailPreferenceUpdate:
    field: str
    enabled: bool


@login_required
@require_http_methods(["GET", "POST"])
def account_email_preferences(request):
    if request.method == "GET":
        return _account_email_preferences_get_response(request.user)

    return _account_email_preferences_update_response(request)


def _stored_preferences(user) -> dict[str, bool]:
    return {
        field: getattr(user, field) is not False
        for field in sorted(EMAIL_PREFERENCE_FIELDS)
    }


def _account_email_preferences_get_response(user):
    payload = {"preferences": _stored_preferences(user)}
    return JsonResponse(payload)


def _email_preference_update_payload(request):
    field = request.POST.get("field", "")
    value = request.POST.get("value", "")
    if field not in EMAIL_PREFERENCE_FIELDS:
        response = JsonResponse(
            {"error": "Unsupported email preference."},
            status=400,
        )
        return None, response

    enabled = value.lower() in {"1", "true", "yes", "on"}
    update = EmailPreferenceUpdate(field, enabled)
    return update, None


def _account_email_preferences_update_response(request):
    update, error_response = _email_preference_update_payload(request)
    if error_response:
        return error_response

    setattr(request.user, update.field, update.enabled)
    request.user.save(update_fields=[update.field])
    record_event(
        "account.email_preference_updated",
        request=request,
        properties={
            "field": update.field,
            "enabled": update.enabled,
        },
    )

    payload = {
        "field": update.field,
        "value": update.enabled,
        "stored": True,
    }
    response = JsonResponse(payload)
    return response
