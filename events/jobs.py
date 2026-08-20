"""Durable event-total cache invalidation intent.

Positive public edge caching is intentionally disabled until #109.  The handler
therefore validates/coalesces the durable intent and performs no network call;
the job remains the hand-off seam for #109's cache provider.
"""

from __future__ import annotations

import re
import uuid

from jobs.execution import PermanentJobError
from jobs.registry import JobContext, JobPayload, register_handler

from .qna.services import PROVISION_VERSION, ensure_native_event_qna

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


@register_handler("events.qna.provision")
def provision_event_qna(context: JobContext, payload: JobPayload) -> None:
    """Converge the current Event-owned session in a leased worker."""

    del context
    event_id = payload.get("event_id")
    if payload.get("version") != PROVISION_VERSION or not isinstance(event_id, str):
        raise PermanentJobError("invalid_qna_provisioning_payload")
    try:
        parsed_event_id = uuid.UUID(event_id)
    except ValueError as exc:
        raise PermanentJobError("invalid_qna_provisioning_payload") from exc
    if str(parsed_event_id) != event_id or parsed_event_id.variant != uuid.RFC_4122:
        raise PermanentJobError("invalid_qna_provisioning_payload")
    try:
        ensure_native_event_qna(parsed_event_id)
    except LookupError as exc:
        raise PermanentJobError("event_not_found") from exc
