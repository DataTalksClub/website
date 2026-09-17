from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from accounts.identity_values import normalize_account_email
from accounts.models import User
from accounts_ext.models import AccountIdentityAlias, IdentityState, identity_state_of


class AccountEmailResolutionStatus(StrEnum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


#: The identity states that may hold or acquire access at all. Quarantine is
#: an immediate containment control (audit BE-08): it does not only block new
#: logins, it ends every existing session, token, and credential path, so the
#: predicate is shared by the login backend, the session middleware, the alias
#: resolver, the legacy token decorator, and the management credential checks.
IDENTITY_ELIGIBLE_STATES = frozenset(
    {
        IdentityState.States.LEGACY,
        IdentityState.States.ACTIVE,
    }
)


def identity_state_eligible(user: Any) -> bool:
    """True when ``user``'s identity state may hold or acquire access."""

    return identity_state_of(user) in IDENTITY_ELIGIBLE_STATES


@dataclass(frozen=True)
class AccountEmailResolution:
    normalized_email: str
    status: AccountEmailResolutionStatus
    user: Any | None = None
    matched_user_ids: tuple[int, ...] = ()

    @property
    def related_user_ids(self) -> tuple[int, ...]:
        user_ids = set(self.matched_user_ids)
        if self.user is not None:
            user_ids.add(self.user.pk)
        return tuple(sorted(user_ids))


def resolve_accounts_by_email(
    values: Iterable[object],
) -> dict[str, AccountEmailResolution]:
    """Resolve normalized emails to one available durable account.

    The result is keyed by the accounts-owned normalized value. Resolution is
    deliberately batch-oriented so callers do not fall into per-item identity
    queries, and deliberately fail-closed when a collision includes an
    unavailable candidate or more than one durable owner.
    """

    normalized_emails = {
        normalized for value in values if (normalized := normalize_account_email(value)) is not None
    }
    if not normalized_emails:
        return {}

    users = list(
        User.objects.select_related("identity")
        .filter(identity__normalized_email__in=normalized_emails)
        .order_by("identity__normalized_email", "pk")
    )
    user_ids = [user.pk for user in users]
    aliases_by_source_id = {
        alias.source_user_id: alias
        for alias in AccountIdentityAlias.objects.select_related("survivor").filter(
            source_user_id__in=user_ids
        )
    }

    users_by_email: dict[str, list[Any]] = {email: [] for email in normalized_emails}
    for user in users:
        if user.identity.normalized_email in users_by_email:
            users_by_email[user.identity.normalized_email].append(user)

    return {
        normalized_email: _resolve_email_candidates(
            normalized_email,
            candidates,
            aliases_by_source_id,
        )
        for normalized_email, candidates in users_by_email.items()
    }


def _resolve_email_candidates(
    normalized_email: str,
    candidates: list[Any],
    aliases_by_source_id: dict[int, AccountIdentityAlias],
) -> AccountEmailResolution:
    matched_user_ids = tuple(user.pk for user in candidates)
    if not candidates:
        return AccountEmailResolution(
            normalized_email=normalized_email,
            status=AccountEmailResolutionStatus.NOT_FOUND,
        )

    available_users: dict[int, Any] = {}
    has_unavailable_candidate = False
    eligible_states = {
        IdentityState.States.ACTIVE,
        IdentityState.States.LEGACY,
    }

    for candidate in candidates:
        alias = aliases_by_source_id.get(candidate.pk)
        if not candidate.is_active:
            has_unavailable_candidate = True
            continue

        candidate_state = candidate.identity.identity_state
        if candidate_state == IdentityState.States.ABSORBED:
            if alias is None:
                has_unavailable_candidate = True
                continue
            survivor = alias.survivor
            if not survivor.is_active or identity_state_of(survivor) not in eligible_states:
                has_unavailable_candidate = True
                continue
            available_users[survivor.pk] = survivor
            continue

        if alias is not None or candidate_state not in eligible_states:
            has_unavailable_candidate = True
            continue
        available_users[candidate.pk] = candidate

    if has_unavailable_candidate:
        status = AccountEmailResolutionStatus.UNAVAILABLE
        user = None
    elif len(available_users) > 1:
        status = AccountEmailResolutionStatus.AMBIGUOUS
        user = None
    elif available_users:
        status = AccountEmailResolutionStatus.AVAILABLE
        user = next(iter(available_users.values()))
    else:
        status = AccountEmailResolutionStatus.UNAVAILABLE
        user = None

    return AccountEmailResolution(
        normalized_email=normalized_email,
        status=status,
        user=user,
        matched_user_ids=matched_user_ids,
    )


def resolve_durable_user_id(user_id: int) -> int | None:
    alias = (
        AccountIdentityAlias.objects.select_related("survivor")
        .filter(source_user_id=user_id)
        .first()
    )
    if alias is None:
        return user_id
    survivor = alias.survivor
    if not survivor.is_active or identity_state_of(survivor) not in IDENTITY_ELIGIBLE_STATES:
        # A quarantined survivor is just as unavailable as a disabled or
        # absorbed one: alias continuity never rescues a quarantined account
        # back into access (audit BE-08).
        return None
    return survivor.pk


def resolve_durable_user(user: Any) -> User | None:
    if user is None or getattr(user, "pk", None) is None:
        return None
    if identity_state_of(user) != IdentityState.States.ABSORBED:
        return user
    survivor_id = resolve_durable_user_id(user.pk)
    if survivor_id is None or survivor_id == user.pk:
        return None
    return User.objects.filter(pk=survivor_id).first()
