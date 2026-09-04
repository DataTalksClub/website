import os
from pathlib import Path
from typing import Any, NoReturn

from django.core.exceptions import ImproperlyConfigured

from core.bootstrap import (
    RuntimeEnvironment,
    database_configuration,
    parse_bool,
    parse_environment,
    parse_list,
    parse_secret,
)
from core.runtime_identity import read_runtime_identity
from website.loginas_policy import can_login_as

BASE_DIR = Path(__file__).resolve().parents[2]

LOCAL_DEVELOPMENT_SECRET_KEY = "dtc-local-only-insecure-secret-key"
TEST_SECRET_KEY = "dtc-tests-only-secret-key-not-for-deployment"
EXAMPLE_SECRET_KEY = "replace-with-a-long-random-local-value"
UNSAFE_SECRET_KEYS = frozenset(
    {
        LOCAL_DEVELOPMENT_SECRET_KEY,
        TEST_SECRET_KEY,
        EXAMPLE_SECRET_KEY,
    }
)


def env_list(name: str, default: str = "") -> list[str]:
    return list(parse_list(name, os.getenv(name, default)))


def env_flag(name: str, default: bool = False) -> bool:
    return parse_bool(name, os.getenv(name), default=default)


def missing(name: str) -> NoReturn:
    raise ImproperlyConfigured(f"Required bootstrap setting {name} is missing")


def secure_secret_from_environment(name: str = "DJANGO_SECRET_KEY") -> str:
    return parse_secret(name, os.getenv(name), forbidden_values=UNSAFE_SECRET_KEYS)


def database_from_environment(
    *,
    environment: RuntimeEnvironment,
) -> dict[str, Any]:
    return database_configuration(
        environment=environment,
        database_url=os.getenv("DATABASE_URL"),
    )


def sqlite_database_from_environment(
    *,
    environment: RuntimeEnvironment,
    default_path: Path,
) -> dict[str, Any]:
    """Build the local/test SQLite setting without consulting ``DATABASE_URL``."""

    if environment not in {RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST}:
        raise ImproperlyConfigured("SQLite settings are available only for local and test")
    configured_path = os.getenv("DTC_SQLITE_PATH")
    path = Path(configured_path) if configured_path else default_path
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve(strict=False)
    if (
        configured_path
        and not Path(configured_path).is_absolute()
        and not path.is_relative_to(BASE_DIR)
    ):
        raise ImproperlyConfigured("Relative DTC_SQLITE_PATH must stay inside the repository")
    return database_configuration(
        environment=environment,
        database_url=None,
        sqlite_fallback=path,
    )


SITE_NAME = "DataTalks.Club"
RUNTIME_ENVIRONMENT = parse_environment(os.getenv("DTC_ENVIRONMENT"))
ENVIRONMENT = RUNTIME_ENVIRONMENT.value
RUNTIME_IDENTITY = read_runtime_identity()
VERSION = RUNTIME_IDENTITY.version
SOURCE_SHA = RUNTIME_IDENTITY.source_sha
IMAGE_DIGEST = RUNTIME_IDENTITY.image_digest
# Python compatibility alias only. Deployed task definitions must not set APP_VERSION.
APP_VERSION = VERSION
DEVELOPMENT_OWNER_LOGIN_ENABLED = RUNTIME_ENVIRONMENT is RuntimeEnvironment.DEVELOPMENT
TEST_PROGRAMMATIC_STAFF_PASSWORD_AUTHENTICATION = False
STUDIO_SESSION_IDLE_SECONDS = int(os.getenv("STUDIO_SESSION_IDLE_SECONDS", "900"))
STUDIO_SESSION_ABSOLUTE_SECONDS = int(os.getenv("STUDIO_SESSION_ABSOLUTE_SECONDS", "28800"))
STUDIO_HIGH_RISK_FRESHNESS_SECONDS = int(os.getenv("STUDIO_HIGH_RISK_FRESHNESS_SECONDS", "900"))
for _studio_timeout_name, _studio_timeout_value in (
    ("STUDIO_SESSION_IDLE_SECONDS", STUDIO_SESSION_IDLE_SECONDS),
    ("STUDIO_SESSION_ABSOLUTE_SECONDS", STUDIO_SESSION_ABSOLUTE_SECONDS),
    ("STUDIO_HIGH_RISK_FRESHNESS_SECONDS", STUDIO_HIGH_RISK_FRESHNESS_SECONDS),
):
    if _studio_timeout_value < 1:
        raise ImproperlyConfigured(f"{_studio_timeout_name} must be at least one second")
CANONICAL_ORIGIN = os.getenv("CANONICAL_ORIGIN", "https://datatalks.club").rstrip("/")
# Source-managed homework answer keys are injected by the runtime secret boundary.  An empty
# value deliberately fails closed when encrypted curriculum is used without configuration.
COURSE_HOMEWORK_ANSWER_KEYRING = os.getenv("COURSE_HOMEWORK_ANSWER_KEYRING", "")
# GitHub sends this secret in the webhook signature.  It is injected at runtime and is never
# persisted in ContentSource or a durable job payload.
COURSE_REPOSITORY_WEBHOOK_SECRET = os.getenv("COURSE_REPOSITORY_WEBHOOK_SECRET", "")

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "loginas",
    "django_q",
    "management_auth.apps.ManagementAuthConfig",
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "content.apps.ContentConfig",
    "content_sync.apps.ContentSyncConfig",
    "courses.apps.CoursesConfig",
    "events.apps.EventsConfig",
    "email_app.apps.EmailAppConfig",
    "studio.apps.StudioConfig",
    "management_api.apps.ManagementAPIConfig",
    "api.apps.ApiConfig",
    "jobs.apps.JobsConfig",
    "data.apps.DataConfig",
    "studio_courses.apps.StudioCoursesConfig",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    "allauth.socialaccount.providers.slack",
]

MIDDLEWARE = [
    # This policy must wrap short-circuits from health, SecurityMiddleware,
    # WhiteNoise, URL resolution, and error handlers.
    "core.middleware.ResponsePolicyMiddleware",
    # Bound request bodies before parsers, authentication, or domain services.
    "core.middleware.RequestBoundaryMiddleware",
    "management_api.middleware.AdminAPIResponseMiddleware",
    "course_management.middleware.ObservabilityExceptionMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.ReadinessProbeCommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.DurableAccountSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "website.urls"
APPEND_SLASH = False
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "course_platform_templates", BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.navigation.account_navigation",
                "course_management.context_processors.export_settings",
                "core.context_processors.site_context",
            ]
        },
    }
]
WSGI_APPLICATION = "website.wsgi.application"
ASGI_APPLICATION = "website.asgi.application"

AUTH_USER_MODEL = "accounts.CustomUser"
AUTHENTICATION_BACKENDS = ["accounts.backends.DurableAccountBackend"]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "home"
ACCOUNT_CANONICAL_ORIGIN = CANONICAL_ORIGIN

SITE_ID = 2
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"
# `ACCOUNT_ALLOW_REGISTRATION` is not a real allauth setting name — allauth
# gates signup entirely through the adapter's `is_open_for_signup()`. The
# actual gate for the plain email/password path is `ACCOUNT_ADAPTER` below;
# see `ClosedAccountAdapter` in `accounts/auth.py`.

# Course registration is account-owned (`_docs/specs/open-decisions.md` §6,
# `_docs/specs/04-courses-and-cohorts.md`), and the signed-in-home spec §8.3
# turns that decision into a sign-in gate on the campaign form.  The flag is the
# revert lever the spec asks for: the gate is a conversion-path bet, and the
# owner can turn it off on evidence rather than on argument, without a code
# change.  Default on.
REGISTRATION_REQUIRES_ACCOUNT = env_flag("REGISTRATION_REQUIRES_ACCOUNT", default=True)
ACCOUNT_ADAPTER = "accounts.auth.ClosedAccountAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.auth.ConsolidatingSocialAccountAdapter"
SOCIALACCOUNT_LOGIN_ON_GET = True
# `user:email` is what makes GitHub return the address list with its own
# per-address `verified` flag, which is the only ownership evidence
# `ConsolidatingSocialAccountAdapter` will act on.
#
# `VERIFIED_EMAIL` is deliberately absent.  Setting it makes allauth overwrite
# every address a provider hands back with `verified=True` inside
# `Provider.cleanup_email_addresses`, before any adapter runs — including
# addresses GitHub reports as `verified: false` and the public profile address,
# which anyone may set to anyone else's.  The migration matches roughly 20,000
# imported accounts to their history by email, so that flag would turn "the
# provider vouched for this address" into "the provider mentioned this address"
# and hand one member another member's course record.  Pinned by
# `accounts/tests/test_imported_account_social_matching.py`.
SOCIALACCOUNT_PROVIDERS = {
    "github": {"SCOPE": ["user:email"]},
}
CAN_LOGIN_AS = can_login_as

UNFOLD = {
    "SITE_HEADER": "DataTalks.Club Django admin",
    "SITE_TITLE": "Django administration",
    "SITE_SYMBOL": "school",
}
SHOW_WRAPPED = False

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "DataTalks.Club <noreply@datatalks.club>"

PUBLIC_BASE_URL = CANONICAL_ORIGIN
OBSERVABILITY_ENVIRONMENT = ENVIRONMENT
OBSERVABILITY_EVENT_SCHEMA_VERSION = os.getenv("OBSERVABILITY_EVENT_SCHEMA_VERSION", "1")
OBSERVABILITY_EVENT_BACKENDS = ["log"]
CLOUDWATCH_APP_METRIC_NAMESPACE = os.getenv(
    "CLOUDWATCH_APP_METRIC_NAMESPACE", "CourseManagement/App"
)
CLOUDWATCH_APP_METRIC_REGION = os.getenv(
    "CLOUDWATCH_APP_METRIC_REGION", os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", ""))
)

# Public projection media objects are read through a pluggable store so the 1,253
# release images do not have to be carried in the git working tree.  The default is the
# credential-free, network-free local filesystem backend, so a developer, a tester, and
# an offline checkout all behave the same.  CI test jobs select the deterministic
# ``memory`` fixture backend in the workflow environment; a deployed environment selects
# ``s3`` and is checked for it under production settings.
PUBLIC_MEDIA_STORE_BACKEND = os.getenv("PUBLIC_MEDIA_STORE_BACKEND", "local").strip().lower()
PUBLIC_MEDIA_LOCAL_ROOT = Path(
    os.getenv("PUBLIC_MEDIA_LOCAL_ROOT") or (BASE_DIR / "content" / "public_projection" / "media")
)
PUBLIC_MEDIA_S3_BUCKET = os.getenv("PUBLIC_MEDIA_S3_BUCKET", "").strip()
# The media objects sit at the bucket root under ``images/``, which is already the
# leading segment of every projection record key, so no extra prefix is needed.
PUBLIC_MEDIA_S3_PREFIX = os.getenv("PUBLIC_MEDIA_S3_PREFIX", "").strip("/")
PUBLIC_MEDIA_S3_REGION = os.getenv("PUBLIC_MEDIA_S3_REGION", "").strip()
# Optional. Lets a developer point the s3 backend at a local or faked endpoint.
PUBLIC_MEDIA_S3_ENDPOINT_URL = os.getenv("PUBLIC_MEDIA_S3_ENDPOINT_URL", "").strip()
PUBLIC_MEDIA_S3_TIMEOUT_SECONDS = float(os.getenv("PUBLIC_MEDIA_S3_TIMEOUT_SECONDS", "5"))
# Fail-closed size bound. The largest known projection object is 3,022,797 bytes.
PUBLIC_MEDIA_MAX_OBJECT_BYTES = int(
    os.getenv("PUBLIC_MEDIA_MAX_OBJECT_BYTES", str(8 * 1024 * 1024))
)

DATAMAILER_URL = os.getenv("DATAMAILER_URL", "")
DATAMAILER_API_KEY = os.getenv("DATAMAILER_API_KEY", "")
DATAMAILER_CLIENT = os.getenv("DATAMAILER_CLIENT", "")
DATAMAILER_AUDIENCE = os.getenv("DATAMAILER_AUDIENCE", "")
DATAMAILER_FROM_EMAIL = os.getenv("DATAMAILER_FROM_EMAIL", "")
DATAMAILER_STRICT = env_flag("DATAMAILER_STRICT")
DATAMAILER_TIMEOUT_SECONDS = float(os.getenv("DATAMAILER_TIMEOUT_SECONDS", "60"))
DATAMAILER_TRANSACTIONAL_DRY_RUN = env_flag("DATAMAILER_TRANSACTIONAL_DRY_RUN")
DATAMAILER_WEBHOOK_TOKEN = os.getenv("DATAMAILER_WEBHOOK_TOKEN", "")
DATAMAILER_IMPORT_S3_BUCKET = os.getenv("DATAMAILER_IMPORT_S3_BUCKET", "")
DATAMAILER_IMPORT_S3_PREFIX = os.getenv("DATAMAILER_IMPORT_S3_PREFIX", "datamailer-imports").strip(
    "/"
)
DATAMAILER_IMPORT_URL_EXPIRES_SECONDS = int(
    os.getenv("DATAMAILER_IMPORT_URL_EXPIRES_SECONDS", "3600")
)
DATAMAILER_IMPORT_S3_REGION = os.getenv("DATAMAILER_IMPORT_S3_REGION", "")
DATAMAILER_SYNC_ON_USER_CREATE = env_flag("DATAMAILER_SYNC_ON_USER_CREATE", True)
DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY = env_flag("DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY")

# Relay recipient-link bridge.  Relay renders open, click and unsubscribe links
# from its own ``PUBLIC_BASE_URL``; that value points at this site, so this site
# has to answer ``/t/o/<token>.gif``, ``/t/c/<token>`` and ``/unsubscribe/<token>``
# and hand each one to Relay in-VPC.  Relay has no public listener, so the base
# below is the private ``http://relay.<zone>:8000`` address, never a public URL.
#
# Empty is the fail-closed default: with no configured Relay the three public
# routes answer 404 and the click route never redirects, so an unconfigured
# environment cannot become an open redirect.
RELAY_LINK_BRIDGE_BASE_URL = os.getenv("RELAY_LINK_BRIDGE_BASE_URL", "").strip()
# Distinct budgets, because the three endpoints have different stakes.  The open
# pixel is the highest-volume route in the system and must never park a worker;
# unsubscribe is low volume and prefers correctness over speed.
RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS = float(
    os.getenv("RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS", "2")
)
RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS = float(
    os.getenv("RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS", "3")
)
RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS = float(
    os.getenv("RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS", "10")
)
RELAY_LINK_BRIDGE_POOL_SIZE = int(os.getenv("RELAY_LINK_BRIDGE_POOL_SIZE", "16"))

Q_CLUSTER = {
    "name": "dtc-website",
    "workers": 2,
    # Recurring schedules are registered and evaluated only by the leased
    # scheduler-owner command. Ordinary qclusters must never contend as
    # implicit scheduler owners.
    "scheduler": False,
    "timeout": 300,
    "retry": 360,
    "max_attempts": 3,
    "queue_limit": 50,
    "save_limit": 250,
    "orm": "default",
}

NOINDEX = False
COMPATIBILITY_CONTRACT_PATHS: dict[str, str] = {}
REQUIRED_BOOTSTRAP_SETTINGS = ("SECRET_KEY", "ALLOWED_HOSTS", "DATABASES")

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Keep the cookie host-only. Cross-host course continuity uses explicit
# reauthentication and never broadens the cookie to ``.datatalks.club``.
SESSION_COOKIE_DOMAIN = None
# CMP's preserved loginas/browser flows read Django's CSRF cookie and submit it
# through the standard X-CSRFToken header.
CSRF_COOKIE_HTTPONLY = False
