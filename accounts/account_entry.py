"""How a visitor gets an account here, decided once for every entrance.

Two separate switches decide whether a new person can get in:

* ``AccountAdapter.is_open_for_signup`` -- plain email/password signup.  Open
  locally and in tests, closed in production by design (``accounts/auth.py``).
* the site's ``SocialApp`` rows -- the Google, GitHub and Slack sign-in
  buttons.  ``ConsolidatingSocialAccountAdapter`` lets a social login with no
  matching member create the account, so in production these buttons *are*
  account creation.

Every "create an account" entrance (the masthead's "Sign up free", the home
and tour CTAs, the course registration gate, the sign-in page, the closed
signup page) asks this module which of the three states it is in, so none of
them can send a visitor to a closed form or promise a path that does not
exist:

* ``signup_open`` -- link to the signup form;
* closed, providers configured -- offer the providers; the first social
  sign-in creates the account.  ``/accounts/signup/`` renders that choice
  (``account/signup_closed.html``), so ``create_url`` still leads there;
* closed, no providers -- a misconfiguration.  Say so honestly and give the
  site's contact address; ``accounts.checks`` (``accounts.W001``) and the
  ``account_entry_no_providers`` log event make it loud for operators.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import Any
from urllib.parse import urlencode

from allauth.account.adapter import get_adapter as get_account_adapter
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch

from accounts.navigation import login_url_for_path, safe_next_path

logger = logging.getLogger(__name__)

NO_PROVIDERS_LOG_EVENT = "account_entry_no_providers"


def signup_url_or_none() -> str | None:
    """The signup route, or ``None`` in a reduced URLConf that omits allauth."""

    try:
        return reverse("account_signup")
    except NoReverseMatch:
        return None


def signup_is_open(request: HttpRequest) -> bool:
    return bool(get_account_adapter(request).is_open_for_signup(request))


def provider_names_phrase(providers: list[dict[str, Any]]) -> str:
    """``"Google, GitHub or Slack"`` from the configured provider records."""

    names = [str(provider["name"]) for provider in providers]
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


def log_missing_providers(request: HttpRequest, *, surface: str) -> None:
    """Structured operator signal: someone reached an entrance with no way in.

    Carries no visitor data: the route name, the path, and the deployment.
    """

    logger.warning(
        NO_PROVIDERS_LOG_EVENT,
        extra={
            "event": NO_PROVIDERS_LOG_EVENT,
            "surface": surface,
            "environment": getattr(settings, "ENVIRONMENT", "local"),
            "site_id": settings.SITE_ID,
            "path": request.path,
        },
    )


def _surface_name(request: HttpRequest) -> str:
    resolver_match = getattr(request, "resolver_match", None)
    return str(getattr(resolver_match, "url_name", None) or "unknown")


@dataclass
class AccountEntry:
    """Lazily resolved entrance state for one request.

    Templates read it through the ``account_entry`` context variable; nothing
    is queried until a template asks, and the provider list comes from the
    same cached reader the sign-in page uses.
    """

    request: HttpRequest

    @cached_property
    def safe_next(self) -> str:
        return safe_next_path(self.request)

    @cached_property
    def signup_url(self) -> str | None:
        return signup_url_or_none()

    @cached_property
    def signup_open(self) -> bool:
        return self.signup_url is not None and signup_is_open(self.request)

    @cached_property
    def create_url(self) -> str:
        """Where every "create an account" CTA points, with ``next``.

        The signup route either is the open form or, closed, the provider
        choice (or the honest no-provider notice); a reduced URLConf without
        it falls back to the sign-in page, which offers the same providers.
        Reading this never queries the providers, so the masthead stays cheap.
        """

        if self.signup_url is None:
            return login_url_for_path(self.safe_next)
        return f"{self.signup_url}?{urlencode({'next': self.safe_next})}"

    @cached_property
    def landing_create_url(self) -> str:
        """``create_url`` without ``next``, for the home and tour CTAs.

        Those pages are served at more than one address (``/`` and
        ``/unified/`` must stay byte-equivalent) and a new member lands on the
        home page after signing up anyway, so their CTA carries no return path.
        """

        if self.signup_url is None:
            return login_url_for_path("/")
        return self.signup_url

    @cached_property
    def providers(self) -> list[dict[str, Any]]:
        from accounts.views.login import get_available_providers

        return list(get_available_providers(self.safe_next))

    @cached_property
    def provider_names(self) -> str:
        return provider_names_phrase(self.providers)

    @cached_property
    def social_signup(self) -> bool:
        """Closed form, configured providers: the providers create accounts."""

        return not self.signup_open and bool(self.providers)

    @cached_property
    def unavailable(self) -> bool:
        """Closed form and no providers: nobody new can get in.

        Only the entrances that render the no-way-in state read this, so the
        operator log fires once per such render, named by its route (the
        registration gate, the closed signup page).  The sign-in view logs
        for itself.
        """

        unavailable = not self.signup_open and not self.providers
        if unavailable:
            log_missing_providers(self.request, surface=_surface_name(self.request))
        return unavailable

    @property
    def contact_email(self) -> str:
        return str(settings.SITE_CONTACT_EMAIL)


def account_entry(request: HttpRequest) -> dict[str, AccountEntry]:
    """Context processor: ``{{ account_entry }}`` on every rendered page."""

    return {"account_entry": AccountEntry(request)}
