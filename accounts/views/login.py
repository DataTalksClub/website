from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from allauth.account.views import LoginView as AllauthLoginView
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers import registry
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch

from accounts.account_entry import log_missing_providers, signup_is_open
from accounts.development_owner import DEVELOPMENT_OWNER_PRINCIPAL
from accounts.identity_values import normalize_account_email
from accounts.navigation import safe_next_path
from accounts.studio_authorization import has_explicit_permission
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from management_auth.models import APIPrincipal, APIRateAdmission
from management_auth.rate_limits import RateLimitExceeded, RateLimitUnavailable, admit


def _login_subject(value: str) -> str:
    return hashlib.sha256(b"dtc:password-login:v1\0" + value.encode("utf-8")).hexdigest()


def _admit_login_attempt(email: str) -> None:
    """Database-backed per-address limit, shared by every worker.

    allauth's own login throttles live in the process-local default cache, so
    on their own they multiply by the worker count and reset on a recycle.
    This keeps a durable ceiling per address (``management_auth`` adaptive
    class: 10 attempts a minute).  It is keyed by address only: behind the
    proxy ``REMOTE_ADDR`` is not the visitor, and a per-IP key there would be
    one global bucket.
    """

    normalized = normalize_account_email(email) or "invalid"
    admit(
        cost_class=APIRateAdmission.CostClass.ADAPTIVE,
        cost=1,
        invalid_prefix=f"login-identity-{_login_subject(normalized)}",
    )


def is_exact_development_owner(user) -> bool:
    if user is None or not getattr(user, "pk", None):
        return False
    return (
        user.is_active
        and user.is_staff
        and APIPrincipal.objects.filter(
            user=user,
            kind=APIPrincipal.Kind.HUMAN,
            identity_snapshot=DEVELOPMENT_OWNER_PRINCIPAL,
            is_active=True,
        ).exists()
        and has_explicit_permission(user, MANAGE_API_CREDENTIALS)
    )


def _optional_signup_url() -> str | None:
    """Resolve the signup destination without failing when it is absent.

    ``account_signup`` is registered by allauth's URLConf, which the full
    site includes but a reduced URLConf (preview/Studio/management-API
    surfaces, or ``core.tests.seo_fixture_urls``) intentionally omits. The
    login page must keep rendering in that reduced surface instead of
    raising ``NoReverseMatch``.
    """

    try:
        return reverse("account_signup")
    except NoReverseMatch:
        return None


def _optional_reset_url() -> str | None:
    try:
        return reverse("account_reset_password")
    except NoReverseMatch:
        return None


THROTTLED_MESSAGE = "Too many sign-in attempts. Wait a minute and try again."


class SiteLoginView(AllauthLoginView):
    """The sign-in page: social providers and allauth's email + password form.

    allauth owns the password flow (its rate limits, the mandatory email
    verification stage, ``next`` handling, signals); this view only adds the
    provider buttons and the site's links, and a durable per-address limit.
    """

    template_name = "accounts/login.html"

    def dispatch(self, request, *args, **kwargs):
        try:
            reverse("account_login")
        except NoReverseMatch:
            # A reduced URLConf (preview, Studio and management-API surfaces,
            # ``core.tests.seo_fixture_urls``) reuses ``accounts.urls``
            # without allauth's routes, so the password flow cannot run
            # there.  The page still renders, with the providers only.
            return render(
                request,
                self.template_name,
                {**self._site_context(), "password_login_available": False},
            )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            _admit_login_attempt(str(request.POST.get("login", "")))
        except (RateLimitExceeded, RateLimitUnavailable):
            form = self.get_form_class()(request=request)
            context = self.get_context_data(form=form, login_error=THROTTLED_MESSAGE)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        errors = form.non_field_errors()
        login_error = (
            str(errors[0])
            if errors
            else "Sign-in was not successful. Check your details and try again."
        )
        return self.render_to_response(self.get_context_data(form=form, login_error=login_error))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._site_context())
        context["password_login_available"] = True
        return context

    def _site_context(self):
        safe_next = safe_next_path(self.request)
        providers = get_available_providers(safe_next)
        if not providers:
            log_missing_providers(self.request, surface="login")
        return {
            "providers": providers,
            "safe_next": safe_next,
            "account_signup_url": (
                _optional_signup_url() if signup_is_open(self.request) else None
            ),
            "password_reset_url": _optional_reset_url(),
        }


social_login_view = SiteLoginView.as_view()


def get_available_providers(safe_next="/"):
    cached_providers = cache.get("available_providers")
    if cached_providers is not None:
        return _providers_with_next(cached_providers, safe_next)

    providers = []
    site_id = settings.SITE_ID
    provider_choices = registry.as_choices()
    for provider, name in provider_choices:
        provider_enabled = SocialApp.objects.filter(
            provider=provider,
            sites__id__exact=site_id,
        ).exists()
        if not provider_enabled:
            continue

        provider_record = {
            "id": provider,
            "name": name,
            "login_url": reverse(f"{provider}_login"),
        }
        providers.append(provider_record)

    # An empty list is never cached: it is the misconfiguration state
    # (`accounts.checks`), and the operator's fix must show on the next request
    # rather than an hour later in every worker.
    if providers:
        cache.set("available_providers", providers, 60 * 60)
    return _providers_with_next(providers, safe_next)


def _providers_with_next(providers, safe_next):
    query = urlencode({"next": safe_next})
    return [
        {
            **provider,
            "login_url": f"{provider['login_url']}?{query}",
        }
        for provider in providers
    ]
