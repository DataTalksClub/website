from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import wraps
from typing import Any, NoReturn

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.models import Session
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.identity_resolution import (
    identity_state_eligible,
    resolve_durable_user,
)
from accounts.identity_values import normalize_account_email, sha256_text
from accounts.models import Token
from accounts_ext.models import (
    AccountIdentityQuarantine,
    IdentityState,
    identity_state_of,
    identity_state_row,
    normalized_email_of,
)
from course_management.observability import record_event

logger = logging.getLogger(__name__)

User = get_user_model()
LIVE_CONFLICT_SNAPSHOT_ID = sha256_text("live-account-link-conflict-v1")


def verified_social_emails(email_addresses: object) -> tuple[str, ...]:
    normalized: set[str] = set()
    # Callers hand this either an EmailAddress queryset or None; ``object`` is
    # the honest annotation, so the iterable is narrowed explicitly.
    entries = email_addresses if isinstance(email_addresses, Iterable) else ()
    for email_address in entries:
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


def _conflict_response(request: HttpRequest | None, reason: str) -> HttpResponse:
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
    request: HttpRequest | None,
    sociallogin: object,
    reason: str,
    user_ids: tuple[int, ...] = (),
) -> NoReturn:
    """Record the conflict, then always abort the sign-in flow."""
    _record_link_conflict(
        sociallogin=sociallogin,
        reason=reason,
        user_ids=user_ids,
    )
    raise ImmediateHttpResponse(_conflict_response(request, reason))


def _account_email(user: Any) -> str | None:
    return normalized_email_of(user) or normalize_account_email(user.email)


def _address_claimed_by_any_account(email: str) -> bool:
    """Whether any account holds ``email``, verified or not."""

    user_model = get_user_model()
    return (
        user_model.objects.filter(email__iexact=email).exists()
        or EmailAddress.objects.filter(email__iexact=email).exists()
    )


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

    An account-column match is usable only when that account cannot authenticate
    with a password. This preserves imported accounts without verified address
    rows while refusing an unverified password account that may have been
    created with someone else's email. Unverified ``EmailAddress`` rows are
    deliberately not read: they are not proof of ownership.
    """

    verified_row_ids = set(
        EmailAddress.objects.filter(
            verified=True,
            email__iexact=email,
        ).values_list("user_id", flat=True)
    )
    account_column_ids = set(
        User.objects.filter(
            Q(identity__normalized_email=email) | Q(email__iexact=email),
        )
        .exclude(identity__identity_state=IdentityState.States.ABSORBED)
        .values_list("pk", flat=True)
    )
    users = User.objects.filter(pk__in=verified_row_ids | account_column_ids).order_by("pk")
    # `email__iexact` is a superset of the normalized comparison the rest of
    # this module makes, so account-column hits are re-checked exactly. A
    # password-bearing account needs its own verified address row before the
    # provider can attach to it.
    return tuple(
        user
        for user in users
        if user.pk in verified_row_ids
        or (
            _account_email(user) == email
            and (not user.has_usable_password() or _is_unclaimed_password_signup(user))
        )
    )


def _is_unclaimed_password_signup(user: Any) -> bool:
    """A password signup whose address was never verified and never used.

    Mandatory email verification keeps such an account from signing in, so
    ``last_login`` stays empty.  It may be the address owner who never
    clicked the link, or someone who typed another person's address.  Either
    way the provider's verified address is the stronger proof: the sign-in
    takes the account over and clears the password nobody proved
    (``_claim_unverified_password_signup``).
    """

    return (
        user.has_usable_password()
        and user.last_login is None
        and not EmailAddress.objects.filter(user_id=user.pk, verified=True).exists()
    )


def _claim_unverified_password_signup(user: Any, email: str) -> None:
    if not _is_unclaimed_password_signup(user):
        return
    user.set_unusable_password()
    user.save(update_fields=["password"])
    address = EmailAddress.objects.filter(user_id=user.pk, email__iexact=email).first()
    if address is None:
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    elif not address.verified:
        address.verified = True
        address.save(update_fields=["verified"])


def _flush_other_user_sessions(request: Any, user: Any) -> None:
    """Revoke existing database sessions for the account before first link."""

    session = getattr(request, "session", None)
    if session is None:
        return
    current_session_key = getattr(session, "session_key", None)
    user_id = str(user.pk)
    session_keys = []
    for existing in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if existing.session_key == current_session_key:
            continue
        if existing.get_decoded().get(SESSION_KEY) == user_id:
            session_keys.append(existing.session_key)
    if session_keys:
        Session.objects.filter(session_key__in=session_keys).delete()


def _has_unresolved_email_collision(*, email: str, user_id: int) -> bool:
    """Would activating ``email`` on ``user_id`` collide with another account?

    ``normalized_email`` is written by the accounts_ext save receiver, so for
    every account this site created the indexed equality below is the whole
    answer.  A bulk import that writes rows without going through the model
    leaves the key empty (or the identity row absent entirely), so those rows
    are still compared in Python.  The scan this replaces read all ~20,000
    account rows on every first sign-in.
    """

    candidates = User.objects.exclude(pk=user_id).exclude(
        identity__identity_state=IdentityState.States.ABSORBED,
    )
    if candidates.filter(identity__normalized_email=email).exists():
        return True
    unnormalized = candidates.filter(
        Q(identity__normalized_email__isnull=True)
        | Q(identity__normalized_email="")
        | Q(identity__isnull=True),
    )
    for candidate in unnormalized.only("email").iterator():
        if normalize_account_email(candidate.email) == email:
            return True
    return False


def _activate_verified_identity(user: Any, email: str) -> Any:
    # The identity columns now live on the identity row, so the old
    # single-row compare-and-swap becomes two guarded updates inside one
    # transaction: either both commit, or a failed guard rolls both back --
    # the same "nothing moved unless everything matched" contract.
    with transaction.atomic():
        current = User.objects.get(pk=user.pk)
        current_state = identity_state_row(current)
        if current_state is not None and current_state.identity_state in {
            IdentityState.States.ABSORBED,
            IdentityState.States.QUARANTINED,
        }:
            raise IntegrityError("identity is unavailable")
        if _has_unresolved_email_collision(email=email, user_id=current.pk):
            raise IntegrityError("normalized email is ambiguous")
        if current_state is None:
            # A row-less bulk-created account has no state to compare
            # against; create its identity row directly.
            IdentityState.objects.create(
                user_id=current.pk,
                normalized_email=email,
                identity_state=IdentityState.States.ACTIVE,
            )
            identity_updated = 1
        else:
            identity_updated = IdentityState.objects.filter(
                user_id=current.pk,
                normalized_email=current_state.normalized_email,
                identity_state=current_state.identity_state,
            ).update(
                normalized_email=email,
                identity_state=IdentityState.States.ACTIVE,
            )
        user_updated = User.objects.filter(
            pk=current.pk,
            email=current.email,
            is_active=current.is_active,
        ).update(
            email=current.email or email,
        )
        if identity_updated != 1 or user_updated != 1:
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


#: allauth's email templates -> the site's package mail purposes
#: (``email_templates/<purpose>.md``, published to Relay by
#: ``import_mail_templates``).  allauth renders nothing itself: each send
#: becomes one durable package delivery, like every other site email.
ACCOUNT_MAIL_PURPOSES = {
    "account/email/email_confirmation_signup": "account-verify-email",
    "account/email/email_confirmation": "account-verify-email",
    "account/email/password_reset_key": "account-password-reset",
    "account/email/account_already_exists": "account-already-exists",
    "account/email/password_changed": "account-password-changed",
    "account/email/password_set": "account-password-changed",
    "account/email/password_reset": "account-password-changed",
}
_PASSWORD_ACTIONS = {
    "account/email/password_changed": "changed",
    "account/email/password_set": "set",
    "account/email/password_reset": "reset",
}


class AccountAdapter(DefaultAccountAdapter):
    """Email + password accounts, open to everyone, mailed through Relay.

    Signup is open in every environment next to the social providers.  A
    signup with an address that already has an account never creates a second
    one and never says so on the page (``ACCOUNT_PREVENT_ENUMERATION``): the
    visitor sees the usual "check your email" page and the address's owner is
    mailed a note pointing at sign-in and password reset.  An unverified
    password account cannot sign in (``ACCOUNT_EMAIL_VERIFICATION =
    "mandatory"``), and the first provider sign-in that proves the address
    takes it over and clears that password (see
    ``ConsolidatingSocialAccountAdapter``), so planting an account on someone
    else's address gains nothing.
    """

    error_messages = {
        **DefaultAccountAdapter.error_messages,
        "email_taken": (
            "An account with this email already exists. Sign in with "
            "Google, GitHub, or Slack, or reset your password."
        ),
    }

    def is_open_for_signup(self, request):
        del request
        return True

    def populate_username(self, request, user):
        """Give a new account a unique opaque username, as imported ones have.

        Members sign in by email (``ACCOUNT_USER_MODEL_USERNAME_FIELD =
        None``), so allauth leaves ``username`` blank, and the model's unique
        ``username`` would refuse the second blank one.
        """

        del request
        if getattr(user, "username", None):
            return
        from django.utils.crypto import get_random_string

        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        while True:
            candidate = get_random_string(8, alphabet)
            if not User.objects.filter(username=candidate).exists():
                user.username = candidate
                return

    def send_notification_mail(self, template_prefix, user, context=None, email=None):
        # Imported and one-click accounts often have no ``EmailAddress`` row;
        # the account address is where their notices go.
        if not email and not EmailAddress.objects.filter(user=user, primary=True).exists():
            email = getattr(user, "email", None) or None
        return super().send_notification_mail(template_prefix, user, context, email)

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        import uuid

        from course_management.package_mail import mail_idempotency_key, send_package_mail

        purpose = ACCOUNT_MAIL_PURPOSES.get(template_prefix)
        if purpose is None:
            # A notice this site has no template for (e.g. a provider was
            # connected).  It was never sent before notifications were on.
            logger.info("account_mail_skipped template=%s", template_prefix)
            return
        user = context.get("user")
        if user is None:
            from accounts.forms import _account_for_email

            user = _account_for_email(email)
        send_package_mail(
            purpose=purpose,
            to=email,
            context=self._mail_context(template_prefix, context),
            idempotency_key=mail_idempotency_key("account", purpose, uuid.uuid4().hex),
            category="account",
            user=user,
        )
        record_event(
            "auth.account_mail_queued",
            user=user,
            properties={"purpose": purpose},
        )

    def _mail_context(self, template_prefix: str, context: dict) -> dict:
        from django.urls import reverse

        site = str(settings.COMMUNITY_BASE["SITE_URL"]).rstrip("/")
        if template_prefix.startswith("account/email/email_confirmation"):
            return {"verify_url": context["activate_url"]}
        if template_prefix == "account/email/password_reset_key":
            return {"reset_url": context["password_reset_url"]}
        if template_prefix == "account/email/account_already_exists":
            return {
                "login_url": f"{site}{reverse('login')}",
                "reset_url": f"{site}{reverse('account_reset_password')}",
            }
        return {
            "password_action": _PASSWORD_ACTIONS[template_prefix],
            "reset_url": f"{site}{reverse('account_reset_password')}",
        }


class ConsolidatingSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Social sign-in open; a matching identity resolves before this even runs.

    ``is_open_for_signup`` only governs whether allauth may create a brand
    new ``User`` for a social login it found no existing match for.  A social
    login that DOES match an existing member never reaches that decision:
    ``pre_social_login`` below runs first in allauth's pipeline and, on a
    genuinely provider-verified email match, resolves and connects the
    sociallogin to that member's real account (with the quarantine, address,
    and provider-conflict checks already in ``pre_social_login``) — allauth
    then treats it as an existing-user login, never as a signup.  So this
    flag only ever decides the fate of a person with no matching account at
    all, i.e. a genuinely new member, which is the case this site wants to
    let through.
    """

    def get_app(self, request, provider, client_id=None):
        try:
            return super().get_app(request, provider, client_id=client_id)
        except SocialApp.DoesNotExist as exc:
            # An installed provider is not necessarily enabled for this site.
            # Direct login/callback links must fail as an unavailable route.
            raise Http404("Sign-in provider is unavailable") from exc

    def is_open_for_signup(self, request, sociallogin):
        del request, sociallogin
        return True

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
        if not candidate_ids:
            # An address some account claims without proof (an unverified
            # password account) must not be taken over or duplicated.
            if any(_address_claimed_by_any_account(email) for email in emails):
                _deny_link(
                    request=request,
                    sociallogin=sociallogin,
                    reason="verified_owner_missing",
                )
            # A provider-verified address no account claims at all is a
            # genuinely new person: let allauth sign them up.
            return
        if len(candidate_ids) != 1:
            _deny_link(
                request=request,
                sociallogin=sociallogin,
                reason="verified_owner_ambiguous",
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
        if identity_state_of(resolved_user) == IdentityState.States.QUARANTINED:
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
        with transaction.atomic():
            _claim_unverified_password_signup(resolved_user, verified_email)
            sociallogin.connect(request, resolved_user)
            _flush_other_user_sessions(request, resolved_user)
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
        if identity_state_of(user) == IdentityState.States.QUARANTINED:
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


def _bearer_error_response(error) -> JsonResponse:
    # The compatibility API keeps its flat error envelope; 401 stays a generic
    # invalid-token body with no reflected header or credential material.
    body = {"error": error.message} if error.status == 429 else {"error": "Invalid token"}
    response = JsonResponse(body, status=error.status)
    for name, value in error.headers.items():
        response[name] = value
    return response


def _run_with_management_credential(view, request, *args, **kwargs):
    # BE-02: `Bearer` tokens are scoped, expiring, revocable management
    # credentials (`management_auth.APICredential`).  The import is local
    # because `management_api.authentication` imports `accounts` modules.
    from management_api.authentication import (
        APIError,
        mark_used,
    )
    from management_api.authentication import (
        authenticate as authenticate_management_credential,
    )

    try:
        identity = authenticate_management_credential(
            request,
            reject_alternate_sources=False,
        )
    except APIError as error:
        record_event(
            "api.auth_failed",
            request=request,
            properties={"reason": "invalid_management_credential"},
        )
        return _bearer_error_response(error)
    # Staff authority is decided later from this identity (api.safety), never
    # from the linked user's is_staff flag alone.
    request.management_identity = identity  # type: ignore[attr-defined]
    principal = identity.principal
    if principal.kind == principal.Kind.HUMAN and principal.user is not None:
        request.user = principal.user
    mark_used(identity)
    return view(request, *args, **kwargs)


def token_required(view):
    @wraps(view)
    def decorated(request, *args, **kwargs):
        token_key = request.headers.get("Authorization")
        if token_key is not None and token_key.startswith("Bearer "):
            return _run_with_management_credential(view, request, *args, **kwargs)
        if token_key:
            token_key = token_key.replace("Token ", "", 1)
            try:
                token = Token.objects.select_related("user").get(key=token_key)
                user = resolve_durable_user(token.user)
                # Identity-state eligibility rides along with the is_active
                # check: a quarantined account loses token access too, and the
                # denial is the same generic invalid-token response (BE-08).
                if user is None or not user.is_active or not identity_state_eligible(user):
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

    # The API views introspect this marker; the narrow ignore is the
    # reviewed exemption for setting an attribute on a plain function.
    decorated.requires_token_auth = True  # type: ignore[attr-defined]
    return decorated


def user_display(user) -> str:
    """How allauth names a member in its messages: the address, never the
    opaque generated username."""

    return getattr(user, "email", "") or "your account"
