from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers import registry
from django.conf import settings
from django.contrib.auth import authenticate, login
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from accounts.development_owner import DEVELOPMENT_OWNER_PRINCIPAL
from accounts.forms import DevelopmentOwnerLoginForm
from accounts.identity_values import normalize_account_email
from accounts.navigation import safe_next_path
from accounts.studio_authorization import has_explicit_permission
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from core.audit import AuditWriteContext, record_audit_event
from core.models import AuditEvent
from management_auth.models import APIPrincipal, APIRateAdmission
from management_auth.rate_limits import RateLimitExceeded, RateLimitUnavailable, admit


def _login_subject(value: str) -> str:
    return hashlib.sha256(b"dtc:development-owner-login:v1\0" + value.encode("utf-8")).hexdigest()


def _admit_login_attempt(request, email: str) -> None:
    normalized = normalize_account_email(email) or "invalid"
    remote = request.META.get("REMOTE_ADDR", "unknown")
    for kind, value in (("identity", normalized), ("network", str(remote))):
        admit(
            cost_class=APIRateAdmission.CostClass.ADAPTIVE,
            cost=1,
            invalid_prefix=f"login-{kind}-{_login_subject(value)}",
        )


def _audit_login(*, outcome: str, reason: str) -> None:
    record_audit_event(
        action="accounts.development_owner.login",
        target_type="accounts.development_owner",
        target_label="development-owner",
        outcome=outcome,
        context=AuditWriteContext(actor_ref="operator:development-login"),
        changes={},
        metadata={"reason": reason},
    )


def _is_exact_development_owner(user) -> bool:
    return APIPrincipal.objects.filter(
        user=user,
        kind=APIPrincipal.Kind.HUMAN,
        identity_snapshot=DEVELOPMENT_OWNER_PRINCIPAL,
        is_active=True,
    ).exists()


def social_login_view(request):
    safe_next = safe_next_path(request)
    providers = get_available_providers(safe_next)
    local_login_enabled = bool(settings.DEVELOPMENT_OWNER_LOGIN_ENABLED)
    form = DevelopmentOwnerLoginForm(request.POST or None)
    login_error = ""
    throttled = False
    if request.method == "POST":
        if not local_login_enabled:
            _audit_login(outcome=AuditEvent.Outcome.DENIED, reason="environment_denied")
            login_error = "Sign-in was not successful. Check your details and try again."
        elif form.is_valid():
            try:
                _admit_login_attempt(request, form.cleaned_data["email"])
            except RateLimitExceeded:
                throttled = True
                _audit_login(outcome=AuditEvent.Outcome.DENIED, reason="rate_limited")
            except RateLimitUnavailable:
                throttled = True
                _audit_login(outcome=AuditEvent.Outcome.FAILED, reason="rate_unavailable")
            if not throttled:
                user = authenticate(
                    request,
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password"],
                )
                if (
                    user is not None
                    and user.is_active
                    and user.is_staff
                    and _is_exact_development_owner(user)
                    and has_explicit_permission(user, MANAGE_API_CREDENTIALS)
                ):
                    login(request, user)
                    _audit_login(outcome=AuditEvent.Outcome.SUCCEEDED, reason="authenticated")
                    return HttpResponseRedirect(safe_next)
                _audit_login(outcome=AuditEvent.Outcome.DENIED, reason="invalid_credentials")
                login_error = "Sign-in was not successful. Check your details and try again."
        else:
            try:
                _admit_login_attempt(request, request.POST.get("email", ""))
            except (RateLimitExceeded, RateLimitUnavailable):
                throttled = True
            _audit_login(
                outcome=AuditEvent.Outcome.DENIED,
                reason="rate_limited" if throttled else "invalid_credentials",
            )
            login_error = "Sign-in was not successful. Check your details and try again."
    if throttled:
        login_error = "Sign-in is temporarily unavailable. Wait a minute and try again."
    if login_error:
        form = DevelopmentOwnerLoginForm()
    context = {
        "providers": providers,
        "safe_next": safe_next,
        "development_owner_form": form,
        "development_owner_login_enabled": local_login_enabled,
        "login_error": login_error,
    }
    return render(
        request,
        "accounts/login.html",
        context,
        status=429 if throttled else 200,
    )


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
            "name": name,
            "login_url": reverse(f"{provider}_login"),
        }
        providers.append(provider_record)

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
