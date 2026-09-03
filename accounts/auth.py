from __future__ import annotations

from functools import wraps
from typing import Any

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from accounts.identity_resolution import resolve_durable_user
from accounts.identity_values import normalize_account_email, sha256_text
from accounts.models import AccountIdentityQuarantine, Token
from course_management.observability import record_event

User = get_user_model()
LIVE_CONFLICT_SNAPSHOT_ID = sha256_text("live-account-link-conflict-v1")


def verified_social_emails(email_addresses: object) -> tuple[str, ...]:
    normalized: set[str] = set()
    for email_address in email_addresses or ():
        if not bool(getattr(email_address, "verified", False)):
            continue
        email = normalize_account_email(getattr(email_address, "email", None))
        if email is not None:
            normalized.add(email)
    return tuple(sorted(normalized))


def verified_social_email(email_addresses: object) -> str | None:
    emails = verified_social_emails(email_addresses)
    if len(emails) != 1:
        return None
    return emails[0]


def sociallogin_email(sociallogin: object) -> str | None:
    if sociallogin is None:
        return None
    return verified_social_email(getattr(sociallogin, "email_addresses", ()))


def extract_email(response_data: object, sociallogin: object | None = None) -> str:
    """Return verified adapter evidence only.

    ``response_data`` is deliberately ignored. Provider payloads and
    notification/unverified email claims are not account ownership evidence.
    The argument remains for copied-call compatibility during the migration
    window.
    """

    del response_data
    email = sociallogin_email(sociallogin)
    if email is None:
        raise KeyError("Verified email not found in social login evidence")
    return email


def _provider_key(sociallogin: object) -> str:
    provider = getattr(getattr(sociallogin, "account", None), "provider", "")
    if not isinstance(provider, str) or not provider:
        return "unknown"
    return provider[:32]


def _provider_uid_fingerprint(sociallogin: object) -> str:
    uid = getattr(getattr(sociallogin, "account", None), "uid", "")
    return sha256_text(str(uid))


def _audit_identity_outcome(
    *,
    action: str,
    outcome: str,
    user_id: int | None,
    provider: str,
    reason: str,
) -> None:
    from core.audit import AuditWriteContext, record_audit_event

    actor_ref = f"user:{user_id}" if user_id is not None else ""
    record_audit_event(
        action=action,
        target_type="accounts.identity",
        outcome=outcome,
        context=AuditWriteContext(
            actor_id=user_id,
            actor_ref=actor_ref,
        ),
        changes={},
        metadata={
            "provider": provider,
            "reason": reason,
            "user_id": user_id,
        },
    )


def _conflict_fingerprint(
    *,
    provider: str,
    uid_fingerprint: str,
    reason: str,
    user_ids: tuple[int, ...],
) -> str:
    component = ":".join(
        (
            provider,
            uid_fingerprint,
            reason,
            ",".join(str(user_id) for user_id in user_ids),
        )
    )
    return sha256_text(component)


def _record_link_conflict(
    *,
    sociallogin: object,
    reason: str,
    user_ids: tuple[int, ...],
) -> None:
    provider = _provider_key(sociallogin)
    fingerprint = _conflict_fingerprint(
        provider=provider,
        uid_fingerprint=_provider_uid_fingerprint(sociallogin),
        reason=reason,
        user_ids=user_ids,
    )
    AccountIdentityQuarantine.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            "source_snapshot_id": LIVE_CONFLICT_SNAPSHOT_ID,
            "source_user_ids": list(user_ids),
            "reason_codes": [reason],
        },
    )
    _audit_identity_outcome(
        action="accounts.identity.link_conflict",
        outcome="denied",
        user_id=(user_ids[0] if len(user_ids) == 1 else None),
        provider=provider,
        reason=reason,
    )
    record_event(
        "auth.account_link_conflict",
        properties={
            "provider": provider,
            "reason": reason,
            "candidate_count": len(user_ids),
        },
    )


def _conflict_response(request: object, reason: str) -> HttpResponse:
    context = {"reason": reason}
    if request is None:
        return HttpResponse(
            "We could not safely link this sign-in. Please use an existing "
            "login method or contact support.",
            status=409,
        )
    return render(
        request,
        "socialaccount/identity_conflict.html",
        context,
        status=409,
    )


def _deny_link(
    *,
    request: object,
    sociallogin: object,
    reason: str,
    user_ids: tuple[int, ...] = (),
) -> None:
    _record_link_conflict(
        sociallogin=sociallogin,
        reason=reason,
        user_ids=user_ids,
    )
    raise ImmediateHttpResponse(_conflict_response(request, reason))


def _account_email(user: Any) -> str | None:
    return user.normalized_email or normalize_account_email(user.email)


def _candidate_users_for_verified_email(email: str) -> tuple[Any, ...]:
    """Accounts that claim ``email``, however this site came to hold them.

    Two kinds of evidence count, and they are both *claims*, never
    verification — the provider's assertion, checked once in
    ``verified_social_emails``, is the only thing that verifies anything.

    A verified ``EmailAddress`` row is the first.  It is not sufficient on its
    own for a bulk-imported account: the CMP export carries 20,005 rows for
    20,009 accounts, so four accounts have no row at all and one has an
    unverified row.  Matching only on that row would have locked those members
    out of every enrollment, submission, score and certificate they own.

    The account's own ``email`` column is the second.  CMP obtained it from a
    provider at signup, ``/accounts/email/`` is disabled here so no member can
    add an address to an account, and the account settings form does not
    expose ``email`` — nobody can point an account at an address they do not
    already hold.  Unverified ``EmailAddress`` rows are deliberately *not*
    read: they would be a second, weaker claim surface for no gain, since
    every such row in the export duplicates its account's ``email`` anyway.
    """

    verified_row_ids = set(
        EmailAddress.objects.filter(
            verified=True,
            email__iexact=email,
        ).values_list("user_id", flat=True)
    )
    account_column_ids = set(
        User.objects.filter(
            Q(normalized_email=email) | Q(email__iexact=email),
        )
        .exclude(identity_state=User.IdentityState.ABSORBED)
        .values_list("pk", flat=True)
    )
    users = User.objects.filter(pk__in=verified_row_ids | account_column_ids).order_by("pk")
    # `email__iexact` is a superset of the normalized comparison the rest of
    # this module makes, so the account-column hits are re-checked exactly.
    return tuple(
        user for user in users if user.pk in verified_row_ids or _account_email(user) == email
    )


def _has_unresolved_email_collision(*, email: str, user_id: int) -> bool:
    """Would activating ``email`` on ``user_id`` collide with another account?

    ``normalized_email`` is written by ``CustomUser.save()``, so for every
    account this site created the indexed equality below is the whole answer.
    A bulk import that writes rows without going through the model leaves the
    column empty, so those rows — and only those — are still compared in
    Python.  The scan this replaces read all ~20,000 account rows on every
    first sign-in.
    """

    candidates = User.objects.exclude(pk=user_id).exclude(
        identity_state=User.IdentityState.ABSORBED,
    )
    if candidates.filter(normalized_email=email).exists():
        return True
    unnormalized = candidates.filter(
        Q(normalized_email__isnull=True) | Q(normalized_email=""),
    )
    for candidate in unnormalized.only("email", "normalized_email").iterator():
        if normalize_account_email(candidate.email) == email:
            return True
    return False


def _activate_verified_identity(user: Any, email: str) -> Any:
    with transaction.atomic():
        current = User.objects.get(pk=user.pk)
        if current.identity_state in {
            User.IdentityState.ABSORBED,
            User.IdentityState.QUARANTINED,
        }:
            raise IntegrityError("identity is unavailable")
        if _has_unresolved_email_collision(email=email, user_id=current.pk):
            raise IntegrityError("normalized email is ambiguous")
        updated = User.objects.filter(
            pk=current.pk,
            email=current.email,
            normalized_email=current.normalized_email,
            identity_state=current.identity_state,
            is_active=current.is_active,
        ).update(
            email=current.email or email,
            normalized_email=email,
            identity_state=User.IdentityState.ACTIVE,
        )
        if updated != 1:
            raise IntegrityError("identity changed during activation")
        return User.objects.get(pk=current.pk)


def _provider_uid_conflicts(sociallogin: object, user: Any) -> bool:
    account = getattr(sociallogin, "account", None)
    provider = getattr(account, "provider", "")
    uid = getattr(account, "uid", "")
    if not provider or not uid:
        return True
    same_uid_elsewhere = SocialAccount.objects.filter(
        provider=provider,
        uid=uid,
    ).exclude(user_id=user.pk)
    if same_uid_elsewhere.exists():
        return True
    different_uid_for_provider = SocialAccount.objects.filter(
        provider=provider,
        user_id=user.pk,
    ).exclude(uid=uid)
    return different_uid_for_provider.exists()


class ConsolidatingSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Fail-closed social linking onto the one adopted durable account."""

    def is_open_for_signup(self, request, sociallogin):
        del request, sociallogin
        return False

    def is_email_verified(self, provider, email) -> bool:
        """This site never assumes an address is verified.  The provider says.

        allauth calls this from ``Provider.cleanup_email_addresses`` and, when
        it answers ``True``, overwrites the provider's own ``verified`` flag on
        every address before any of the code below runs — including addresses
        GitHub reports as ``verified: false`` and the public profile address,
        which anyone may set to anyone else's.  It answers ``True`` whenever a
        ``VERIFIED_EMAIL`` entry exists in ``SOCIALACCOUNT_PROVIDERS`` or a
        ``verified_email`` key is stored on the ``SocialApp`` row, so leaving
        the base implementation in place would make the linking rule below
        revertible by configuration.

        Roughly 20,000 imported accounts are matched to their enrollments,
        submissions, scores and certificates by email address, and the
        provider's assertion is the only evidence that an address belongs to
        the person presenting it.  Refusing here closes that door structurally
        rather than by settings hygiene.
        """

        del provider, email
        return False

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            self._validate_existing_connection(request, sociallogin)
            return

        emails = verified_social_emails(sociallogin.email_addresses)
        if not emails:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="verified_email_required",
            )

        candidates_by_id: dict[int, Any] = {}
        matched_email_by_id: dict[int, str] = {}
        for email in emails:
            for candidate in _candidate_users_for_verified_email(email):
                candidates_by_id[candidate.pk] = candidate
                matched_email_by_id.setdefault(candidate.pk, email)
        candidate_ids = tuple(sorted(candidates_by_id))
        if len(candidate_ids) != 1:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason=(
                    "verified_owner_missing" if not candidate_ids else "verified_owner_ambiguous"
                ),
                user_ids=candidate_ids,
            )

        user = candidates_by_id[candidate_ids[0]]
        resolved_user = resolve_durable_user(user)
        if resolved_user is None or not resolved_user.is_active:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="verified_owner_unavailable",
                user_ids=candidate_ids,
            )
        if resolved_user.identity_state == User.IdentityState.QUARANTINED:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="verified_owner_quarantined",
                user_ids=(resolved_user.pk,),
            )

        # The address that selected this account, not whichever sorted first.
        # A GitHub account routinely carries a work and a personal verified
        # address; only one of them is known here, and it is the one that must
        # be written onto the account.  Requiring *every* provider address to
        # be this account's address instead locked out every member with more
        # than one verified address at their provider.
        verified_email = matched_email_by_id[candidate_ids[0]]
        if _account_email(resolved_user) != verified_email:
            # Reached when an alias resolved to a survivor that does not hold
            # the matched address.  Fail closed rather than move a survivor's
            # identity onto an absorbed account's address.
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="verified_owner_claim_mismatch",
                user_ids=(resolved_user.pk,),
            )
        if _provider_uid_conflicts(sociallogin, resolved_user):
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="provider_uid_conflict",
                user_ids=(resolved_user.pk,),
            )

        try:
            resolved_user = _activate_verified_identity(
                resolved_user,
                verified_email,
            )
        except IntegrityError:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="normalized_email_conflict",
                user_ids=(resolved_user.pk,),
            )
        sociallogin.connect(request, resolved_user)
        provider = _provider_key(sociallogin)
        _audit_identity_outcome(
            action="accounts.identity.link_succeeded",
            outcome="succeeded",
            user_id=resolved_user.pk,
            provider=provider,
            reason="verified_owner",
        )
        record_event(
            "auth.returning_login",
            user=resolved_user,
            properties={
                "provider": provider,
                "account_created": False,
            },
        )

    def _validate_existing_connection(self, request, sociallogin):
        user = resolve_durable_user(sociallogin.user)
        if user is None or not user.is_active:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="existing_connection_unavailable",
            )
        if user.identity_state == User.IdentityState.QUARANTINED:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="existing_connection_quarantined",
                user_ids=(user.pk,),
            )
        if _provider_uid_conflicts(sociallogin, user):
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="provider_uid_conflict",
                user_ids=(user.pk,),
            )
        sociallogin.user = user


def token_required(view):
    @wraps(view)
    def decorated(request, *args, **kwargs):
        token_key = request.headers.get("Authorization")
        if token_key:
            token_key = token_key.replace("Token ", "", 1)
            try:
                token = Token.objects.select_related("user").get(key=token_key)
                user = resolve_durable_user(token.user)
                if user is None or not user.is_active:
                    raise Token.DoesNotExist
                request.user = user
            except Token.DoesNotExist:
                record_event(
                    "api.auth_failed",
                    request=request,
                    properties={"reason": "invalid_token"},
                )
                return JsonResponse({"error": "Invalid token"}, status=401)
        else:
            record_event(
                "api.auth_failed",
                request=request,
                properties={"reason": "missing_token"},
            )
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        return view(request, *args, **kwargs)

    decorated.requires_token_auth = True
    return decorated
