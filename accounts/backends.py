from __future__ import annotations

from allauth.account.auth_backends import AuthenticationBackend

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser


class DurableAccountBackend(AuthenticationBackend):
    """Email-first authentication with fail-closed legacy compatibility."""

    def _authenticate(self, request, **credentials):
        del request
        password = credentials.get("password") or ""
        login = credentials.get("email") or credentials.get("username")
        if not isinstance(login, str) or not login:
            return None
        email_candidates = self._email_candidates(login)
        if email_candidates:
            if len(email_candidates) != 1:
                return None
            return self._check_password(email_candidates[0], password)
        return self._authenticate_by_username(login, password)

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

    def _authenticate_by_username(self, username, password):
        candidates = tuple(
            self._eligible().filter(username__iexact=username).order_by("pk")[:2]
        )
        if len(candidates) != 1:
            return None
        return self._check_password(candidates[0], password)
