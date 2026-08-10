from django.core.exceptions import ImproperlyConfigured

from test_support.runtime import current_worker_id, get_test_runtime

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = TEST_SECRET_KEY  # noqa: F405
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = ["http://localhost"]
ACCOUNT_CANONICAL_ORIGIN = "http://testserver"

if os.getenv("DTC_SQLITE_PATH"):  # noqa: F405
    raise ImproperlyConfigured("DTC_SQLITE_PATH cannot override the owned test worker database")

TEST_RUNTIME = get_test_runtime(BASE_DIR)  # noqa: F405
TEST_WORKER_ID = current_worker_id()
TEST_WORKER = TEST_RUNTIME.worker(TEST_WORKER_ID)
TEST_DATABASE_PATH = TEST_RUNTIME.assert_database_path(
    TEST_WORKER.database,
    worker_id=TEST_WORKER_ID,
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": TEST_DATABASE_PATH,
        "DTC_WORKER_ID": TEST_WORKER_ID,
        "TEST": {"NAME": TEST_DATABASE_PATH},
    }
}
TEST_RUNNER = "test_support.django_runner.IsolatedDiscoverRunner"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "test_support.email_backend.SyntheticCaptureEmailBackend"
DEFAULT_FROM_EMAIL = "DataTalks.Club tests <noreply@example.invalid>"
DATAMAILER_URL = ""
DATAMAILER_API_KEY = ""
DATAMAILER_CLIENT = ""
DATAMAILER_FROM_EMAIL = ""
DATAMAILER_STRICT = False
DATAMAILER_TRANSACTIONAL_DRY_RUN = True
DATAMAILER_SYNC_ON_USER_CREATE = False
DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY = False
AWS_EC2_METADATA_DISABLED = True
TEST_PROGRAMMATIC_STAFF_PASSWORD_AUTHENTICATION = True
HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE = True
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
