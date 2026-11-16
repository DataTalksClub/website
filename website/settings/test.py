from django.core.exceptions import ImproperlyConfigured

from test_support.parallelism import apply_default_test_processes
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
# A bare ``--parallel`` forks one ~250 MiB worker per core; cap it by the
# memory actually available (see test_support/parallelism.py).
apply_default_test_processes()

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
AWS_EC2_METADATA_DISABLED = True
TEST_PROGRAMMATIC_STAFF_PASSWORD_AUTHENTICATION = True
NOINDEX = True
OBSERVABILITY_EVENT_BACKENDS = ["noop"]
COMMUNITY_BASE = {
    **COMMUNITY_BASE,
    "JOBS_BACKEND": "sync",
    # The mail app is installed (D1.2a) but nothing in the suite sends mail;
    # the package memory backend keeps an accidental send process-local.
    "MAIL_BACKEND": "memory",
}  # noqa: F405
MIDDLEWARE = [
    middleware
    for middleware in MIDDLEWARE  # noqa: F405
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Schema-2 importer asset bytes land here during tests: project-local,
# gitignored, never the source tree root.
MEDIA_ROOT = BASE_DIR / ".tmp" / "test-media"

# The suite never touches a real public media store, whatever the shell exports.
# The content sync uploads media objects, so an environment that selects ``s3``
# and a deployed bucket would otherwise have the tests overwrite production
# objects with fixture bytes, and ``local`` would write into the checkout's
# hydrated root.  The runtime registry reads the environment before these
# settings, so the ``PUBLIC_MEDIA_*`` variables are dropped here as well:
# otherwise an ``override_settings`` in a test would silently lose to the shell.
# A test that needs another backend opts in with ``override_settings``.
for _name in [name for name in os.environ if name.startswith("PUBLIC_MEDIA_")]:  # noqa: F405
    del os.environ[_name]  # noqa: F405
PUBLIC_MEDIA_STORE_BACKEND = "memory"
PUBLIC_MEDIA_LOCAL_ROOT = BASE_DIR / ".local-media" / "public"  # noqa: F405
PUBLIC_MEDIA_S3_BUCKET = ""
PUBLIC_MEDIA_S3_PREFIX = ""
PUBLIC_MEDIA_S3_REGION = ""
PUBLIC_MEDIA_S3_ENDPOINT_URL = ""
PUBLIC_MEDIA_S3_TIMEOUT_SECONDS = 5.0
PUBLIC_MEDIA_MAX_OBJECT_BYTES = 8 * 1024 * 1024
