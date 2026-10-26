"""Leased, retryable Relay contact upserts after registration commits."""

from community_base.jobs.registry import JobContext, JobPayload, register_handler
from community_base.jobs.runner import RetryableJobError

from courses.services.registration_contacts import (
    HANDLER,
    report_contacts_failure,
    sync_registration_to_relay,
)


@register_handler(HANDLER)
def dispatch_registration_contacts(context: JobContext, payload: JobPayload) -> None:
    del context
    registration_id = payload["registration_id"]
    if not isinstance(registration_id, int) or isinstance(registration_id, bool):
        raise ValueError("invalid_registration_id")
    try:
        sync_registration_to_relay(registration_id)
    except Exception:
        report_contacts_failure(registration_id)
        # No provider text, recipient data or configuration in job diagnostics.
        raise RetryableJobError("registration_contacts_failed") from None
