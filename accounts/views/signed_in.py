"""The landing page of a sign-in that ran in a pop-up window.

The event registration card starts a social sign-in in a small window so the
visitor never leaves the event page.  The provider sends that window back
here; the page tells the event page (same origin, over a BroadcastChannel,
because the cross-origin trip through the provider severs ``window.opener``)
and closes itself.  Opened directly, it is a plain "you are signed in" page.
"""

from __future__ import annotations

from django.shortcuts import render


def login_popup_done(request):
    return render(
        request,
        "accounts/login_popup_done.html",
        {"signed_in": request.user.is_authenticated},
    )
