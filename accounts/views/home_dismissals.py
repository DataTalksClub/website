"""Persist one signed-in-home checklist/nudge dismissal (spec §7.2, §9).

Progressive enhancement: each Skip/No-thanks control is a real form POST that
redirects back to a safe local page; JavaScript may later make it inline.
Only the allowlisted keys in ``accounts.home_dismissals`` may be written.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from accounts.home_dismissals import HOME_DISMISSAL_KEYS

# The checklist's "Join Slack" row both records the click and has to take the
# member to Slack (§7.2: the action is `Join Slack` -> `/slack`, and the item
# completes on click).  The destination is therefore an allowlist of two route
# names, never a caller-supplied path: an arbitrary `next` here would be an
# open redirect on an authenticated POST.
REDIRECT_TARGETS = frozenset({"home", "slack"})


@login_required
@require_POST
def dismiss_home_item(request: HttpRequest) -> HttpResponse:
    key = request.POST.get("key", "")
    if key not in HOME_DISMISSAL_KEYS:
        return HttpResponseBadRequest("Unknown dismissal key.")

    user = request.user
    dismissals = dict(user.home_dismissals or {})
    dismissals[key] = True
    user.home_dismissals = dismissals
    user.save(update_fields=["home_dismissals"])

    target = request.POST.get("redirect_to", "home")
    if target not in REDIRECT_TARGETS:
        target = "home"
    return redirect(target)
