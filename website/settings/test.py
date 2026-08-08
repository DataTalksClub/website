import os

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = TEST_SECRET_KEY  # noqa: F405
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = ["http://localhost"]

if os.getenv("DATABASE_URL"):
    DATABASES = {
        "default": database_from_environment(  # noqa: F405
            environment=RuntimeEnvironment.TEST,  # noqa: F405
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test.sqlite3",  # noqa: F405
        }
    }

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
NOINDEX = True
OBSERVABILITY_EVENT_BACKENDS = ["noop"]
# Django's TransactionTestCase flushes tables with TRUNCATE. The core migration
# also requires either Django's generated `test_*` name or the explicitly
# provisioned CI database `dtc_test`, so this code-owned opt-in cannot weaken a
# deployed database merely because of its name.
CORE_ALLOW_APPEND_ONLY_TEST_FLUSH = True
Q_CLUSTER = {**Q_CLUSTER, "sync": True}  # noqa: F405
MIDDLEWARE = [
    middleware
    for middleware in MIDDLEWARE  # noqa: F405
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
