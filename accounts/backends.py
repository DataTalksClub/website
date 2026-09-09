from __future__ import annotations

from allauth.account.auth_backends import AuthenticationBackend
from django.conf import settings
from django.db.models import Q

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

    #: One matching account authenticates; two is enough to see the address
    #: is ambiguous and deny. The lookup never materializes more.
    _MAX_EMAIL_CANDIDATES = 2

    def _email_candidates(self, email):
        """Match against the indexed ``normalized_email`` column.

        Every attempt used to normalize and compare every eligible account in
        Python (audit BE-09); the database now does the matching against the
        indexed key with a bounded result set.  Rows without a normalized key
        yet are the only population a Python-side comparison may still scan,
        and only that population -- it shrinks to empty as backfill covers
        them.
        """

        normalized = normalize_account_email(email)
        if normalized is None:
            return ()
        candidates = tuple(
            self._eligible()
            .filter(normalized_email=normalized)
            .order_by("pk")[: self._MAX_EMAIL_CANDIDATES]
        )
        if candidates:
            return candidates
        matches = []
        for user in (
            self._eligible()
            .filter(Q(normalized_email="") | Q(normalized_email__isnull=True))
            .order_by("pk")
            .iterator()
        ):
            if normalize_account_email(user.email) == normalized:
                matches.append(user)
                if len(matches) >= self._MAX_EMAIL_CANDIDATES:
                    break
        return tuple(matches)

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
