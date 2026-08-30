"""Issue #237 synthetic rendered-review settings.

This opt-in module keeps every local-review network/provider denial and adds only
the exact side-effect-bounded synthetic interactions audited for the design loop.
"""

from test_support.design_review_identity import complaint_path

from .local_review import *  # noqa: F403

DEBUG = False
ISSUE_237_SYNTHETIC_INTERACTIONS_ENABLED = True
ISSUE_237_SYNTHETIC_COMPLAINT_PATH = complaint_path()
