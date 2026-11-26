"""The link in the "Confirm to get course and community emails" message."""

from __future__ import annotations

from django.shortcuts import render

from accounts.services.newsletter_consent import NewsletterConfirmError, confirm_from_token


def newsletter_confirm(request):
    try:
        confirm_from_token(request.GET.get("token", ""))
    except NewsletterConfirmError as error:
        return render(
            request,
            "account/newsletter_confirm.html",
            {"confirmed": False, "expired": error.code == "expired"},
            status=400,
        )
    return render(request, "account/newsletter_confirm.html", {"confirmed": True})
