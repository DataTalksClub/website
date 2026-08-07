import os
from pathlib import Path
from typing import Any, NoReturn, cast

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

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
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def missing(name: str) -> NoReturn:
    raise ImproperlyConfigured(f"Required bootstrap setting {name} is missing")


def secure_secret_from_environment(name: str = "DJANGO_SECRET_KEY") -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        missing(name)
    if value.strip() in UNSAFE_SECRET_KEYS or value.strip().startswith("django-insecure-"):
        raise ImproperlyConfigured(f"Required bootstrap setting {name} uses a known unsafe value")
    return value


def database_from_environment(*, allow_sqlite: bool = False) -> dict[str, Any]:
    if allow_sqlite and env_flag("DTC_USE_SQLITE"):
        sqlite_path = os.getenv("DTC_SQLITE_PATH", "db.sqlite3")
        path = Path(sqlite_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": path}

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        missing("DATABASE_URL")
    return cast(
        dict[str, Any],
        dj_database_url.parse(database_url, conn_max_age=60, conn_health_checks=True),
    )


SITE_NAME = "DataTalks.Club"
APP_VERSION = os.getenv("APP_VERSION", "dev")
ENVIRONMENT = os.getenv("DTC_ENVIRONMENT", "local")
CANONICAL_ORIGIN = os.getenv("CANONICAL_ORIGIN", "https://datatalks.club").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_q",
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "content.apps.ContentConfig",
    "content_sync.apps.ContentSyncConfig",
    "courses.apps.CoursesConfig",
    "events.apps.EventsConfig",
    "email_app.apps.EmailAppConfig",
    "studio.apps.StudioConfig",
    "api.apps.ApiConfig",
    "jobs.apps.JobsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.PrivateSurfaceMiddleware",
    "core.middleware.NoIndexMiddleware",
]

ROOT_URLCONF = "website.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_context",
            ]
        },
    }
]
WSGI_APPLICATION = "website.wsgi.application"
ASGI_APPLICATION = "website.asgi.application"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "studio:home"
LOGOUT_REDIRECT_URL = "home"

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

Q_CLUSTER = {
    "name": "dtc-website",
    "workers": 2,
    "timeout": 300,
    "retry": 360,
    "max_attempts": 3,
    "queue_limit": 50,
    "save_limit": 250,
    "orm": "default",
}

NOINDEX = False
REQUIRED_BOOTSTRAP_SETTINGS = ("SECRET_KEY", "ALLOWED_HOSTS", "DATABASES")

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
