from __future__ import annotations

from allauth.account.auth_backends import AuthenticationBackend
from django.conf import settings

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser


class DurableAccountBackend(AuthenticationBackend):
    """Email-first authentication with fail-closed legacy compatibility."""

    def _authenticate(self, request, **credentials):
        password = credentials.get("password") or ""
        login = credentials.get("email") or credentials.get("username")
        if not isinstance(login, str) or not login:
            return None
        email_candidates = self._email_candidates(login)
        if email_candidates:
            if len(email_candidates) != 1:
                return None
            return self._checked_candidate(email_candidates[0], password, request=request)
        return self._authenticate_by_username(login, password, request=request)

    def _eligible(self):
        return CustomUser.objects.filter(
            is_active=True,
            identity_state__in=(
                CustomUser.IdentityState.LEGACY,
                CustomUser.IdentityState.ACTIVE,
            ),
        )

    def _email_candidates(self, email):
        normalized = normalize_account_email(email)
        if normalized is None:
            return ()
        candidates = []
        for user in self._eligible().order_by("pk").iterator():
            candidate_email = user.normalized_email or normalize_account_email(user.email)
            if candidate_email == normalized:
                candidates.append(user)
        return tuple(candidates)

    def _authenticate_by_username(self, username, password, *, request):
        candidates = tuple(self._eligible().filter(username__iexact=username).order_by("pk")[:2])
        if len(candidates) != 1:
            return None
        return self._checked_candidate(candidates[0], password, request=request)

    def _checked_candidate(self, user, password, *, request):
        programmatic_test_fixture = bool(
            request is None and settings.TEST_PROGRAMMATIC_STAFF_PASSWORD_AUTHENTICATION
        )
        if user.is_staff and not (
            settings.DEVELOPMENT_OWNER_LOGIN_ENABLED or programmatic_test_fixture
        ):
            user.check_password(password)
            return None
        return self._check_password(user, password)
