"""Issue #237 synthetic rendered-review settings.

This opt-in module keeps every local-review network/provider denial and adds only
the exact side-effect-bounded synthetic interactions audited for the design loop.
"""

from test_support.design_review_identity import complaint_path

from .local_review import *  # noqa: F403

DEBUG = False
ISSUE_237_SYNTHETIC_INTERACTIONS_ENABLED = True
ISSUE_237_SYNTHETIC_COMPLAINT_PATH = complaint_path()

# DEBUG is off here but nothing is collected: the review runs from source, so
# static files resolve without a manifest (a page that names a static file
# would otherwise fail to render, 404 page included).
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
