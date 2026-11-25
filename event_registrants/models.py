"""Attendee-level event registration facts.

These models moved verbatim out of the ``events`` app (#412) so their rows
survive the P5 rebuild that swaps the ``events`` label to
``community_base.events``.  Every row points at ``events.Event`` -- the
identity row that label names both before and after the swap.
"""

from __future__ import annotations

import uuid
from typing import Any

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
    ``refresh`` replaces the rows of one *provider event* wholesale instead --
    those attributed to it by ``source_event_identifier``, plus unattributed
    rows written before attribution existed -- so an ``id`` and a ``created_at``
    here are stable only until the next refresh of that event.  Nothing reads
    either.

    Public event pages count eligible rows from this table directly. Provider
    exports are translated only by the ingest scripts; runtime reads never
    consult a legacy aggregate, mapping, or fallback projection.
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
    #: The provider's own *event* id this fact was imported from (a Luma
    #: ``evt-...`` or an Eventbrite event number -- public, part of the event's
    #: URL, not an attendee value). One ``Event`` can hold rows from more than
    #: one provider event, so a refresh replaces only the rows of the provider
    #: event it re-read. Blank on rows written before attribution existed.
    source_event_identifier = models.CharField(max_length=512, blank=True, default="")
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
    #: The ``Event`` this provider event's rows were last written to. A claim:
    #: the title-and-start fallback never sends a second provider event to an
    #: event another one already holds, even when that one is skipped as
    #: completed on a later run.
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
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


class EventOperatorConfig(models.Model):
    """Site-side operator configuration and uploaded banner for an ``events.Event``.

    Extends ``community_base.events.Event`` without modifying the shared package
    schema: per-event reminder intervals, sponsor-sharing / company-collection
    flags, and operator-uploaded 1000x1000 banner bytes.
    """

    event = models.OneToOneField(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="operator_config",
    )
    reminders_enabled = models.BooleanField(default=True)
    reminder_intervals = models.JSONField(default=list, blank=True)
    sponsor_sharing_enabled = models.BooleanField(default=False)
    collect_company = models.BooleanField(default=False)
    sponsor_name = models.CharField(max_length=200, blank=True, default="")
    banner_image = models.BinaryField(null=True, blank=True)
    banner_content_type = models.CharField(max_length=64, blank=True, default="")
    banner_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("view_event_admin", "View events in Studio and admin API"),
            ("manage_events", "Create, edit, publish, cancel, and complete events"),
            ("export_event_registrants", "Export event registrants"),
            ("message_event_registrants", "Message event registrants"),
        ]

    def __str__(self) -> str:
        return f"event-operator-config:{self.event_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Refuse a row without picture bytes: a picture is replaced, never cleared.

        The row is created together with the event
        (:func:`events.identity.create_event_identity`), so a row without a
        picture would be an event without one (``events.pictures``).
        """

        from events.pictures import EventPictureRequired, has_picture_bytes

        update_fields = kwargs.get("update_fields")
        if (update_fields is None or "banner_image" in update_fields) and not has_picture_bytes(
            self.banner_image
        ):
            raise EventPictureRequired()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse: the row holds the event's picture.  Deleting the event cascades."""

        from events.pictures import EventPictureRequired

        raise EventPictureRequired()


class EventSponsor(models.Model):
    """One sponsor of one event, linked to the sponsor directory (``core.Sponsor``).

    A sponsored workshop or webinar names its sponsor(s) here; the public event
    page, the ``/events`` card and the event picture read the sponsor's name and
    logo from the directory row, never from copy of their own.  ``position``
    orders several sponsors of one event.  Written only by
    ``events.admin_services`` (``sponsors`` on create/update), which replaces the
    whole list.  Unrelated to ``EventOperatorConfig.sponsor_name``, which is the
    party registrants consent to share their email with.
    """

    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, related_name="event_sponsors"
    )
    sponsor = models.ForeignKey(
        "core.Sponsor", on_delete=models.PROTECT, related_name="event_links"
    )
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("event_id", "position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "sponsor"), name="event_registrants_event_sponsor_unique"
            ),
            models.UniqueConstraint(
                fields=("event", "position"),
                name="event_registrants_event_sponsor_position_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"event-sponsor:{self.event_id}:{self.sponsor_id}"


class EventRegistrationMeta(models.Model):
    """Site-side registration metadata (company and sponsor-sharing consent).

    Linked 1:1 to ``community_base.events.EventRegistration`` (app label ``events``).
    """

    registration = models.OneToOneField(
        "events.EventRegistration",
        on_delete=models.CASCADE,
        related_name="meta",
    )
    company = models.CharField(max_length=200, blank=True, default="")
    sponsor_sharing_consent = models.BooleanField(null=True, blank=True)
    sponsor_sharing_consented_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"event-registration-meta:{self.registration_id}"


class EventBannerUploadChunk(models.Model):
    """Temporary chunk storage for multi-request event banner uploads.

    Allows ``dtc events upload-banner`` and ``dtc events create`` to send 1000x1000
    banners in small chunks (<8 KB per request) across multi-worker Gunicorn processes behind
    edge WAF body-size limits, then assemble into ``EventOperatorConfig.banner_image``.
    """

    upload_id = models.UUIDField(db_index=True)
    #: Null while the picture is staged for an event that is about to be
    #: created (``events.admin_services.stage_event_picture_chunk``).
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="banner_upload_chunks",
        null=True,
        blank=True,
    )
    chunk_index = models.PositiveIntegerField()
    chunk_count = models.PositiveIntegerField()
    content_type = models.CharField(max_length=64)
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("upload_id", "chunk_index"),
                name="event_banner_chunk_upload_index_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("event", "upload_id")),
        ]

    def __str__(self) -> str:
        return f"banner-chunk:{self.upload_id}:{self.chunk_index}/{self.chunk_count}"
