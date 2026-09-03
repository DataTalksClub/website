"""The slim ``/accounts/welcome/`` "About you" onboarding page.

Owns the person-level fields (signed-in-home spec §7.3): not the settings
page, deliberately, because settings couples in remote Datamailer email
preferences that can 503 and mixes five concerns.  Every field is
save-if-present; nothing is required, and "Skip for now" writes nothing.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.forms import AboutYouForm
from allauth.socialaccount.models import SocialAccount


def _social_prefill_initial(user) -> dict:
    """Prefill-only, blank-fields-only signals from a linked provider (§7.4).

    Because social *signup* stays closed (`ConsolidatingSocialAccountAdapter
    .is_open_for_signup`), this only ever fires for someone who linked a
    provider after creating an email account -- a small population today,
    honestly scoped in the spec rather than worked around.
    """

    initial: dict = {}
    accounts = SocialAccount.objects.filter(user=user)
    for account in accounts:
        extra_data = account.extra_data if isinstance(account.extra_data, dict) else {}
        if account.provider == "github" and not user.github_url:
            profile_url = extra_data.get("html_url")
            if isinstance(profile_url, str) and profile_url.strip():
                initial.setdefault("github_url", profile_url.strip())
        if account.provider in {"google", "slack"} and not user.certificate_name:
            name = extra_data.get("name") or extra_data.get("real_name")
            if isinstance(name, str) and name.strip():
                initial.setdefault("certificate_name", name.strip())
    return initial


@login_required
def welcome(request):
    # "Skip for now" is a plain link straight back to "/" (§7.3): skipping
    # the page and skipping the checklist item are separate acts, so it
    # writes nothing to ``home_dismissals``.
    if request.method == "POST":
        form = AboutYouForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            return redirect("home")
    else:
        form = AboutYouForm(instance=request.user, initial=_social_prefill_initial(request.user))

    return render(request, "account/welcome.html", {"form": form})
