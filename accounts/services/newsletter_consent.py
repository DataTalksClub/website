"""Course and community emails: the signup-onboarding double opt-in.

Owner rule (2026-11-26, final): the onboarding step after signup says plainly
that the member will receive emails about their course and the DataTalks.Club
community (the weekly newsletter) and can unsubscribe anytime, with a
pre-ticked box.  Receiving them starts only after the member **confirms by
clicking an emailed link** -- everyone, social signups included:

* an email + password signup has already clicked its account verification
  email before it can sign in and reach onboarding; that one click confirms
  both, so the subscription is active at once;
* a social (Google/GitHub/Slack) signup gets one short "Confirm to get course
  and community emails" message (``newsletter-confirm``); until the link is
  clicked the subscription is pending.

On confirmation the consent is recorded: the notice version, the confirmation
time and the source ``signup onboarding``.  Unticking means no subscription and
no email.  The account settings page shows pending/active and unsubscribes.
The design note is ``_docs/design/specs/signed-in-home.md`` §7.3a.
"""

from __future__ import annotations

from urllib.parse import urlencode

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

SIGNUP_ONBOARDING_SOURCE = "signup onboarding"
CONFIRM_PURPOSE = "newsletter-confirm"
CONFIRM_SALT = "accounts.newsletter-confirm"
CONFIRM_MAX_AGE_SECONDS = 7 * 24 * 3600

STATE_ACTIVE = "active"
STATE_PENDING = "pending"
STATE_OFF = "off"

_FIELDS = (
    "newsletter_subscribed",
    "newsletter_confirmation_sent_at",
    "newsletter_consent_version",
    "newsletter_consent_source",
    "newsletter_consented_at",
)


def newsletter_state(user) -> str:
    if user.newsletter_subscribed:
        return STATE_ACTIVE
    if user.newsletter_confirmation_sent_at is not None:
        return STATE_PENDING
    return STATE_OFF


def email_signup_already_confirmed(user) -> bool:
    """An email + password account whose verification link was clicked."""

    from allauth.account.utils import has_verified_email

    if SocialAccount.objects.filter(user=user).exists():
        return False
    return bool(user.has_usable_password() and has_verified_email(user, user.email))


ACCOUNT_SETTINGS_SOURCE = "account settings"


def _activate(user, now, source=None) -> None:
    user.newsletter_subscribed = True
    user.newsletter_confirmation_sent_at = None
    user.newsletter_consent_version = settings.SIGNUP_NEWSLETTER_CONSENT_VERSION
    user.newsletter_consent_source = (
        source or user.newsletter_consent_source or SIGNUP_ONBOARDING_SOURCE
    )
    user.newsletter_consented_at = now


def unsubscribe(user) -> None:
    user.newsletter_subscribed = False
    user.newsletter_confirmation_sent_at = None
    user.save(update_fields=["newsletter_subscribed", "newsletter_confirmation_sent_at"])


def confirmation_token(user) -> str:
    return signing.dumps({"u": user.pk, "e": (user.email or "").lower()}, salt=CONFIRM_SALT)


def _send_confirmation(user) -> None:
    from course_management.package_mail import mail_idempotency_key, send_package_mail

    site = str(settings.COMMUNITY_BASE["SITE_URL"]).rstrip("/")
    token = confirmation_token(user)
    confirm_url = f"{site}{reverse('newsletter_confirm')}?{urlencode({'token': token})}"
    send_package_mail(
        purpose=CONFIRM_PURPOSE,
        to=user.email,
        context={"confirm_url": confirm_url},
        idempotency_key=mail_idempotency_key(CONFIRM_PURPOSE, str(user.pk), token[-24:]),
        category="account",
        user=user,
    )


def record_signup_email_choice(
    user, *, subscribe: bool, now=None, source: str = SIGNUP_ONBOARDING_SOURCE
) -> str:
    """Apply the onboarding (or settings) box; return the resulting state.

    The pending state remembers its source so the confirmation records where
    the consent was asked.
    """

    now = now or timezone.now()
    if not subscribe:
        user.newsletter_subscribed = False
        user.newsletter_confirmation_sent_at = None
        user.newsletter_consent_version = ""
        user.newsletter_consent_source = ""
        user.newsletter_consented_at = None
    elif email_signup_already_confirmed(user):
        _activate(user, now, source)
    else:
        user.newsletter_subscribed = False
        user.newsletter_confirmation_sent_at = now
        user.newsletter_consent_source = source
        user.newsletter_consent_version = ""
        user.newsletter_consented_at = None
        _send_confirmation(user)
    user.save(update_fields=list(_FIELDS))
    return newsletter_state(user)


class NewsletterConfirmError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def confirm_from_token(token: str, now=None):
    """Activate the subscription a confirm link names; return the user."""

    from accounts.models import User

    try:
        payload = signing.loads(token or "", salt=CONFIRM_SALT, max_age=CONFIRM_MAX_AGE_SECONDS)
    except signing.SignatureExpired as error:
        raise NewsletterConfirmError("expired") from error
    except signing.BadSignature as error:
        raise NewsletterConfirmError("invalid") from error
    user = User.objects.filter(pk=payload.get("u")).first()
    if user is None or (user.email or "").lower() != payload.get("e"):
        raise NewsletterConfirmError("invalid")
    if not user.newsletter_subscribed:
        _activate(user, now or timezone.now())
        user.save(update_fields=list(_FIELDS))
    return user
