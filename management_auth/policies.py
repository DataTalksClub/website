"""Immutable production policy registry for API credential management."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

CREDENTIAL_CONFIRMATION_POLICY = "management.credentials.explicit-confirmation"
HIGH_RISK_FRESH_CONFIRMATION_POLICY = "management.high-risk.fresh-authenticated-confirmation"


@dataclass(frozen=True, slots=True)
class ExplicitConfirmationPolicy:
    """Require an exact boolean confirmation for a credential mutation."""

    key: str = CREDENTIAL_CONFIRMATION_POLICY

    def authorize(
        self,
        *,
        confirmed: object,
        authenticated_at: object = None,
    ) -> bool:
        del authenticated_at
        return confirmed is True


@dataclass(frozen=True, slots=True)
class FreshAuthenticatedConfirmationPolicy:
    """Require exact confirmation plus recently established authentication."""

    key: str = HIGH_RISK_FRESH_CONFIRMATION_POLICY

    def authorize(
        self,
        *,
        confirmed: object,
        authenticated_at: object = None,
    ) -> bool:
        if confirmed is not True:
            return False
        if not isinstance(authenticated_at, datetime):
            return False
        from django.conf import settings
        from django.utils import timezone

        if timezone.is_naive(authenticated_at):
            return False
        seconds = getattr(settings, "STUDIO_HIGH_RISK_FRESHNESS_SECONDS", 0)
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
            return False
        return 0 <= (timezone.now() - authenticated_at).total_seconds() <= seconds


_POLICIES: Mapping[
    str,
    ExplicitConfirmationPolicy | FreshAuthenticatedConfirmationPolicy,
] = MappingProxyType(
    {
        CREDENTIAL_CONFIRMATION_POLICY: ExplicitConfirmationPolicy(),
        HIGH_RISK_FRESH_CONFIRMATION_POLICY: FreshAuthenticatedConfirmationPolicy(),
    }
)

ConfirmationPolicy = ExplicitConfirmationPolicy | FreshAuthenticatedConfirmationPolicy


def resolved_high_risk_policy_keys() -> frozenset[str]:
    return frozenset(_POLICIES)


def require_high_risk_policy(key: str) -> ConfirmationPolicy:
    try:
        return _POLICIES[key]
    except KeyError as error:
        raise PermissionError("high-risk policy is unavailable") from error
