"""Durable Q&A provisioning intent handler.

Moved from ``events.jobs`` (#412) with the rest of the Q&A implementation.
The handler name keeps its historical "events.qna.provision" form: durable
intents stored before the app split reference it, and renaming it would
orphan queued work.
"""

from __future__ import annotations

import uuid

from community_base.jobs.registry import JobContext, JobPayload, register_handler
from community_base.jobs.runner import PermanentJobError

from .services import PROVISION_VERSION, ensure_native_event_qna


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
