"""The allowlisted keys ``CustomUser.home_dismissals`` may hold.

Owned by ``accounts`` because ``home_dismissals`` is an accounts field and the
dismiss endpoint (``accounts/views/home_dismissals.py``) must validate against
this list without importing the courses app.  ``courses/services/member_home.py``
imports these constants (``courses`` already depends on ``accounts`` elsewhere,
e.g. ``courses/models/cohort.py``), rather than the reverse, per
``_docs/architecture/app-boundaries.md``.
"""

from __future__ import annotations

CHECKLIST_SKIP_COURSE = "getting_started_skip_course"
CHECKLIST_SLACK_DONE = "getting_started_slack_done"
CHECKLIST_SKIP_SLACK = "getting_started_skip_slack"
CHECKLIST_SKIP_PROFILE = "getting_started_skip_profile"
CHECKLIST_DISMISSED = "getting_started_checklist"
PROFILE_NUDGE_DISMISSED = "profile_nudge"

HOME_DISMISSAL_KEYS = frozenset(
    {
        CHECKLIST_SKIP_COURSE,
        CHECKLIST_SLACK_DONE,
        CHECKLIST_SKIP_SLACK,
        CHECKLIST_SKIP_PROFILE,
        CHECKLIST_DISMISSED,
        PROFILE_NUDGE_DISMISSED,
    }
)
