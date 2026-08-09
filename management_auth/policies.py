"""Immutable production policy registry for API credential management."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

CREDENTIAL_CONFIRMATION_POLICY = "management.credentials.explicit-confirmation"


@dataclass(frozen=True, slots=True)
class ExplicitConfirmationPolicy:
    """Require an exact boolean confirmation for a credential mutation."""

    key: str = CREDENTIAL_CONFIRMATION_POLICY

    def authorize(self, *, confirmed: object) -> bool:
        return confirmed is True


_POLICIES: Mapping[str, ExplicitConfirmationPolicy] = MappingProxyType(
    {CREDENTIAL_CONFIRMATION_POLICY: ExplicitConfirmationPolicy()}
)


def resolved_high_risk_policy_keys() -> frozenset[str]:
    return frozenset(_POLICIES)


def require_high_risk_policy(key: str) -> ExplicitConfirmationPolicy:
    try:
        return _POLICIES[key]
    except KeyError as error:
        raise PermissionError("high-risk policy is unavailable") from error
