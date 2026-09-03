"""Compose the URLs and addresses whose parts are stored scheme-less.

``core.configuration`` refuses to persist a URL or an email address: those are
the two shapes ``core.redaction`` treats as sensitive, and the settings table's
values are written into an audit trail and a revision history in the clear.  So
the five values that *are* URLs or addresses are stored as their safe parts --
``mailer.example.com/api`` rather than ``https://mailer.example.com/api``,
``noreply`` plus ``example.com`` rather than ``noreply@example.com`` -- and are
put back together here, once, at the point of use.

Every composer takes the same shape: if the operator has stored a value, that
value wins and is returned as a URL or an address; if they have not, the process
keeps the value it booted with from ``django.conf.settings``.  An operator can
therefore take over a value at runtime and hand it back by clearing it, without
a deployment either way.
"""

from __future__ import annotations

from django.conf import settings as django_settings

from core.runtime_config import get_str_setting


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _booted(attribute: str) -> str:
    return _text(getattr(django_settings, attribute, "")).rstrip("/")


def https_endpoint(key: str, *, settings_attr: str, using: str = "default") -> str:
    """The stored scheme-less endpoint as an https URL, or the booted URL."""

    stored = _text(get_str_setting(key, using=using))
    if stored:
        return f"https://{stored.rstrip('/')}"
    return _booted(settings_attr)


def canonical_origin(*, using: str = "default") -> str:
    """The origin every canonical link, sitemap entry and email link is built on.

    This is the one an operator is most likely to change under time pressure --
    a DNS cutover finishes here -- so it is worth being explicit: a stored host
    is always served over https, and clearing it returns the site to the origin
    the process booted with.
    """

    host = _text(get_str_setting("site.origin.canonical_host", using=using))
    if host:
        return f"https://{host}"
    return _booted("CANONICAL_ORIGIN")


def datamailer_base_url(*, using: str = "default") -> str:
    return https_endpoint("datamailer.endpoint", settings_attr="DATAMAILER_URL", using=using)


def relay_link_bridge_base_url(*, using: str = "default") -> str:
    """The relay base, which may legitimately be plain http inside the VPC.

    The stored form is always read as https; an operator who needs the in-VPC
    http endpoint keeps it in the environment, where the booted value is used
    unchanged.
    """

    stored = _text(get_str_setting("relay.link_bridge.endpoint", using=using))
    if stored:
        return f"https://{stored.rstrip('/')}"
    return _booted("RELAY_LINK_BRIDGE_BASE_URL")


def public_media_s3_endpoint_url(*, using: str = "default") -> str:
    return https_endpoint(
        "public_media.s3_endpoint",
        settings_attr="PUBLIC_MEDIA_S3_ENDPOINT_URL",
        using=using,
    )


def datamailer_from_email(*, using: str = "default") -> str:
    """The sender address, joined from its two stored halves.

    Half an address is not an address, so a stored mailbox without a stored
    domain (or the reverse) is treated as *not configured* and the booted
    address is used.  Falling back to a half-written sender would send mail from
    an address nobody owns.
    """

    mailbox = _text(get_str_setting("datamailer.from_mailbox", using=using))
    domain = _text(get_str_setting("datamailer.from_domain", using=using))
    if mailbox and domain:
        return f"{mailbox}@{domain}"
    return _text(getattr(django_settings, "DATAMAILER_FROM_EMAIL", ""))
