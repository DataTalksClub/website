"""Application services for the Relay recipient-link seam.

The only business mutation on this seam is an unsubscribe, and it belongs to
Relay.  What the website owns is the promise made to the person who clicked:
once we have told a recipient their opt-out is recorded, it has to happen, even
if Relay was unreachable at that instant.  That promise is kept here -- persist
the intent in a transaction, then hand the replay to a leased durable job after
commit, which is the architecture's standing rule for a network side effect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import DEFAULT_DB_ALIAS, transaction

from email_app import relay_links
from email_app.models import PendingUnsubscribe
from jobs.dispatch import dispatch_after_commit

UNSUBSCRIBE_REPLAY_HANDLER = "email.unsubscribe-replay"
# An opt-out is worth persisting harder than an ordinary side effect.  Twenty
# attempts against the capped exponential backoff spans well over a day, which
# comfortably covers any Relay outage that is not itself an incident.
UNSUBSCRIBE_REPLAY_MAX_ATTEMPTS = 20


@dataclass(frozen=True, slots=True)
class AcceptedUnsubscribe:
    pending_id: uuid.UUID
    created: bool


def accept_unsubscribe_for_replay(
    *,
    token: str,
    scope: str,
    using: str = DEFAULT_DB_ALIAS,
) -> AcceptedUnsubscribe:
    """Record an opt-out the website accepted but Relay has not yet applied.

    The caller has already failed to reach Relay.  Validation still runs, because
    a durable record of a malformed request would be a permanently failing job.
    """

    if not relay_links.is_well_formed_token(token):
        raise ValueError("malformed unsubscribe token")
    if scope not in relay_links.UNSUBSCRIBE_SCOPES:
        raise ValueError("unsupported unsubscribe scope")

    with transaction.atomic(using=using):
        # A row that already reached a terminal state carries a durable job that
        # will never run again.  A recipient asking a second time deserves a
        # fresh intent, not a revived one, so the settled row is replaced.
        PendingUnsubscribe.objects.using(using).filter(unsubscribe_token=token).exclude(
            status=PendingUnsubscribe.Status.PENDING
        ).delete()
        pending, created = PendingUnsubscribe.objects.using(using).get_or_create(
            unsubscribe_token=token,
            defaults={
                "token_fingerprint": relay_links.token_fingerprint(token),
                "scope": scope,
                "status": PendingUnsubscribe.Status.PENDING,
            },
        )
        if not created and pending.scope != scope:
            # Honour the newer choice: the recipient is the authority on which
            # mail they want stopped.
            pending.scope = scope
            pending.save(using=using, update_fields=["scope", "updated_at"])

        dispatch_after_commit(
            handler=UNSUBSCRIBE_REPLAY_HANDLER,
            deduplication_key=f"email:unsubscribe-replay:{pending.id}",
            payload={"pending_unsubscribe_id": str(pending.id)},
            max_attempts=UNSUBSCRIBE_REPLAY_MAX_ATTEMPTS,
            using=using,
        )

    return AcceptedUnsubscribe(pending_id=pending.id, created=created)


def replay_pending_unsubscribe(
    pending_id: uuid.UUID,
    *,
    using: str = DEFAULT_DB_ALIAS,
) -> str:
    """Apply one persisted opt-out in Relay.  Returns a low-cardinality outcome.

    The token is read here, inside the worker, and is never carried in the job
    payload -- the durable payload contract forbids a protected value, and a
    scalar identifier is all a worker needs.
    """

    pending = PendingUnsubscribe.objects.using(using).filter(pk=pending_id).first()
    if pending is None:
        return "absent"
    if pending.status != PendingUnsubscribe.Status.PENDING:
        return "settled"

    result = relay_links.submit_unsubscribe(pending.unsubscribe_token, pending.scope)
    PendingUnsubscribe.objects.using(using).filter(pk=pending.pk).update(
        attempt_count=pending.attempt_count + 1,
        last_outcome=result.outcome.value,
    )

    if result.outcome is relay_links.BridgeOutcome.RECORDED:
        # The opt-out is Relay's now.  The recipient-identifying row has served
        # its purpose and is removed rather than retained.
        PendingUnsubscribe.objects.using(using).filter(pk=pending.pk).delete()
        return "applied"
    if result.outcome is relay_links.BridgeOutcome.REJECTED:
        # Relay does not know this link.  Retrying cannot change that.
        PendingUnsubscribe.objects.using(using).filter(pk=pending.pk).update(
            status=PendingUnsubscribe.Status.REJECTED,
        )
        return "rejected"
    return result.outcome.value
