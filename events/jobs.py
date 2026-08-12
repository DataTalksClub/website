"""Durable event-total cache invalidation intent.

Positive public edge caching is intentionally disabled until #109.  The handler
therefore validates/coalesces the durable intent and performs no network call;
the job remains the hand-off seam for #109's cache provider.
"""

from __future__ import annotations

import re

from jobs.registry import JobContext, JobPayload, register_handler

_CANONICAL_EVENT_PATH = re.compile(
    r"^/events/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[a-z0-9]+(?:-[a-z0-9]+)*$"
)


@register_handler("events.registration_total.invalidate")
def invalidate_registration_total(context: JobContext, payload: JobPayload) -> None:
    del context
    path = payload.get("path")
    if (
        not isinstance(payload.get("total_state_id"), str)
        or not isinstance(payload.get("total_revision"), int)
        or not isinstance(path, str)
        or _CANONICAL_EVENT_PATH.fullmatch(path) is None
    ):
        raise ValueError("invalid event registration total invalidation intent")
