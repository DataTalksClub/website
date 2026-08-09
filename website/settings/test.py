from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = TEST_SECRET_KEY  # noqa: F405
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = ["http://localhost"]
ACCOUNT_CANONICAL_ORIGIN = "http://testserver"

DATABASES = {
    "default": sqlite_database_from_environment(  # noqa: F405
        environment=RuntimeEnvironment.TEST,  # noqa: F405
        default_path=BASE_DIR / ".tmp" / "test.sqlite3",  # noqa: F405
    )
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
TEST_PROGRAMMATIC_STAFF_PASSWORD_AUTHENTICATION = True
NOINDEX = True
OBSERVABILITY_EVENT_BACKENDS = ["noop"]
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
