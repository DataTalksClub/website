"""Attendee-level event registration facts.

These models moved verbatim out of the ``events`` app (#412) so their rows
survive the P5 rebuild that swaps the ``events`` label to
``community_base.events``.  Every row points at ``events.Event`` -- the
identity row that label names both before and after the swap.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class EventRegistrantIdentity(models.Model):
    """The consolidated real person behind one or more provider event registrations.

    Matching happens in :mod:`scripts.prod.registrant_import`, by ``normalized_email``,
    against ``accounts_user`` first -- the same table and field
    ``accounts.services.cmp_learner_import`` already uses for its own
    cross-source deduplication (see ``_find_cross_source_match`` there). When
    that lookup finds an account, ``account`` is set here and this row is a
    pointer onto it, never a competing profile -- this is the case the owner
    was explicit about: someone who both took a course and registered for an
    event must resolve to that one account, never two. When it finds nothing,
    and no prior registrant-only identity already claims the address, a new
    row is created with ``normalized_email`` set and ``account`` left null --
    a real identity in the same email-keyed space, but deliberately never a
    login-capable ``User`` row (self-registration is closed, see
    ``accounts.models.User`` / commit ``c237ef2``). A future import that
    matches this address onto a real account attaches through the same
    account-first lookup on its next run -- a plain merge, nothing special
    needs to be built for that later.

    No Studio surface reads this table in this first pass. That is a
    deliberate, conservative default, not an oversight -- matching accounts
    (the common case) already have full account handling via existing paths,
    and an unmatched registrant-only identity is pure backend data until a
    future pass decides it needs one. See the ingest inventory, section 9.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="event_registrant_identity",
    )
    # Only ever set when `account` is null -- an account-anchored identity's
    # email is read from the account itself, never cached here where it could
    # drift out of sync with it.
    # NULL is load-bearing, not an empty value: the check constraint below and the
    # partial unique index both key off `normalized_email IS NULL`.
    normalized_email = models.EmailField(  # noqa: DJ001 -- null marks an account-anchored row.
        max_length=254, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_email",),
                condition=Q(account__isnull=True),
                name="event_registrants_identity_email_unique_unmatched",
            ),
            models.CheckConstraint(
                condition=(
                    Q(account__isnull=False, normalized_email__isnull=True)
                    | Q(account__isnull=True, normalized_email__isnull=False)
                ),
                name="event_registrants_identity_exactly_one_anchor",
            ),
        ]

    def __str__(self) -> str:
        return f"event-registrant-identity:{self.id}"


class EventRegistration(models.Model):
    """One provider registration fact, pointing at a consolidated identity.

    Never carries a name, an email, a phone number, or any other directly
    identifying attendee value -- those stay in the protected source export,
    never copied into the database.  It also never stores the provider's own
    per-attendee token (Luma ``guest_id``; Eventbrite order/attendee id):
    replay safety does not come from a natural key on this table at all --
    see :class:`EventRegistrantImportProgress` and
    ``scripts.prod.registrant_import``. One event's rows are read and written
    inside a single transaction, gated on that event's progress row not
    already being ``completed``; a killed run leaves nothing partially
    written for a later run to duplicate against, so there is nothing this
    table itself needs to deduplicate on, and no reason to keep a protected
    per-attendee token around permanently to do it with.

    The consequence, and it is the reason a refresh works the way it does: a
    newer export of an event we already hold cannot be merged row by row,
    because there is no key to merge on.  ``scripts.prod.registrant_import``'s
    ``refresh`` replaces the event's rows for that provider wholesale instead,
    so an ``id`` and a ``created_at`` here are stable only until the next
    refresh of that event.  Nothing reads either.

    Public event pages are unaffected by this table.  They keep showing
    ``HistoricalRegistrationAggregateRevision``-derived counts through the
    existing ``mapping_review_required``/activation flow; a later pass may
    derive that aggregate from these rows instead, but this model does not
    change how a public page gets its count.
    """

    class Provider(models.TextChoices):
        LUMA = "luma", "Luma"
        EVENTBRITE = "eventbrite", "Eventbrite"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        "events.Event", on_delete=models.PROTECT, related_name="registrant_registrations"
    )
    identity = models.ForeignKey(
        EventRegistrantIdentity, on_delete=models.PROTECT, related_name="registrations"
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    status = models.CharField(max_length=32)
    registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("event_id", "provider", "id")
        indexes = [
            models.Index(fields=("identity",), name="event_registrants_reg_identity"),
        ]

    def __str__(self) -> str:
        return f"event-registration:{self.id}"


class EventRegistrantInterestSignal(models.Model):
    """A broad, non-event-specific interest signal, distinct from a real registration.

    ``EventRegistration`` above states a fact a provider actually recorded:
    "this identity registered for event X" -- it always carries a specific
    ``event`` FK. A Mailchimp audience-export tag like ``event-podcast``
    states something weaker: "this identity is broadly associated with
    podcast-related events", with no specific event named anywhere in the
    source data. Folding that into ``EventRegistration`` would either force a
    fabricated ``event`` value (there isn't one) or silently blur two
    different kinds of fact for a future reader -- one row that means
    "attended" sitting next to one that only ever meant "self-tagged
    interest, sourced from an email platform, no event identified". This
    model exists so that distinction stays visible in the schema itself, not
    just in a docstring: a query against ``EventRegistration`` can never
    accidentally pick up a tag-derived signal, and vice versa.

    ``category`` is populated only from the reviewed, hardcoded mapping in
    :mod:`events.mailchimp_event_tag_categories` -- never inferred from a raw
    tag string at read time. ``source`` records where the signal came from;
    it is deliberately only ``mailchimp_tag`` today (the one producer that
    exists), kept as a field rather than assumed so a second producer, if one
    is ever built, does not require a schema change to be told apart from
    the first.

    One row per (identity, category, source): a subscriber tagged with both
    ``event-podcast`` and ``event-conference`` gets two rows, not one row
    with two values crammed in -- the same one-fact-per-row discipline
    ``EventRegistration`` already uses. The unique constraint below is also
    what makes importing idempotent: a replay's ``get_or_create`` finds the
    row instead of duplicating it.
    """

    class Category(models.TextChoices):
        GENERAL = "general", "General"
        CONFERENCE = "conference", "Conference"
        PODCAST = "podcast", "Podcast"
        PRODUCTION = "production", "Production"
        ANALYTICS = "analytics", "Analytics"
        DATA = "data", "Data"
        SOFT_SKILLS = "soft_skills", "Soft skills"
        DATA_SCIENCE = "data_science", "Data science"

    class Source(models.TextChoices):
        MAILCHIMP_TAG = "mailchimp_tag", "Mailchimp tag"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    identity = models.ForeignKey(
        EventRegistrantIdentity, on_delete=models.PROTECT, related_name="interest_signals"
    )
    category = models.CharField(max_length=32, choices=Category.choices)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MAILCHIMP_TAG)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("identity_id", "category", "source")
        constraints = [
            models.UniqueConstraint(
                fields=("identity", "category", "source"),
                name="event_registrants_interest_signal_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"event-registrant-interest-signal:{self.id}"


class EventRegistrantImportProgress(models.Model):
    """Per-(provider, event) completion marker for the resumable registrant import.

    Unlike ``accounts_ext.models.CmpLearnerImportProgress`` (one monotonic source
    table, watermarked by row id), a Luma/Eventbrite export is one bounded file
    per event -- the largest event file in the real export is a few thousand
    rows.  So resumability here is at event granularity rather than row
    granularity: one event's registrant rows are read and written inside a
    single transaction, and this row is only flipped to ``completed`` once
    that transaction commits.  A re-run skips a completed event without even
    reopening its file.  An event interrupted mid-transaction is simply
    retried whole on the next run -- cheap, because a single event's file is
    small enough that redoing it in full is not the same problem CMP's 20,009
    rows would have been.

    ``updated_at`` is therefore also the per-event answer to "when did we last
    read this event's registrations", which is what an operator needs to decide
    which events a newer export makes stale.  ``scripts.prod.registrant_import``'s
    ``refresh`` re-reads a completed event and replaces its registration facts;
    it moves this row's counts and ``updated_at`` and never clears ``completed``.
    """

    provider = models.CharField(max_length=16, choices=EventRegistration.Provider.choices)
    external_event_identifier = models.CharField(max_length=512)
    completed = models.BooleanField(default=False)
    rows_total = models.PositiveIntegerField(default=0)
    rows_written = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    matched_account_total = models.PositiveIntegerField(default=0)
    matched_prior_identity_total = models.PositiveIntegerField(default=0)
    new_identity_total = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("provider", "external_event_identifier")
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "external_event_identifier"),
                name="event_registrants_import_progress_provider_external_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"registrant-import-progress:{self.provider}:{self.external_event_identifier}"
