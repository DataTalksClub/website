"""Course-registration-scoped email verification (pragmatic slice of #243).

The full member email-verification design (issue #243) is blocked on a
``MemberProfile`` model (#248) and the durable ``EmailDelivery``/Relay job
pipeline (#49) — neither exists yet, and this is a pre-launch site with no
real production data, so building toward that formal 7-state design now
would be scope the site does not need yet.

This module is the smallest working slice: it answers "is this account's
registration email verified" using allauth's own ``EmailAddress`` model (the
same evidence ``accounts/auth.py`` already treats as verification truth for
social-login identity resolution) and builds the confirmation link that
``allauth.urls`` already serves at ``account_confirm_email`` — no new
verification model, no new confirm view, no new email pipeline. The link is
folded into the registration confirmation email
(``course_management.package_mail.send_registration_confirmation_mail``)
rather than sent as a second, separate email.
"""

from __future__ import annotations

from allauth.account.models import EmailAddress, EmailConfirmationHMAC

from accounts.identity_values import normalize_account_email


def is_account_email_verified(user, email: str) -> bool:
    """Whether ``email`` is a verified address on ``user``'s account.

    ``user`` may be ``None`` (an anonymous, un-gated registration has no
    account to verify) or unsaved; both answer "not verified" rather than
    raising, since the caller only uses this to decide whether to show a
    reminder.
    """

    if user is None or not getattr(user, "pk", None):
        return False
    normalized = normalize_account_email(email)
    if normalized is None:
        return False
    return EmailAddress.objects.filter(
        user=user,
        email__iexact=normalized,
        verified=True,
    ).exists()


def _email_address_for_verification(user, email: str) -> EmailAddress:
    """The (possibly unverified) ``EmailAddress`` row backing the confirm link.

    Reuses an existing row for the address so a second registration under
    the same account does not spawn duplicate rows; creates one, unverified,
    the first time this account needs a confirm link for this address.
    """

    normalized = normalize_account_email(email) or email.strip().lower()
    existing = EmailAddress.objects.filter(user=user, email__iexact=normalized).first()
    if existing is not None:
        return existing
    has_primary = EmailAddress.objects.filter(user=user, primary=True).exists()
    return EmailAddress.objects.create(
        user=user,
        email=normalized,
        verified=False,
        primary=not has_primary,
    )


def registration_verification_url(user, email: str) -> str:
    """The confirm-email link a registration confirmation offers.

    Building this does not send anything by itself and does not touch
    ``ACCOUNT_EMAIL_VERIFICATION`` (that setting stays ``"none"`` site-wide,
    per the sign-in gate this registration flow already enforces — this is a
    narrower, registration-scoped ask, not a sitewide verification mandate).
    """

    from course_management.datamailer.payloads.urls import public_route_url

    address = _email_address_for_verification(user, email)
    confirmation = EmailConfirmationHMAC(address)
    return public_route_url("account_confirm_email", {"key": confirmation.key})
