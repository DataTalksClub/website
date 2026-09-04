"""The leased durable job that finishes an accepted opt-out.

This is the boundary the architecture reserves for a Relay mutation: a leased
job, running after the accepting transaction committed, carrying only a scalar
identifier and resolving the recipient token itself.
"""

from __future__ import annotations

import uuid

from jobs.execution import PermanentJobError, RetryableJobError
from jobs.registry import JobContext, JobPayload, register_handler

from .services import UNSUBSCRIBE_REPLAY_HANDLER, replay_pending_unsubscribe


@register_handler(UNSUBSCRIBE_REPLAY_HANDLER)
def replay_unsubscribe(context: JobContext, payload: JobPayload) -> None:
    del context
    raw_id = payload.get("pending_unsubscribe_id")
    if not isinstance(raw_id, str):
        raise PermanentJobError("invalid_unsubscribe_replay_payload")
    try:
        pending_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise PermanentJobError("invalid_unsubscribe_replay_payload") from exc

    outcome = replay_pending_unsubscribe(pending_id)
    if outcome in {"applied", "absent", "settled", "rejected"}:
        return
    if outcome == "not_configured":
        # No Relay is wired up in this deployment.  Retrying cannot help, and a
        # job that retries for a day would only hide the misconfiguration.
        raise PermanentJobError("relay_bridge_not_configured")
    # Unavailable or an unexpected Relay answer.  The intent stays durable and
    # the recipient's opt-out is retried; it is not dropped.
    raise RetryableJobError("relay_unavailable")
