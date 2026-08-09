"""Local-only settings for browsing a sanitized CMP review database."""

import os

from django.core.exceptions import ImproperlyConfigured

from review_import.environment import disable_local_review_provider_environment

disable_local_review_provider_environment(os.environ)

from .local import *  # noqa: E402,F403

if RUNTIME_ENVIRONMENT != RuntimeEnvironment.LOCAL:  # noqa: F405
    raise ImproperlyConfigured("The local review settings require DTC_ENVIRONMENT=local")

if DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":  # noqa: F405
    raise ImproperlyConfigured("The local review settings require SQLite")

# Review data must never trigger mail, Datamailer/provider traffic, or
# background work. These values deliberately override both the shell and the
# local `.env` loaded by `website.settings.local`.
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
DATAMAILER_URL = ""
DATAMAILER_API_KEY = ""
DATAMAILER_CLIENT = ""
DATAMAILER_AUDIENCE = ""
DATAMAILER_FROM_EMAIL = ""
DATAMAILER_STRICT = False
DATAMAILER_TIMEOUT_SECONDS = 0.0
DATAMAILER_TRANSACTIONAL_DRY_RUN = True
DATAMAILER_WEBHOOK_TOKEN = ""
DATAMAILER_IMPORT_S3_BUCKET = ""
DATAMAILER_IMPORT_S3_PREFIX = ""
DATAMAILER_IMPORT_URL_EXPIRES_SECONDS = 0
DATAMAILER_IMPORT_S3_REGION = ""
DATAMAILER_SYNC_ON_USER_CREATE = False
DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY = False
CLOUDWATCH_APP_METRIC_REGION = ""
AWS_REGION = ""
AWS_DEFAULT_REGION = ""
Q_CLUSTER = {**Q_CLUSTER, "sync": True, "scheduler": False}  # noqa: F405
LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED = True
_AUTH_MIDDLEWARE = "django.contrib.auth.middleware.AuthenticationMiddleware"
_BASE_MIDDLEWARE = list(globals()["MIDDLEWARE"])
_AUTH_MIDDLEWARE_INDEX = _BASE_MIDDLEWARE.index(_AUTH_MIDDLEWARE) + 1
MIDDLEWARE = [
    *_BASE_MIDDLEWARE[:_AUTH_MIDDLEWARE_INDEX],
    "review_import.middleware.LocalReviewNoNetworkMiddleware",
    *_BASE_MIDDLEWARE[_AUTH_MIDDLEWARE_INDEX:],
]
