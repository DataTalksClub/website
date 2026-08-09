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
APP_VERSION = os.getenv("APP_VERSION", "dev")
RUNTIME_ENVIRONMENT = parse_environment(os.getenv("DTC_ENVIRONMENT"))
ENVIRONMENT = RUNTIME_ENVIRONMENT.value
CANONICAL_ORIGIN = os.getenv("CANONICAL_ORIGIN", "https://datatalks.club").rstrip("/")

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
    "cadmin.apps.CadminConfig",
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
    "compatibility.monitoring.CompatibilityMonitoringMiddleware",
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
ACCOUNT_ALLOW_REGISTRATION = False
SOCIALACCOUNT_ADAPTER = "accounts.auth.ConsolidatingSocialAccountAdapter"
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_PROVIDERS = {
    "github": {"SCOPE": ["user:email"], "VERIFIED_EMAIL": True},
}
CAN_LOGIN_AS = can_login_as

UNFOLD = {
    "SITE_HEADER": "Course Management",
    "SITE_TITLE": "Course Management",
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

VERSION = APP_VERSION
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
# Keep the cookie host-only. Cross-host course continuity uses explicit
# reauthentication and never broadens the cookie to ``.datatalks.club``.
SESSION_COOKIE_DOMAIN = None
# CMP's preserved loginas/browser flows read Django's CSRF cookie and submit it
# through the standard X-CSRFToken header.
CSRF_COOKIE_HTTPONLY = False
