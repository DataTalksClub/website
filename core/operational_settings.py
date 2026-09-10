"""Operator-tunable settings, declared once and resolved at runtime.

Everything here used to be readable only from the process environment, so
changing one meant editing a task definition and replacing every running
container.  Each definition below names the environment variable and the
``django.conf.settings`` attribute it replaces, so a deployment keeps booting
exactly as it does today while an operator can move any of these values into
the database through the admin API and have every task pick it up.  See
``core.runtime_config`` for the resolution order and how a write reaches the
other containers.

Every ``default`` below is the value ``website/settings/base.py`` already boots
with.  They agree on purpose: the definition default is the floor a value falls
to when no row, no environment variable and no settings attribute answers, and a
floor that disagreed with the boot value would make "nothing is configured" mean
two different things depending on which layer answered.

A URL and an email address are stored here as themselves.  ``https://datatalks.club``
is the most public string this site owns, and storing it as a bare host so that
a scrubber's pattern would not match it bought nothing: the value reached the
same table either way, in a shape no operator would have typed.  What does earn
its keep is the per-setting validator: an origin is https with no path, an
endpoint is an absolute URL with no userinfo and no query string, and a sender
is exactly one sender.  That refuses the thing the pattern was aimed at -- a
credential smuggled through a URL -- while letting the ordinary value through.
Keeping secrets out of logs remains ``core.redaction``'s job at the logging
boundary, where it is unchanged.

What is deliberately *not* here:

* **Secrets.**    ``COURSE_REPOSITORY_WEBHOOK_SECRET`` and ``COURSE_HOMEWORK_ANSWER_KEYRING``
  stay in the environment.  ``core.configuration`` refuses to register them and
  that refusal is the point: this table is readable by anything that can read
  the database, and its values are written to an audit trail and a revision
  history in the clear.
* **Boot-critical settings.**  ``DJANGO_SECRET_KEY``, ``DATABASE_URL``,
  ``DTC_SQLITE_PATH``, ``DJANGO_ALLOWED_HOSTS``, ``DJANGO_CSRF_TRUSTED_ORIGINS``,
  ``DTC_ENVIRONMENT``, ``DTC_WORKER_ID`` and the AWS region.  A setting that has
  to be read before the database connection exists cannot live behind it.
* **OAuth client credentials.**  Google, GitHub and Slack are allauth
  ``SocialApp`` rows already, read per request, so they are already restart-free.
  ``management_api`` exposes them; they are not duplicated here.
"""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit

from core.configuration import (
    InvalidOperationalSetting,
    OperationalSettingDefinition,
    register_operational_setting,
)
from core.idempotency import JsonValue
from core.models import OperationalSetting

OPERATIONAL_SETTINGS_DOCS_REFERENCE = "_docs/specs/01-platform-architecture.md"

PUBLIC_MEDIA_GROUP = "public_media"
OBSERVABILITY_GROUP = "observability"
SITE_ORIGIN_GROUP = "site.origin"


def _bounded_int(*, minimum: int, maximum: int) -> object:
    def validate(value: JsonValue) -> JsonValue:
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidOperationalSetting("value must be an integer")
        if not minimum <= value <= maximum:
            raise InvalidOperationalSetting(f"value must be between {minimum} and {maximum}")
        return value

    return validate


def _one_of(*allowed: str) -> object:
    def validate(value: JsonValue) -> JsonValue:
        if not isinstance(value, str):
            raise InvalidOperationalSetting("value must be a string")
        normalized = value.strip().casefold()
        if normalized not in allowed:
            raise InvalidOperationalSetting(f"value must be one of {', '.join(allowed)}")
        return normalized

    return validate


def _trimmed(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        raise InvalidOperationalSetting("value must be a string")
    return value.strip()


#: ``host`` or ``host:port``.  A dotted name or ``localhost``; no scheme, no
#: userinfo, no path.
_HOST = re.compile(
    r"^(?:localhost|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+)(?::[0-9]{1,5})?$"
)
#: The local part of an address, and equally the whole of a named sender.
_MAILBOX = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}$")
#: Exactly one ``mailbox@domain``.  One ``@``, no display name, no comma, no
#: angle brackets -- a list of senders is not a sender.
_EMAIL = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}"
    r"@(?:localhost|[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)+)$"
)


def _parsed_url(value: str, *, schemes: tuple[str, ...]) -> SplitResult:
    """One absolute URL that carries nothing but a location.

    Userinfo and a query string are refused, and that refusal is the point: a
    URL is the shape a credential travels in, so ``https://user:pass@host`` and
    ``https://host/callback?token=...`` are exactly what must not become a
    stored setting.  A plain endpoint is not sensitive and is stored as itself.
    """

    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise InvalidOperationalSetting("value must be a URL") from error
    if parsed.scheme not in schemes:
        raise InvalidOperationalSetting(f"value must be a {' or '.join(schemes)} URL")
    if "@" in parsed.netloc:
        raise InvalidOperationalSetting("value must not carry credentials")
    if parsed.query or parsed.fragment:
        raise InvalidOperationalSetting("value must not carry a query string or a fragment")
    if not _HOST.fullmatch(parsed.netloc.casefold()):
        raise InvalidOperationalSetting("value must name one host")
    if ".." in parsed.path:
        raise InvalidOperationalSetting("value path must not traverse")
    return parsed


def _origin(value: JsonValue) -> JsonValue:
    """One bare https origin: scheme and host, and nothing else.

    This is the value every canonical link, sitemap entry and absolute email
    link is built from, so a path here would be appended to every one of them.
    """

    if not isinstance(value, str):
        raise InvalidOperationalSetting("value must be a string")
    normalized = value.strip()
    if not normalized:
        return ""
    parsed = _parsed_url(normalized, schemes=("https",))
    if parsed.path.strip("/"):
        raise InvalidOperationalSetting("origin must be a scheme and host, with no path")
    return f"https://{parsed.netloc.casefold()}"


def _url(*schemes: str) -> object:
    """One absolute URL in the given schemes, or empty for "not configured"."""

    def validate(value: JsonValue) -> JsonValue:
        if not isinstance(value, str):
            raise InvalidOperationalSetting("value must be a string")
        normalized = value.strip()
        if not normalized:
            return ""
        if len(normalized) > 512:
            raise InvalidOperationalSetting("value must be one URL")
        parsed = _parsed_url(normalized, schemes=schemes)
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc.casefold()}{path}"

    return validate


def _sender(value: JsonValue) -> JsonValue:
    """One sender: a whole ``mailbox@domain``, or a named sender, or empty.

    ``noreply@datatalks.club`` is the obvious form and is now stored as itself.
    The bare form is not a leftover of the old split shape: Datamailer resolves
    a *named* sender of its own -- this deployment configures ``courses`` --
    and refusing it here would silently drop the sender every course mail is
    sent from, because an unreadable layer falls through to the next one.

    What is refused is anything that is not *one* sender: whitespace, a comma,
    a display name in angle brackets, a second ``@``.  A header built from a
    list is how an injected recipient gets in.
    """

    if not isinstance(value, str):
        raise InvalidOperationalSetting("value must be a string")
    normalized = value.strip()
    if not normalized:
        return ""
    if ".." in normalized:
        raise InvalidOperationalSetting("value must be one sender")
    if "@" in normalized:
        if not _EMAIL.fullmatch(normalized):
            raise InvalidOperationalSetting("value must be one email address")
    elif not _MAILBOX.fullmatch(normalized):
        raise InvalidOperationalSetting("value must be one address or one named sender")
    return normalized


def _declare(
    *,
    key: str,
    group: str,
    label: str,
    description: str,
    value_type: str,
    default: JsonValue,
    env_var: str,
    settings_attr: str,
    validation: dict[str, JsonValue] | None = None,
    validator: object = None,
) -> OperationalSettingDefinition:
    return register_operational_setting(
        OperationalSettingDefinition(
            key=key,
            group=group,
            label=label,
            description=description,
            value_type=value_type,
            default=default,
            validation=validation or {},
            docs_reference=OPERATIONAL_SETTINGS_DOCS_REFERENCE,
            lifecycle="active",
            cache_policy="stamped",
            sensitivity="operational",
            env_var=env_var,
            settings_attr=settings_attr,
            validator=validator,  # type: ignore[arg-type]
        )
    )


BOOLEAN = OperationalSetting.ValueType.BOOLEAN
INTEGER = OperationalSetting.ValueType.INTEGER
STRING = OperationalSetting.ValueType.STRING


# -- the public media store --------------------------------------------------

PUBLIC_MEDIA_STORE_BACKEND = _declare(
    key="public_media.store_backend",
    group=PUBLIC_MEDIA_GROUP,
    label="Public media backend",
    description="Where public projection media is read from: local or s3.",
    value_type=STRING,
    default="local",
    env_var="PUBLIC_MEDIA_STORE_BACKEND",
    settings_attr="PUBLIC_MEDIA_STORE_BACKEND",
    validation={"choices": ["local", "s3"]},
    validator=_one_of("local", "s3"),
)

PUBLIC_MEDIA_S3_BUCKET = _declare(
    key="public_media.s3_bucket",
    group=PUBLIC_MEDIA_GROUP,
    label="Public media bucket",
    description="Bucket the public projection media is served from.",
    value_type=STRING,
    default="",
    env_var="PUBLIC_MEDIA_S3_BUCKET",
    settings_attr="PUBLIC_MEDIA_S3_BUCKET",
    validation={"trim": True},
    validator=_trimmed,
)

PUBLIC_MEDIA_S3_PREFIX = _declare(
    key="public_media.s3_prefix",
    group=PUBLIC_MEDIA_GROUP,
    label="Public media prefix",
    description="Key prefix the public projection media lives under.",
    value_type=STRING,
    default="",
    env_var="PUBLIC_MEDIA_S3_PREFIX",
    settings_attr="PUBLIC_MEDIA_S3_PREFIX",
    validation={"trim": True},
    validator=_trimmed,
)

PUBLIC_MEDIA_S3_REGION = _declare(
    key="public_media.s3_region",
    group=PUBLIC_MEDIA_GROUP,
    label="Public media region",
    description="Region of the public projection media bucket.",
    value_type=STRING,
    default="",
    env_var="PUBLIC_MEDIA_S3_REGION",
    settings_attr="PUBLIC_MEDIA_S3_REGION",
    validation={"trim": True},
    validator=_trimmed,
)

PUBLIC_MEDIA_S3_ENDPOINT_URL = _declare(
    key="public_media.s3_endpoint_url",
    group=PUBLIC_MEDIA_GROUP,
    label="Public media endpoint URL",
    description=(
        "Alternate S3 endpoint, used when the store is not AWS itself. Empty "
        "means AWS. It exists to point at a local or faked store, which is "
        "reached over http, so http is allowed here."
    ),
    value_type=STRING,
    default="",
    env_var="PUBLIC_MEDIA_S3_ENDPOINT_URL",
    settings_attr="PUBLIC_MEDIA_S3_ENDPOINT_URL",
    validation={"url": True, "schemes": ["https", "http"], "trim": True},
    validator=_url("https", "http"),
)

PUBLIC_MEDIA_S3_TIMEOUT_SECONDS = _declare(
    key="public_media.s3_timeout_seconds",
    group=PUBLIC_MEDIA_GROUP,
    label="Public media request timeout",
    description="Seconds one media object read may take before it is abandoned.",
    value_type=INTEGER,
    default=5,
    env_var="PUBLIC_MEDIA_S3_TIMEOUT_SECONDS",
    settings_attr="PUBLIC_MEDIA_S3_TIMEOUT_SECONDS",
    validation={"minimum": 1, "maximum": 120},
    validator=_bounded_int(minimum=1, maximum=120),
)

PUBLIC_MEDIA_MAX_OBJECT_BYTES = _declare(
    key="public_media.max_object_bytes",
    group=PUBLIC_MEDIA_GROUP,
    label="Largest media object served",
    description="Bytes above which a media object is refused rather than streamed.",
    value_type=INTEGER,
    default=8 * 1024 * 1024,
    env_var="PUBLIC_MEDIA_MAX_OBJECT_BYTES",
    settings_attr="PUBLIC_MEDIA_MAX_OBJECT_BYTES",
    validation={"minimum": 1024, "maximum": 512 * 1024 * 1024},
    validator=_bounded_int(minimum=1024, maximum=512 * 1024 * 1024),
)


# -- observability -----------------------------------------------------------

CLOUDWATCH_APP_METRIC_NAMESPACE = _declare(
    key="observability.cloudwatch_metric_namespace",
    group=OBSERVABILITY_GROUP,
    label="CloudWatch metric namespace",
    description="Namespace application metrics are published under.",
    value_type=STRING,
    default="CourseManagement/App",
    env_var="CLOUDWATCH_APP_METRIC_NAMESPACE",
    settings_attr="CLOUDWATCH_APP_METRIC_NAMESPACE",
    validation={"trim": True},
    validator=_trimmed,
)

CLOUDWATCH_APP_METRIC_REGION = _declare(
    key="observability.cloudwatch_metric_region",
    group=OBSERVABILITY_GROUP,
    label="CloudWatch metric region",
    description="Region application metrics are published to.",
    value_type=STRING,
    default="",
    env_var="CLOUDWATCH_APP_METRIC_REGION",
    settings_attr="CLOUDWATCH_APP_METRIC_REGION",
    validation={"trim": True},
    validator=_trimmed,
)

OBSERVABILITY_EVENT_SCHEMA_VERSION = _declare(
    key="observability.event_schema_version",
    group=OBSERVABILITY_GROUP,
    label="Observability event schema version",
    description="Schema version stamped onto every emitted observability event.",
    value_type=STRING,
    default="1",
    env_var="OBSERVABILITY_EVENT_SCHEMA_VERSION",
    settings_attr="OBSERVABILITY_EVENT_SCHEMA_VERSION",
    validation={"trim": True},
    validator=_trimmed,
)


# -- the canonical origin ----------------------------------------------------
#
# This one is load-bearing for SEO: it is the host every canonical link, sitemap
# entry and absolute email link is built from, and the deployed smoke asserts
# the value it expects.  It is here so a cutover can be finished without a
# release, and the validator refuses anything that is not a bare https origin.

CANONICAL_ORIGIN = _declare(
    key="site.origin.canonical",
    group=SITE_ORIGIN_GROUP,
    label="Canonical origin",
    description=(
        "https origin every canonical link, sitemap entry and absolute email "
        "link is built from. Scheme and host only, with no path."
    ),
    value_type=STRING,
    default="https://datatalks.club",
    env_var="CANONICAL_ORIGIN",
    settings_attr="CANONICAL_ORIGIN",
    validation={"https_origin": True, "trim": True},
    validator=_origin,
)


#: Every key this module declares, in the order an operator reads them.  The
#: admin API and the contract test both iterate this rather than rediscovering
#: the registry, so adding a setting without listing it here fails a test.
OPERATIONAL_SETTING_KEYS: tuple[str, ...] = (
    CANONICAL_ORIGIN.key,
    CLOUDWATCH_APP_METRIC_NAMESPACE.key,
    CLOUDWATCH_APP_METRIC_REGION.key,
    OBSERVABILITY_EVENT_SCHEMA_VERSION.key,
    PUBLIC_MEDIA_MAX_OBJECT_BYTES.key,
    PUBLIC_MEDIA_S3_BUCKET.key,
    PUBLIC_MEDIA_S3_ENDPOINT_URL.key,
    PUBLIC_MEDIA_S3_PREFIX.key,
    PUBLIC_MEDIA_S3_REGION.key,
    PUBLIC_MEDIA_S3_TIMEOUT_SECONDS.key,
    PUBLIC_MEDIA_STORE_BACKEND.key,
)
