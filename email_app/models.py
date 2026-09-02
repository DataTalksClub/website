"""Email-domain records the website owns.

The logical ``EmailDelivery`` intent is still deferred to its own issue.  The one
record here exists for a single narrow reason: an unsubscribe request must never
be refused because Relay happened to be unreachable at that moment.  When the
synchronous hand-off fails, the request is persisted and replayed by a leased
durable job, which is the only Relay-calling boundary for a mutation.
"""

from __future__ import annotations

import uuid

from django.db import models


class PendingUnsubscribe(models.Model):
    """One opt-out that was accepted from a recipient but not yet applied in Relay.

    The raw Relay token is stored because replaying the request is the entire
    point of the record and Relay accepts nothing else.  The row is therefore
    recipient-identifying and is treated that way: it is deleted as soon as Relay
    confirms the opt-out, it is never rendered into a page, a log line or an
    observability event, and no admin registration exposes it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Opaque Relay-minted token.  Unique so that a recipient who submits twice
    # during one outage produces one replay rather than two.
    unsubscribe_token = models.CharField(max_length=128, unique=True)
    # A stable, non-reversible handle for the same token, so operational queries
    # and diagnostics never need to select the token column.
    token_fingerprint = models.CharField(max_length=32, db_index=True)
    scope = models.CharField(max_length=16)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_outcome = models.CharField(max_length=32, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pending unsubscribe"
        verbose_name_plural = "pending unsubscribes"
        indexes = [
            models.Index(fields=["status", "accepted_at"], name="pending_unsub_status_idx"),
        ]

    def __str__(self) -> str:
        # Never the token, and never the scope keyed to a person.
        return f"pending unsubscribe {self.token_fingerprint}"
