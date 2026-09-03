from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from accounts.identity_values import normalize_account_email
from accounts.models import AccountIdentityAlias, CustomUser


class AccountEmailResolutionStatus(StrEnum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class AccountEmailResolution:
    normalized_email: str
    status: AccountEmailResolutionStatus
    user: CustomUser | None = None
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
        CustomUser.objects.filter(normalized_email__in=normalized_emails).order_by(
            "normalized_email", "pk"
        )
    )
    user_ids = [user.pk for user in users]
    aliases_by_source_id = {
        alias.source_user_id: alias
        for alias in AccountIdentityAlias.objects.select_related("survivor").filter(
            source_user_id__in=user_ids
        )
    }

    users_by_email: dict[str, list[CustomUser]] = {email: [] for email in normalized_emails}
    for user in users:
        if user.normalized_email in users_by_email:
            users_by_email[user.normalized_email].append(user)

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
    candidates: list[CustomUser],
    aliases_by_source_id: dict[int, AccountIdentityAlias],
) -> AccountEmailResolution:
    matched_user_ids = tuple(user.pk for user in candidates)
    if not candidates:
        return AccountEmailResolution(
            normalized_email=normalized_email,
            status=AccountEmailResolutionStatus.NOT_FOUND,
        )

    available_users: dict[int, CustomUser] = {}
    has_unavailable_candidate = False
    eligible_states = {
        CustomUser.IdentityState.ACTIVE,
        CustomUser.IdentityState.LEGACY,
    }

    for candidate in candidates:
        alias = aliases_by_source_id.get(candidate.pk)
        if not candidate.is_active:
            has_unavailable_candidate = True
            continue

        if candidate.identity_state == CustomUser.IdentityState.ABSORBED:
            if alias is None:
                has_unavailable_candidate = True
                continue
            survivor = alias.survivor
            if not survivor.is_active or survivor.identity_state not in eligible_states:
                has_unavailable_candidate = True
                continue
            available_users[survivor.pk] = survivor
            continue

        if alias is not None or candidate.identity_state not in eligible_states:
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
    if not survivor.is_active or survivor.identity_state == CustomUser.IdentityState.ABSORBED:
        return None
    return survivor.pk


def resolve_durable_user(user: Any) -> CustomUser | None:
    if user is None or getattr(user, "pk", None) is None:
        return None
    if user.identity_state != CustomUser.IdentityState.ABSORBED:
        return user
    survivor_id = resolve_durable_user_id(user.pk)
    if survivor_id is None or survivor_id == user.pk:
        return None
    return CustomUser.objects.filter(pk=survivor_id).first()
