"""Event identity and attendee-level registration facts.

The historical registration aggregates now live in the
``historical_registrations`` app and the event-linked Q&A in ``event_qna``
(#412); this module keeps only the Event identity and the ``EventRegistrant*``
per-person registration facts -- see their own docstrings for the
identity-consolidation contract.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Collection
from time import sleep
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import OperationalError, models, transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from core.runtime_config import get_str_setting

from .slugs import event_title_slug

MAX_PUBLIC_ID = 2_147_483_647
_PUBLIC_ID_ALLOCATION_ATTEMPTS = 5


class EventIdentityError(ValueError):
    """A bounded manifest or exact-identity failure."""


class EventIdentityNotFound(LookupError):
    """An unknown UUID, public ID, or source identity was requested."""


class EventPublicIdSequence(models.Model):
    """The durable, never-decremented allocator for public Event route IDs."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    next_public_id = models.PositiveIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(id=1),
                name="events_public_id_sequence_singleton",
            ),
            models.CheckConstraint(
                condition=Q(next_public_id__gt=0),
                name="events_public_id_sequence_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"Next public Event ID: {self.next_public_id}"


class Event(models.Model):
    """The database-owned identity for one public event.

    The identity row is also the ownership boundary for event-linked products.  Public
    projections remain a separate read model, but lifecycle is kept here so an Event-owned
    Q&A cannot accidentally outlive the Event's public visibility policy.
    """

    _allow_public_id_assignment: bool = False

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # UUID remains the immutable internal identity.  This separate sequence is the
    # human-facing public identifier used in event URLs.
    public_id = models.PositiveIntegerField(null=True, unique=True, editable=False, db_index=True)
    title = models.CharField(max_length=1_000)
    slug = models.SlugField(max_length=255, db_index=True)
    source_repository = models.CharField(max_length=255)
    source_revision = models.CharField(max_length=64)
    source_key = models.CharField(max_length=512)
    source_path = models.CharField(max_length=512, default="")
    source_checksum = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Lifecycle(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        ARCHIVED = "archived", "Archived"

    lifecycle = models.CharField(
        max_length=16,
        choices=Lifecycle.choices,
        default=Lifecycle.PUBLISHED,
        db_index=True,
    )

    class Meta:
        ordering = ("source_key", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_repository", "source_revision", "source_key"),
                name="events_event_source_identity_unique",
            ),
            models.CheckConstraint(condition=Q(title__gt=""), name="events_event_title_nonempty"),
            models.CheckConstraint(
                condition=Q(public_id__isnull=True) | Q(public_id__gt=0),
                name="events_event_public_id_positive",
            ),
            models.CheckConstraint(
                condition=Q(source_repository__gt="")
                & Q(source_revision__gt="")
                & Q(source_key__gt=""),
                name="events_event_source_identity_nonempty",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.title:
            raise ValidationError({"title": "Event title is required."})
        original_id = getattr(self, "_identity_original_id", self.id)
        if not self._state.adding and original_id != self.id:
            raise ValueError("event identity cannot be reassigned")
        original = None
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values("slug", "public_id").first()
        expected_slug = event_title_slug(self.title)
        if self._state.adding and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("event identity already exists")
        # A stale or blank slug is a cosmetic snapshot, never a second identity.
        self.slug = expected_slug

        if not self._state.adding and not type(self).objects.filter(pk=self.pk).exists():
            # Django otherwise treats a loaded object whose primary key was reassigned as a
            # new insert.  Make identity reassignment explicit and fail closed.
            raise ValueError("event identity cannot be reassigned")
        if self._state.adding and (
            self.public_id is None or not getattr(self, "_allow_public_id_assignment", False)
        ):
            raise ValueError("event public ID must be allocated by the identity service")
        if original is not None and original["public_id"] != self.public_id:
            raise ValueError("event public ID is immutable")
        original_slug = original["slug"] if original is not None else None
        slug_changed = original_slug is not None and original_slug != expected_slug
        update_fields = kwargs.get("update_fields")
        if slug_changed and update_fields is not None and "slug" not in update_fields:
            kwargs["update_fields"] = (*update_fields, "slug")
        with transaction.atomic():
            super().save(*args, **kwargs)
            self._identity_original_id = self.id

    def clean(self) -> None:
        super().clean()
        try:
            expected_slug = event_title_slug(self.title)
        except ValueError as exc:
            raise ValidationError({"title": str(exc)}) from exc
        if self.slug and self.slug != expected_slug:
            raise ValidationError({"slug": "Event slug is generated from title."})
        self.slug = expected_slug

    @classmethod
    def from_db(
        cls,
        db: str | None,
        field_names: Collection[str],
        values: Collection[Any],
    ) -> Event:
        """Remember the persisted primary key so an in-memory reassignment cannot retarget a row."""

        instance = super().from_db(db, field_names, values)
        instance._identity_original_id = instance.id
        return instance


# --------------------------------------------------------------------------
# Identity: allocation, lookup, and canonical-path building
# --------------------------------------------------------------------------
#
# Plain functions beside the model they operate on, the same convention
# ``events.queries``, ``events.services`` and ``courses.services.course_family_identity``
# already use elsewhere in this codebase -- not a separate "identity" module.  What
# stays here is exactly what a live route, Studio, or the admin API resolves an Event
# by (UUID/public-ID/source-identity lookup, and the canonical path/URL builders); the
# reviewed manifest's one-time import machinery and provider-discovery's identity
# minting are pure ingestion, and live in ``scripts/prod`` (``identity_manifest.py``,
# ``registrant_import.py``) instead.


def ensure_public_id_sequence() -> int:
    """Park the singleton allocator above every public ID that already exists.

    The allocator is a one-row table, so something has to put the row there and
    keep it ahead of the rows an import wrote.  That belongs with the code that
    writes events, not in a migration: a migration runs once at a fixed point in
    the schema history, and on an empty database that point is *before* the
    manifest import, which would leave the allocator handing out an ID the
    import had already used.

    Returns the ``next_public_id`` the allocator will hand out.
    """

    latest = Event.objects.aggregate(value=Max("public_id"))["value"] or 0
    row, created = EventPublicIdSequence.objects.get_or_create(
        pk=1,
        defaults={"next_public_id": latest + 1},
    )
    if not created and row.next_public_id <= latest:
        EventPublicIdSequence.objects.filter(pk=1, next_public_id=row.next_public_id).update(
            next_public_id=latest + 1,
            updated_at=timezone.now(),
        )
        return latest + 1
    return row.next_public_id


def _allocate_public_id() -> int:
    ensure_public_id_sequence()
    while True:
        public_id = EventPublicIdSequence.objects.values_list("next_public_id", flat=True).get(pk=1)
        latest = Event.objects.aggregate(value=Max("public_id"))["value"] or 0
        if (
            public_id >= MAX_PUBLIC_ID
            or public_id <= latest
            or Event.objects.filter(public_id=public_id).exists()
        ):
            raise EventIdentityError("event_public_id_allocator_invalid")
        claimed = EventPublicIdSequence.objects.filter(
            pk=1,
            next_public_id=public_id,
        ).update(next_public_id=public_id + 1, updated_at=timezone.now())
        if claimed == 1:
            return public_id


def insert_event_with_public_id(*, public_id: int, **values: Any) -> Event:
    event = Event(public_id=public_id, **values)
    event._allow_public_id_assignment = True
    event.save(force_insert=True)
    return event


def _create_event_identity_atomic(
    *,
    title: str,
    source_repository: str,
    source_revision: str,
    source_key: str,
    source_path: str = "",
    source_checksum: str = "",
    event_id: uuid.UUID | None = None,
) -> Event:
    with transaction.atomic():
        event = insert_event_with_public_id(
            id=event_id or uuid.uuid4(),
            public_id=_allocate_public_id(),
            title=title,
            source_repository=source_repository,
            source_revision=source_revision,
            source_key=source_key,
            source_path=source_path,
            source_checksum=source_checksum,
        )
        # Event creation owns Q&A provisioning.  Keep the import local so this
        # module does not import the Q&A implementation at module load time.
        from event_qna.services import ensure_event_qna

        ensure_event_qna(event.id)
        return event


def create_event_identity(
    *,
    title: str,
    source_repository: str,
    source_revision: str,
    source_key: str,
    source_path: str = "",
    source_checksum: str = "",
    event_id: uuid.UUID | None = None,
) -> Event:
    """Create one Event with a portable, never-reused public route identifier."""

    for attempt in range(_PUBLIC_ID_ALLOCATION_ATTEMPTS):
        try:
            return _create_event_identity_atomic(
                title=title,
                source_repository=source_repository,
                source_revision=source_revision,
                source_key=source_key,
                source_path=source_path,
                source_checksum=source_checksum,
                event_id=event_id,
            )
        except OperationalError:
            if attempt == _PUBLIC_ID_ALLOCATION_ATTEMPTS - 1:
                raise
            sleep(0.01 * (2**attempt))
    raise AssertionError("public ID allocation retry loop exhausted without returning")


# The three helpers below back both a live path (``events.services``, imported
# transitively from ``content.public_views`` through ``public_registration_total``)
# and an ingestion one (``scripts.prod.registrant_import.ExistingEventIndex``).
_PROVIDER_EVENT_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_CANONICAL_SOURCE_KEY_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


def normalize_event_title(title: str) -> str:
    """Collapse whitespace and fold case so two titles compare exactly."""

    return " ".join(title.split()).casefold()


def canonical_event_date(source_key: str) -> str | None:
    """The event's date, read from its canonical ``YYYY-MM-DD-slug`` source key.

    Some events have no date component in their source key at all: the older
    podcast-style legacy entries, and every event minted from a provider export
    (whose source key is the provider's own opaque event id).  Those never enter
    a duplicate-creation guard built from this, so a provider event can neither
    match one nor match a duplicate a previous buggy run created -- replay
    idempotency stays the source identity's job, not that guard's.
    """

    match = _CANONICAL_SOURCE_KEY_DATE.match(source_key)
    return match.group(1) if match else None


def provider_event_date(start_at: str) -> str | None:
    """The calendar date a provider export's start timestamp names, if any."""

    match = _PROVIDER_EVENT_DATE.match(start_at)
    return match.group(1) if match else None


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        if value.variant != uuid.RFC_4122:
            raise EventIdentityNotFound("unknown_event")
        return value
    if not isinstance(value, str):
        raise EventIdentityNotFound("unknown_event")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise EventIdentityNotFound("unknown_event") from exc
    if str(parsed) != value or parsed.variant != uuid.RFC_4122:
        raise EventIdentityNotFound("unknown_event")
    return parsed


def resolve_uuid(event_id: uuid.UUID | str) -> Event:
    parsed = _coerce_uuid(event_id)
    try:
        return Event.objects.get(pk=parsed)
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("unknown_event") from exc


def resolve_public_id(public_id: int | str) -> Event:
    """Resolve the stable numeric identifier used only by public event routes."""

    if isinstance(public_id, bool):
        raise EventIdentityNotFound("unknown_event")
    if isinstance(public_id, int):
        parsed = public_id
    elif isinstance(public_id, str) and re.fullmatch(r"[1-9][0-9]*", public_id):
        parsed = int(public_id)
    else:
        raise EventIdentityNotFound("unknown_event")
    if parsed < 1 or parsed > MAX_PUBLIC_ID:
        raise EventIdentityNotFound("unknown_event")
    try:
        return Event.objects.get(public_id=parsed)
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("unknown_event") from exc


def resolve_source_identity(*, repository: str, revision: str, source_key: str) -> Event:
    try:
        return Event.objects.get(
            source_repository=repository,
            source_revision=revision,
            source_key=source_key,
        )
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("source_identity_unmapped") from exc
    except Event.MultipleObjectsReturned as exc:
        raise EventIdentityError("source_identity_ambiguous") from exc


def current_slug(event_id: uuid.UUID | str) -> str:
    return resolve_uuid(event_id).slug


def canonical_detail_path(event_id: uuid.UUID | str) -> str:
    event = resolve_uuid(event_id)
    if event.public_id is None:
        raise EventIdentityNotFound("event_public_id_unavailable")
    return f"/events/{event.public_id}/{event.slug}"


def canonical_detail_url(event_id: uuid.UUID | str) -> str:
    return f"{get_str_setting('site.origin.canonical')}{canonical_detail_path(event_id)}"


def canonical_registration_path(event_id: uuid.UUID | str) -> str:
    return f"{canonical_detail_path(event_id)}/register"


def redirect_for_supplied_slug(event_id: uuid.UUID | str, supplied_slug: str) -> str | None:
    event = resolve_uuid(event_id)
    return None if supplied_slug == event.slug else canonical_detail_path(event.id)


def serialize_event_identity(event: Event) -> dict[str, Any]:
    """Serialize the authorized identity view without public or attendee data."""

    return {
        "id": str(event.id),
        "public_id": event.public_id,
        "public_url": canonical_detail_url(event.id),
        "title": event.title,
        "slug": event.slug,
        "canonical_path": canonical_detail_path(event.id),
        "registration_path": canonical_registration_path(event.id),
        "provenance": {
            "repository": event.source_repository,
            "revision": event.source_revision,
            "source_key": event.source_key,
            "source_path": event.source_path,
            "source_checksum": event.source_checksum,
        },
    }


def list_event_identities(*, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("event_identity_page_invalid")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise ValueError("event_identity_page_size_invalid")
    queryset = Event.objects.all()
    total = queryset.count()
    offset = (page - 1) * page_size
    return {
        "items": [
            serialize_event_identity(event) for event in queryset[offset : offset + page_size]
        ],
        "page": page,
        "page_size": page_size,
        "total_count": total,
    }


def get_event_identity(event_id: uuid.UUID | str) -> dict[str, Any]:
    return serialize_event_identity(resolve_uuid(event_id))


class EventContent(models.Model):
    """What one public event page says, as database columns.

    :class:`Event` is identity -- the UUID, the public ID, the slug the URL is
    built from -- and deliberately says nothing about the event itself. This is
    the other half: the schedule the page prints, the type it is filed under,
    the description it renders, and the season and episode a podcast recording
    carries.

    It is a separate row rather than more columns on ``Event`` because the two
    change for different reasons and at different times: identity is frozen at
    import and never re-derived, while content is re-ingested whenever upstream
    edits an event. An identity with no content row yet is a normal state -- the
    page renders what it has -- and content can be replaced without touching the
    identity the public URL depends on.
    """

    class Type(models.TextChoices):
        WEBINAR = "webinar", "Webinar"
        WORKSHOP = "workshop", "Workshop"
        PODCAST = "podcast", "Podcast"
        CONFERENCE = "conference", "Conference"

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="content")
    type = models.CharField(max_length=16, choices=Type.choices, db_index=True)
    starts_at = models.DateTimeField(db_index=True)
    # Almost no event records an end. A missing one is "not stated", which the
    # page omits, and is never filled in from a guessed duration.
    ends_at = models.DateTimeField(null=True, blank=True)
    # Podcast recordings carry a season and episode; nothing else does.
    season = models.PositiveIntegerField(null=True, blank=True)
    episode = models.PositiveIntegerField(null=True, blank=True)
    description_html = models.TextField(blank=True)
    description_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_at", "event_id")
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gte=F("starts_at")),
                name="events_event_content_ends_after_start",
            ),
            models.CheckConstraint(
                # Season and episode name one position between them, so a row
                # carrying half of it would describe an episode of no season or
                # a season with no episode.
                condition=Q(season__isnull=True, episode__isnull=True)
                | Q(season__isnull=False, episode__isnull=False),
                name="events_event_content_season_episode_together",
            ),
        ]

    def __str__(self) -> str:
        return f"content:{self.event_id}"


class EventSpeaker(models.Model):
    """One person billed on one event, in the order the page lists them."""

    content = models.ForeignKey(EventContent, on_delete=models.CASCADE, related_name="speakers")
    # The person's own key upstream. The public path is stored beside it rather
    # than derived, because whether a speaker has a person page at all is a fact
    # about the people catalogue, not something this row may assume.
    key = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    public_path = models.CharField(max_length=1024, blank=True)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("content_id", "position")
        constraints = [
            models.UniqueConstraint(
                fields=("content", "position"), name="events_event_speaker_position_unique"
            ),
            models.UniqueConstraint(
                fields=("content", "key"), name="events_event_speaker_key_unique"
            ),
            models.CheckConstraint(
                condition=Q(key__gt="") & Q(name__gt=""),
                name="events_event_speaker_identified",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class EventLink(models.Model):
    """One outbound link an event page offers, in the order it offers them."""

    content = models.ForeignKey(EventContent, on_delete=models.CASCADE, related_name="links")
    label = models.CharField(max_length=255)
    url = models.URLField(max_length=2048)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("content_id", "position")
        constraints = [
            models.UniqueConstraint(
                fields=("content", "position"), name="events_event_link_position_unique"
            ),
            models.CheckConstraint(condition=Q(label__gt=""), name="events_event_link_labelled"),
        ]

    def __str__(self) -> str:
        return f"{self.label}: {self.url}"
