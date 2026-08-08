"""Build-only settings for generating release static assets.

This module deliberately reuses the self-contained test bootstrap while restoring
the manifest storage contract used by deployed settings. It must never be selected
by a deployed process.
"""

from django.core.exceptions import ImproperlyConfigured

from .test import *  # noqa: F403

if RUNTIME_ENVIRONMENT in {  # noqa: F405
    RuntimeEnvironment.DEVELOPMENT,  # noqa: F405
    RuntimeEnvironment.PRODUCTION,  # noqa: F405
}:
    raise ImproperlyConfigured("Collectstatic settings are build-only")

STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
