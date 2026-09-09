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

from community_base.jobs.dispatch import dispatch_after_commit
from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models import F

from email_app import relay_links
from email_app.models import PendingUnsubscribe

UNSUBSCRIBE_REPLAY_HANDLER = "email.unsubscribe-replay"
# An opt-out is worth persisting harder than an ordinary side effect.  Twenty
# attempts against the capped exponential backoff spans well over a day, which
# comfortably covers any Relay outage that is not itself an incident.
UNSUBSCRIBE_REPLAY_MAX_ATTEMPTS = 20


def replay_job_key(pending_id: uuid.UUID, generation: int) -> str:
    """The durable job's deduplication key for one opt-out generation.

    The generation is part of the key on purpose: a recipient re-submitting
    against a still-pending row changes it, so the fresh request dispatches a
    brand-new intent even when the previous one is leased or already terminal
    -- a terminal intent is never claimable again, so reusing its key would
    strand the opt-out with no runnable work (audit BE-10).
    """

    return f"email:unsubscribe-replay:{pending_id}:{generation}"


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
        if created:
            generation = pending.generation
        else:
            # Honour the newer choice, and give it its own generation: the
            # recipient is the authority on which mail they want stopped, and
            # a fresh accepted request must always produce fresh runnable
            # work.  The bump is a single atomic UPDATE, so concurrent
            # re-acceptances each advance the generation exactly once and each
            # dispatch exactly one new intent; the older generation's job,
            # whatever state it is in, can no longer settle this row (audit
            # BE-11).
            PendingUnsubscribe.objects.using(using).filter(pk=pending.pk).update(
                scope=scope,
                generation=F("generation") + 1,
            )
            pending.refresh_from_db(using=using)
            generation = pending.generation

        dispatch_after_commit(
            UNSUBSCRIBE_REPLAY_HANDLER,
            replay_job_key(pending.id, generation),
            {"pending_unsubscribe_id": str(pending.id)},
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

    # The generation is captured with the read.  Whatever happens to this row
    # while Relay is being called -- a newer scope choice, a re-acceptance --
    # belongs to a newer generation, and this older attempt must never settle
    # it: an older success used to delete the row carrying the newer choice
    # (audit BE-11).  No lock is held across the Relay call; the generation
    # check is what makes the settlement safe.
    generation = pending.generation

    result = relay_links.submit_unsubscribe(pending.unsubscribe_token, pending.scope)
    still_current = (
        PendingUnsubscribe.objects.using(using)
        .filter(pk=pending.pk, generation=generation)
        .update(
            attempt_count=F("attempt_count") + 1,
            last_outcome=result.outcome.value,
        )
    )

    if result.outcome is relay_links.BridgeOutcome.RECORDED:
        # The opt-out this generation sent is Relay's now.  The
        # recipient-identifying row is removed only if it still carries this
        # generation; otherwise a newer choice owns it and its own job will
        # finish the work.
        deleted, _ = (
            PendingUnsubscribe.objects.using(using)
            .filter(pk=pending.pk, generation=generation)
            .delete()
        )
        if not deleted:
            return "superseded"
        return "applied"
    if result.outcome is relay_links.BridgeOutcome.REJECTED:
        # Relay does not know this link.  Retrying cannot change that -- but
        # only this generation's request was rejected; a newer generation's
        # choice stays pending for its own job.
        if not still_current:
            return "superseded"
        PendingUnsubscribe.objects.using(using).filter(
            pk=pending.pk,
            generation=generation,
            status=PendingUnsubscribe.Status.PENDING,
        ).update(status=PendingUnsubscribe.Status.REJECTED)
        return "rejected"
    return result.outcome.value
