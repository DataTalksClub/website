"""Durable event-total cache invalidation intent.

Positive public edge caching is intentionally disabled until #109.  The handler
therefore validates/coalesces the durable intent and performs no network call;
the job remains the hand-off seam for #109's cache provider.
"""

from __future__ import annotations

from jobs.registry import JobContext, JobPayload, register_handler


@register_handler("events.registration_total.invalidate")
def invalidate_registration_total(context: JobContext, payload: JobPayload) -> None:
    del context
    path = payload.get("path")
    if (
        not isinstance(payload.get("total_state_id"), str)
        or not isinstance(payload.get("total_revision"), int)
        or not isinstance(path, str)
        or not path.startswith("/events/")
    ):
        raise ValueError("invalid event registration total invalidation intent")
