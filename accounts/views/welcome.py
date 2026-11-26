"""The ``/accounts/welcome/`` learner-profile card, and its review step.

Owns the person-level fields (signed-in-home spec §7.3): not the settings
page, deliberately, because settings couples in remote Datamailer email
preferences that can 503 and mixes five concerns.

The same form serves three entrances, each carrying ``next=`` back to where
the learner was:

* right after a new account signs up (``onboarding=1``, skippable);
* in front of course registration, when the profile is missing a required
  field (``courses.services.learner_profile_gate``);
* "Update" on the review step below.

Saving stamps ``profile_confirmed_at``.  ``profile_review`` is the short "Is
this still up to date?" step registration shows when that stamp is older than
``LEARNER_PROFILE_CONFIRMATION_MAX_AGE_DAYS``.
"""

from __future__ import annotations

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.forms import AboutYouForm, about_you_country_options
from accounts.services.newsletter_consent import (
    STATE_ACTIVE,
    email_signup_already_confirmed,
    newsletter_state,
    record_signup_email_choice,
)
from courses.models.learner_profile import (
    YEARS_EXPERIENCE_CHOICES,
    LearnerProfile,
    learner_profile_for,
    profile_field_default,
)
from courses.services.learner_profile_gate import (
    mark_profile_confirmed,
    missing_required_fields,
    profile_form_url,
    safe_next_url,
)


def _social_prefill_initial(user) -> dict:
    """Prefill-only, blank-fields-only signals from a linked provider (§7.4).

    Because social *signup* stays closed (`ConsolidatingSocialAccountAdapter
    .is_open_for_signup`), this only ever fires for someone who linked a
    provider after creating an email account -- a small population today,
    honestly scoped in the spec rather than worked around.
    """

    initial: dict = {}
    accounts = SocialAccount.objects.filter(user=user)
    profile = learner_profile_for(user)
    github_url = (
        profile.github_url if profile is not None else profile_field_default("github_url")
    )
    certificate_name = (
        profile.certificate_name
        if profile is not None
        else profile_field_default("certificate_name")
    )
    for account in accounts:
        extra_data = account.extra_data if isinstance(account.extra_data, dict) else {}
        if account.provider == "github" and not github_url:
            profile_url = extra_data.get("html_url")
            if isinstance(profile_url, str) and profile_url.strip():
                initial.setdefault("github_url", profile_url.strip())
        if account.provider in {"google", "slack"} and not certificate_name:
            name = extra_data.get("name") or extra_data.get("real_name")
            if isinstance(name, str) and name.strip():
                initial.setdefault("certificate_name", name.strip())
    return initial


@login_required
def welcome(request):
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next"), "")
    onboarding = (request.POST.get("onboarding") or request.GET.get("onboarding")) == "1"
    # The signup step also asks for course and community emails (pre-ticked,
    # confirmed by an emailed link); an account already receiving them is
    # not asked again.
    ask_newsletter = onboarding and newsletter_state(request.user) != STATE_ACTIVE
    if request.method == "POST":
        form = AboutYouForm(
            request.POST,
            instance=_unbound_or_existing_profile(request.user),
            ask_newsletter=ask_newsletter,
        )
        if form.is_valid():
            form.save()
            if ask_newsletter:
                record_signup_email_choice(
                    request.user, subscribe=form.cleaned_data["newsletter"]
                )
            return redirect(next_url or "home")
    else:
        form = AboutYouForm(
            instance=_unbound_or_existing_profile(request.user),
            initial=_social_prefill_initial(request.user),
            ask_newsletter=ask_newsletter,
        )

    profile = learner_profile_for(request.user)
    context = {
        "form": form,
        "country_options": about_you_country_options(),
        "next_url": next_url,
        "onboarding": onboarding,
        # "Skip for now" belongs to the post-signup step and the plain
        # checklist visit: in front of a registration (a ``next=`` without
        # ``onboarding``) the required fields are the point of the page.
        "skip_url": (next_url or "/") if onboarding or not next_url else "",
        "profile_complete": not missing_required_fields(profile),
        # An email + password signup already clicked its verification link,
        # which confirms the emails too; a social signup gets one message.
        "newsletter_needs_email": ask_newsletter
        and not email_signup_already_confirmed(request.user),
    }
    return render(request, "account/welcome.html", context)


def _profile_review_rows(profile) -> list[tuple[str, str]]:
    from courses.views.registration_form import registration_role_label

    years = dict(YEARS_EXPERIENCE_CHOICES).get(profile.years_experience or "", "")
    rows = [
        ("Full name", profile.certificate_name or ""),
        ("Country", profile.country or ""),
        ("Role", registration_role_label(profile.registration_role)),
        ("Company", profile.company_name or ""),
        ("Experience", f"{years} years" if years else ""),
        ("GitHub", profile.github_url or ""),
        ("LinkedIn", profile.linkedin_url or ""),
        ("Website", profile.personal_website_url or ""),
    ]
    return [(label, value) for label, value in rows if value]


@login_required
def profile_review(request):
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next"), "/")
    profile = learner_profile_for(request.user)
    if missing_required_fields(profile):
        # Nothing complete to confirm: the form asks for what is missing.
        return redirect(profile_form_url(next_url))
    if request.method == "POST":
        mark_profile_confirmed(profile)
        profile.save(update_fields=["profile_confirmed_at"])
        return redirect(next_url)
    context = {
        "rows": _profile_review_rows(profile),
        "profile": profile,
        "next_url": next_url,
        "update_url": profile_form_url(next_url),
    }
    return render(request, "account/profile_review.html", context)


def _unbound_or_existing_profile(user):
    """The profile instance for a GET render, without creating a row.

    Rendering must stay write-free for accounts that have no profile row yet
    (bulk-seeded accounts): an unsaved instance renders the same defaults.
    """

    profile = learner_profile_for(user)
    if profile is None:
        return LearnerProfile(user=user)
    return profile
