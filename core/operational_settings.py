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

* **Secrets.**  ``DATAMAILER_API_KEY``, ``DATAMAILER_WEBHOOK_TOKEN``,
  ``COURSE_REPOSITORY_WEBHOOK_SECRET`` and ``COURSE_HOMEWORK_ANSWER_KEYRING``
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

DATAMAILER_GROUP = "datamailer"
PUBLIC_MEDIA_GROUP = "public_media"
RELAY_LINK_BRIDGE_GROUP = "relay.link_bridge"
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


# -- the transactional mailer ------------------------------------------------
#
# The API key and the webhook token are the two values this group cannot hold,
# so they stay in the environment; everything an operator actually changes --
# which audience a campaign lands in, which address it comes from, whether a
# send is a dry run -- is here.

DATAMAILER_URL = _declare(
    key="datamailer.url",
    group=DATAMAILER_GROUP,
    label="Datamailer URL",
    description="Base https URL of the transactional mailer. Empty means no mailer.",
    value_type=STRING,
    default="",
    env_var="DATAMAILER_URL",
    settings_attr="DATAMAILER_URL",
    validation={"https_url": True, "trim": True},
    validator=_url("https"),
)

DATAMAILER_CLIENT = _declare(
    key="datamailer.client",
    group=DATAMAILER_GROUP,
    label="Datamailer client",
    description="Client identifier this site sends under.",
    value_type=STRING,
    default="",
    env_var="DATAMAILER_CLIENT",
    settings_attr="DATAMAILER_CLIENT",
    validation={"trim": True},
    validator=_trimmed,
)

DATAMAILER_AUDIENCE = _declare(
    key="datamailer.audience",
    group=DATAMAILER_GROUP,
    label="Datamailer audience",
    description="Audience that newsletter subscriptions are written into.",
    value_type=STRING,
    default="",
    env_var="DATAMAILER_AUDIENCE",
    settings_attr="DATAMAILER_AUDIENCE",
    validation={"trim": True},
    validator=_trimmed,
)

DATAMAILER_FROM_EMAIL = _declare(
    key="datamailer.from_email",
    group=DATAMAILER_GROUP,
    label="Sender address",
    description=(
        "Address course and event mail is sent from, or the named sender the "
        "mailer resolves. One sender, never a list."
    ),
    value_type=STRING,
    default="",
    env_var="DATAMAILER_FROM_EMAIL",
    settings_attr="DATAMAILER_FROM_EMAIL",
    validation={"sender": True, "trim": True},
    validator=_sender,
)

DATAMAILER_STRICT = _declare(
    key="datamailer.strict",
    group=DATAMAILER_GROUP,
    label="Fail sends instead of degrading",
    description="Raise on a mailer error rather than recording it and continuing.",
    value_type=BOOLEAN,
    default=False,
    env_var="DATAMAILER_STRICT",
    settings_attr="DATAMAILER_STRICT",
)

DATAMAILER_TIMEOUT_SECONDS = _declare(
    key="datamailer.timeout_seconds",
    group=DATAMAILER_GROUP,
    label="Datamailer request timeout",
    description="Seconds one mailer request may take before it is abandoned.",
    value_type=INTEGER,
    default=60,
    env_var="DATAMAILER_TIMEOUT_SECONDS",
    settings_attr="DATAMAILER_TIMEOUT_SECONDS",
    validation={"minimum": 1, "maximum": 600},
    validator=_bounded_int(minimum=1, maximum=600),
)

DATAMAILER_TRANSACTIONAL_DRY_RUN = _declare(
    key="datamailer.transactional_dry_run",
    group=DATAMAILER_GROUP,
    label="Transactional dry run",
    description="Record transactional sends without handing them to the mailer.",
    value_type=BOOLEAN,
    default=False,
    env_var="DATAMAILER_TRANSACTIONAL_DRY_RUN",
    settings_attr="DATAMAILER_TRANSACTIONAL_DRY_RUN",
)

DATAMAILER_SYNC_ON_USER_CREATE = _declare(
    key="datamailer.sync_on_user_create",
    group=DATAMAILER_GROUP,
    label="Sync a new account to the mailer",
    description="Send a newly registered account to the mailer as it is created.",
    value_type=BOOLEAN,
    default=True,
    env_var="DATAMAILER_SYNC_ON_USER_CREATE",
    settings_attr="DATAMAILER_SYNC_ON_USER_CREATE",
)

DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY = _declare(
    key="datamailer.outbox_dispatch_immediately",
    group=DATAMAILER_GROUP,
    label="Dispatch the outbox in-process",
    description="Deliver queued mail as soon as it is written instead of waiting for a worker.",
    value_type=BOOLEAN,
    default=False,
    env_var="DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY",
    settings_attr="DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY",
)

DATAMAILER_IMPORT_S3_BUCKET = _declare(
    key="datamailer.import_s3_bucket",
    group=DATAMAILER_GROUP,
    label="Recipient import bucket",
    description="Bucket recipient-list imports are staged in.",
    value_type=STRING,
    default="",
    env_var="DATAMAILER_IMPORT_S3_BUCKET",
    settings_attr="DATAMAILER_IMPORT_S3_BUCKET",
    validation={"trim": True},
    validator=_trimmed,
)

DATAMAILER_IMPORT_S3_PREFIX = _declare(
    key="datamailer.import_s3_prefix",
    group=DATAMAILER_GROUP,
    label="Recipient import prefix",
    description="Key prefix recipient-list imports are staged under.",
    value_type=STRING,
    default="datamailer-imports",
    env_var="DATAMAILER_IMPORT_S3_PREFIX",
    settings_attr="DATAMAILER_IMPORT_S3_PREFIX",
    validation={"trim": True},
    validator=_trimmed,
)

DATAMAILER_IMPORT_S3_REGION = _declare(
    key="datamailer.import_s3_region",
    group=DATAMAILER_GROUP,
    label="Recipient import region",
    description="Region of the recipient-list import bucket.",
    value_type=STRING,
    default="",
    env_var="DATAMAILER_IMPORT_S3_REGION",
    settings_attr="DATAMAILER_IMPORT_S3_REGION",
    validation={"trim": True},
    validator=_trimmed,
)

DATAMAILER_IMPORT_URL_EXPIRES_SECONDS = _declare(
    key="datamailer.import_url_expires_seconds",
    group=DATAMAILER_GROUP,
    label="Recipient import link lifetime",
    description="Seconds a staged recipient-list download link stays valid.",
    value_type=INTEGER,
    default=3600,
    env_var="DATAMAILER_IMPORT_URL_EXPIRES_SECONDS",
    settings_attr="DATAMAILER_IMPORT_URL_EXPIRES_SECONDS",
    validation={"minimum": 60, "maximum": 86_400},
    validator=_bounded_int(minimum=60, maximum=86_400),
)


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
    default="public-projection",
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


# -- the relay's link bridge -------------------------------------------------

RELAY_LINK_BRIDGE_BASE_URL = _declare(
    key="relay.link_bridge.base_url",
    group=RELAY_LINK_BRIDGE_GROUP,
    label="Relay link bridge base URL",
    description=(
        "Base URL of the relay that resolves open, click and unsubscribe links. "
        "Relay has no public listener, so this is the private in-VPC address and "
        "http is allowed. Empty means no relay, and the three public routes 404."
    ),
    value_type=STRING,
    default="",
    env_var="RELAY_LINK_BRIDGE_BASE_URL",
    settings_attr="RELAY_LINK_BRIDGE_BASE_URL",
    validation={"url": True, "schemes": ["https", "http"], "trim": True},
    validator=_url("https", "http"),
)

RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS = _declare(
    key="relay.link_bridge.open_timeout_seconds",
    group=RELAY_LINK_BRIDGE_GROUP,
    label="Open beacon timeout",
    description="Seconds an open beacon may take before the pixel is served anyway.",
    value_type=INTEGER,
    default=2,
    env_var="RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS",
    settings_attr="RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS",
    validation={"minimum": 1, "maximum": 30},
    validator=_bounded_int(minimum=1, maximum=30),
)

RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS = _declare(
    key="relay.link_bridge.click_timeout_seconds",
    group=RELAY_LINK_BRIDGE_GROUP,
    label="Click resolution timeout",
    description="Seconds a click resolution may take before the reader is redirected anyway.",
    value_type=INTEGER,
    default=3,
    env_var="RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS",
    settings_attr="RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS",
    validation={"minimum": 1, "maximum": 30},
    validator=_bounded_int(minimum=1, maximum=30),
)

RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS = _declare(
    key="relay.link_bridge.unsubscribe_timeout_seconds",
    group=RELAY_LINK_BRIDGE_GROUP,
    label="Unsubscribe timeout",
    description="Seconds an unsubscribe may take before the reader is answered anyway.",
    value_type=INTEGER,
    default=10,
    env_var="RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS",
    settings_attr="RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS",
    validation={"minimum": 1, "maximum": 60},
    validator=_bounded_int(minimum=1, maximum=60),
)

RELAY_LINK_BRIDGE_POOL_SIZE = _declare(
    key="relay.link_bridge.pool_size",
    group=RELAY_LINK_BRIDGE_GROUP,
    label="Relay connection pool size",
    description="Connections kept open to the relay.",
    value_type=INTEGER,
    default=16,
    env_var="RELAY_LINK_BRIDGE_POOL_SIZE",
    settings_attr="RELAY_LINK_BRIDGE_POOL_SIZE",
    validation={"minimum": 1, "maximum": 256},
    validator=_bounded_int(minimum=1, maximum=256),
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
    DATAMAILER_AUDIENCE.key,
    DATAMAILER_CLIENT.key,
    DATAMAILER_FROM_EMAIL.key,
    DATAMAILER_IMPORT_S3_BUCKET.key,
    DATAMAILER_IMPORT_S3_PREFIX.key,
    DATAMAILER_IMPORT_S3_REGION.key,
    DATAMAILER_IMPORT_URL_EXPIRES_SECONDS.key,
    DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY.key,
    DATAMAILER_STRICT.key,
    DATAMAILER_SYNC_ON_USER_CREATE.key,
    DATAMAILER_TIMEOUT_SECONDS.key,
    DATAMAILER_TRANSACTIONAL_DRY_RUN.key,
    DATAMAILER_URL.key,
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
    RELAY_LINK_BRIDGE_BASE_URL.key,
    RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS.key,
    RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS.key,
    RELAY_LINK_BRIDGE_POOL_SIZE.key,
    RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS.key,
)
