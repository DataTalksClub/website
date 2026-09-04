"""Reviewed, hardcoded mapping from Mailchimp audience-export tags to event categories.

The Mailchimp subscribed-audience export's ``TAGS`` column carries a small,
fixed vocabulary of 32 distinct values (verified structurally against the
real export while building ``accounts.services.mailchimp_subscription_import``,
and re-verified against this module's own real-export check). Exactly three
disjoint groups live in that vocabulary:

1. **Course-cohort tags** (e.g. ``de-zoomcamp-2026``, ``llm-zoomcamp-2025``) --
   completely out of scope for this module. They are blocked on a separate,
   unresolved decision gate about how course-tag history should map onto
   ``courses.course_family_catalog``, and nothing here reads, stores, or
   otherwise references them. A tag not present as a key in
   :data:`MAILCHIMP_EVENT_TAG_CATEGORIES` or as a member of
   :data:`DROPPED_MAILCHIMP_TAGS` is, by construction, one of these -- silently
   ignored by every caller of this module, not specially detected.
2. **The 8 event-category tags below** -- the only tags this module maps to
   anything. Each names a broad interest, never a specific event: Mailchimp's
   own tagging has no per-event granularity at all (contrast
   ``events.registrant_import``, whose Luma/Eventbrite rows each name one
   real event). The mapping is a small, explicit, reviewed table -- not
   pattern-matched from the tag string at runtime -- because a fixed
   vocabulary of 8 values is exactly the case a reviewed table is for, and
   the owner's own words were explicit: "we will need to have hardcoded
   mapping."
3. **Three miscellaneous tags, dropped entirely by owner decision**:
   ``registered-in-slack``, ``Berlin DataTalks Club Group``,
   ``ai-bootcamp-free-email-course``. Listed in :data:`DROPPED_MAILCHIMP_TAGS`
   so a reader can tell "considered and explicitly rejected" apart from "not
   yet reviewed" -- they are not simply absent from the mapping the way a
   course tag is.

Values are :class:`events.models.EventRegistrantInterestSignal.Category`
members (their ``.value``, a plain string here to keep this module free of a
model import): ``event`` itself carries no sub-category, so it maps to the
catch-all ``general``; the other seven map onto the category their own tag
name already names.
"""

from __future__ import annotations

__all__ = ["DROPPED_MAILCHIMP_TAGS", "MAILCHIMP_EVENT_TAG_CATEGORIES"]

# Mailchimp tag string (verbatim, case-sensitive, as it appears in the TAGS
# column after stripping Mailchimp's own per-tag quoting -- see
# events.mailchimp_tag_import.parse_mailchimp_tags) -> the
# EventRegistrantInterestSignal.Category value it is reviewed to mean.
MAILCHIMP_EVENT_TAG_CATEGORIES: dict[str, str] = {
    "event": "general",
    "event-conference": "conference",
    "event-podcast": "podcast",
    "event-production": "production",
    "event-analytics": "analytics",
    "event-data": "data",
    "events-soft": "soft_skills",
    "events-data-science": "data_science",
}

# Dropped entirely, by owner decision -- never imported in any form, by this
# module or any other. Not course tags (those are simply out of this
# mapping's vocabulary, see the module docstring); these three were reviewed
# and explicitly rejected.
DROPPED_MAILCHIMP_TAGS: frozenset[str] = frozenset(
    {
        "registered-in-slack",
        "Berlin DataTalks Club Group",
        "ai-bootcamp-free-email-course",
    }
)
