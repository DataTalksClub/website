"""Build-only settings for generating release static assets.

Static collection is part of the production image build, not a test run.  Keep this
module self-contained so importing it never acquires the Git-dependent test runtime.
It must never be selected by a deployed process.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if RUNTIME_ENVIRONMENT in {  # noqa: F405
    RuntimeEnvironment.DEVELOPMENT,  # noqa: F405
    RuntimeEnvironment.PRODUCTION,  # noqa: F405
}:
    raise ImproperlyConfigured("Collectstatic settings are build-only")

DEBUG = False
SECRET_KEY = TEST_SECRET_KEY  # noqa: F405
ALLOWED_HOSTS = ["localhost"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
