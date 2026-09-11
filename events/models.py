"""Event identity, Q&A, and historical registration provenance.

The ``Historical*`` section below is aggregate-only: no model there stores a
registration or attendee identity, and provider event identifiers are protected
data that must only be presented masked, through the historical registration
import capability.

The ``EventRegistrant*`` section at the bottom of this file is the one place in
this module that does store a per-person registration fact -- see its own
docstrings for the identity-consolidation contract and why it is deliberately
admin-only with no Studio surface in this first pass.
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

from core.models import RevisionedModel
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
        from .qna.services import ensure_event_qna

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


class EventQnaSession(models.Model):
    """The website-owned Q&A session attached to exactly one Event.

    The public Q&A behavior is adapted from DataQnA ``7704f99``.  The Event owns
    exactly one session; questions, votes, and co-host grants are separate rows
    so their privacy and idempotency invariants can be enforced transactionally.
    """

    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        ARCHIVED = "archived", "Archived"

    class AnsweredPlacement(models.TextChoices):
        SEPARATE = "separate", "Separate"
        BOTTOM = "bottom", "Bottom"
        INLINE = "inline", "Inline"

    class DefaultSort(models.TextChoices):
        POPULAR = "popular", "Popular"
        RECENT = "recent", "Recent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        related_name="qna_session",
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    listed = models.BooleanField(default=True)
    allow_names = models.BooleanField(default=True)
    require_names = models.BooleanField(default=False)
    answered_placement = models.CharField(
        max_length=16,
        choices=AnsweredPlacement.choices,
        default=AnsweredPlacement.SEPARATE,
    )
    default_sort = models.CharField(
        max_length=16,
        choices=DefaultSort.choices,
        default=DefaultSort.POPULAR,
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    retention_days = models.PositiveIntegerField(null=True, blank=True, default=365)
    state_changed_at = models.DateTimeField(default=timezone.now)
    archive_delete_at = models.DateTimeField(null=True, blank=True)
    q_total = models.PositiveBigIntegerField(default=0)
    q_answered = models.PositiveBigIntegerField(default=0)
    backend_key = models.CharField(max_length=32, default="native")
    backend_reference = models.CharField(max_length=255, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    provisioning_job = models.OneToOneField(
        "cb_jobs.JobIntent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qna_provisioning_session",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("event_id", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="events_qna_session_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(backend_key__gt=""),
                name="events_qna_session_backend_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(q_answered__lte=F("q_total")),
                name="events_qna_answered_lte_total",
            ),
            models.CheckConstraint(
                condition=Q(retention_days__isnull=True) | Q(retention_days__gte=1),
                name="events_qna_retention_positive",
            ),
        ]
        permissions = (
            ("view_event_qna", "Can view event-linked Q&A"),
            ("manage_event_qna", "Can manage event-linked Q&A"),
        )

    def __str__(self) -> str:
        return f"qna:{self.event_id}"


class EventQnaQuestion(models.Model):
    """A single opaque-ID question; participant identity is never serialized."""

    class Status(models.TextChoices):
        VISIBLE = "visible", "Visible"
        ANSWERED = "answered", "Answered"
        DELETED = "deleted", "Deleted"

    question_id = models.CharField(max_length=26, primary_key=True, editable=False)
    session = models.ForeignKey(
        EventQnaSession,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    text = models.CharField(max_length=315)
    author_name = models.CharField(max_length=60, blank=True, default="")
    participant_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.VISIBLE)
    score = models.PositiveIntegerField(default=0)
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-pinned", "-score", "created_at", "question_id")
        constraints = [
            models.CheckConstraint(
                condition=Q(text__gt="") & Q(participant_digest__gt=""),
                name="events_qna_question_content_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0),
                name="events_qna_question_score_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=("session", "status", "created_at"),
                name="events_qna_q_status_time",
            ),
            models.Index(fields=("session", "pinned", "score"), name="events_qna_q_rank"),
        ]

    def __str__(self) -> str:
        return f"qna-question:{self.question_id}"


class EventQnaVote(models.Model):
    """One idempotent participant vote for one question."""

    question = models.ForeignKey(
        EventQnaQuestion,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    participant_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("question", "participant_digest"),
                name="events_qna_vote_participant_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"qna-vote:{self.question_id}"


class EventQnaCohostInvite(models.Model):
    """A revocable link name plus a separately delivered passcode digest."""

    invite_id = models.CharField(max_length=26, primary_key=True, editable=False)
    session = models.ForeignKey(
        EventQnaSession,
        on_delete=models.CASCADE,
        related_name="cohost_invites",
    )
    name = models.SlugField(max_length=48)
    passcode_digest = models.CharField(max_length=128)
    created_by_ref = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("name", "invite_id")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "name"),
                name="events_qna_cohost_name_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"qna-cohost:{self.name}"


class EventQnaRateLimit(models.Model):
    """Database-backed fixed-window admission for anonymous Q&A actions."""

    scope_digest = models.CharField(max_length=64)
    window_seconds = models.PositiveIntegerField()
    window_started_at = models.DateTimeField()
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("scope_digest", "window_seconds", "window_started_at"),
                name="events_qna_rate_window_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("scope_digest", "window_seconds"), name="events_qna_rate_scope"),
        ]

    def __str__(self) -> str:
        return f"qna-rate:{self.scope_digest[:12]}"


class HistoricalRegistrationSourceRun(RevisionedModel):
    class Provider(models.TextChoices):
        LUMA = "luma", "Luma"
        EVENTBRITE = "eventbrite", "Eventbrite"

    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        ROLLED_BACK = "rolled_back", "Rolled back"
        QUARANTINED = "quarantined", "Quarantined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=16, choices=Provider.choices)
    adapter_version = models.CharField(max_length=32)
    schema_version = models.CharField(max_length=64)
    whole_source_checksum = models.CharField(max_length=64)
    source_reference_digest = models.CharField(max_length=64)
    manifest_entry_total = models.PositiveIntegerField()
    manifest_event_total = models.PositiveIntegerField()
    parsed_row_total = models.PositiveBigIntegerField()
    eligible_row_total = models.PositiveBigIntegerField()
    excluded_row_total = models.PositiveBigIntegerField()
    quarantined_event_total = models.PositiveIntegerField(default=0)
    status_totals = models.JSONField(default=dict)
    state_totals = models.JSONField(default=dict)
    reason_codes = models.JSONField(default=list)
    mapping_set_revision = models.PositiveBigIntegerField()
    policy_version = models.CharField(max_length=32)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="historical_registration_source_runs",
    )
    actor_ref = models.CharField(max_length=128, blank=True)
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _IMMUTABLE_FIELDS = (
        "provider",
        "adapter_version",
        "schema_version",
        "whole_source_checksum",
        "source_reference_digest",
        "manifest_entry_total",
        "manifest_event_total",
        "parsed_row_total",
        "eligible_row_total",
        "excluded_row_total",
        "quarantined_event_total",
        "status_totals",
        "state_totals",
        "reason_codes",
        "mapping_set_revision",
        "policy_version",
    )

    class Meta:
        ordering = ("-created_at", "-id")
        permissions = (
            (
                "historical_registration_import_manage",
                "Can manage historical registration aggregate imports",
            ),
            (
                "historical_registration_mapping_manage",
                "Can manage historical event mappings",
            ),
        )
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "provider",
                    "whole_source_checksum",
                    "schema_version",
                    "mapping_set_revision",
                    "policy_version",
                ),
                name="events_hist_run_replay_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_run_revision_positive"
            ),
            models.CheckConstraint(
                condition=Q(eligible_row_total__lte=F("parsed_row_total")),
                name="events_hist_run_eligible_bounded",
            ),
            models.CheckConstraint(
                condition=Q(excluded_row_total__lte=F("parsed_row_total")),
                name="events_hist_run_excluded_bounded",
            ),
        ]
        indexes = [
            models.Index(fields=("provider", "state", "-created_at"), name="events_hist_run_state"),
            models.Index(fields=("whole_source_checksum",), name="events_hist_run_checksum"),
        ]

    def __str__(self) -> str:
        return f"historical-run:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("source-run aggregate provenance is immutable")
        super().save(*args, **kwargs)


class HistoricalRegistrationAggregateRevision(RevisionedModel):
    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        QUARANTINED = "quarantined", "Quarantined"
        ROLLED_BACK = "rolled_back", "Rolled back"

    class CombinationPolicy(models.TextChoices):
        ADDITIVE_DISJOINT = "additive_disjoint", "Additive, disjoint"
        REPLACEMENT = "replacement", "Replacement"
        EXCLUDE = "exclude", "Exclude"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_run = models.ForeignKey(
        HistoricalRegistrationSourceRun,
        on_delete=models.PROTECT,
        related_name="aggregate_revisions",
    )
    # The provider's own event identifier -- protected data, only ever presented
    # masked (see events.services._mask_identifier).  Identifies which of the
    # source run's candidates this row is, the same role the removed
    # HistoricalEventMapping row used to play.
    external_event_identifier = models.CharField(max_length=512)
    # Null until this provider event is resolved to a canonical Event: either
    # automatically (exact case/whitespace-normalized title, unique date match)
    # or by a human naming the exact pair in the current-registration-input
    # JSON file.  Settable exactly once, from null -- never retargeted once
    # resolved (see save() below).  Resolving this is a distinct, separate
    # concern from activating the row's count for public display (`state`
    # below): a resolved aggregate can still be `staged`.
    event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="historical_registration_aggregate_revisions",
    )
    eligible_count = models.PositiveBigIntegerField()
    excluded_count = models.PositiveBigIntegerField(default=0)
    quarantined_count = models.PositiveBigIntegerField(default=0)
    coverage_boundary = models.CharField(max_length=128)
    status_policy_version = models.CharField(max_length=32)
    combination_policy = models.CharField(max_length=24, choices=CombinationPolicy.choices)
    aggregate_checksum = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _IMMUTABLE_FIELDS = (
        "source_run_id",
        "external_event_identifier",
        "eligible_count",
        "excluded_count",
        "quarantined_count",
        "coverage_boundary",
        "status_policy_version",
        "combination_policy",
        "aggregate_checksum",
    )

    class Meta:
        ordering = ("source_run_id", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_run", "external_event_identifier", "aggregate_checksum"),
                name="events_hist_aggregate_revision_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_aggregate_revision_positive"
            ),
            models.CheckConstraint(
                condition=Q(external_event_identifier__gt=""),
                name="events_hist_aggregate_external_id_nonempty",
            ),
        ]
        indexes = [
            models.Index(fields=("event", "state", "-created_at"), name="events_hist_agg_state"),
            models.Index(fields=("aggregate_checksum",), name="events_hist_agg_checksum"),
            models.Index(
                fields=("source_run", "external_event_identifier"),
                name="events_hist_agg_run_ext_id",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(*self._IMMUTABLE_FIELDS, "event_id")
                .first()
            )
            if original is not None:
                if any(original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS):
                    raise ValueError("aggregate revision provenance and counts are immutable")
                if original["event_id"] is not None and original["event_id"] != self.event_id:
                    raise ValueError("aggregate revision event resolution is immutable once set")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"historical-aggregate:{self.id}"


class HistoricalRegistrationAggregateSlot(RevisionedModel):
    """The one active aggregate pointer for an event/provider/coverage slot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_repository = models.CharField(max_length=255)
    canonical_revision = models.CharField(max_length=64)
    canonical_source_key = models.CharField(max_length=512)
    canonical_slug_snapshot = models.SlugField(max_length=255)
    provider = models.CharField(
        max_length=16, choices=HistoricalRegistrationSourceRun.Provider.choices
    )
    coverage_boundary = models.CharField(max_length=128)
    active_revision = models.OneToOneField(
        HistoricalRegistrationAggregateRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="active_slot",
    )
    replacement_revision_id = models.UUIDField(null=True, blank=True)
    replacement_eligible_count = models.PositiveBigIntegerField(null=True, blank=True)
    replacement_combination_policy = models.CharField(
        max_length=24,
        choices=HistoricalRegistrationAggregateRevision.CombinationPolicy.choices,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("canonical_source_key", "provider", "coverage_boundary")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "canonical_repository",
                    "canonical_revision",
                    "canonical_source_key",
                    "provider",
                    "coverage_boundary",
                ),
                name="events_hist_active_slot_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_slot_revision_positive"
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        active_revision__isnull=False,
                        replacement_revision_id__isnull=True,
                        replacement_eligible_count__isnull=True,
                        replacement_combination_policy="",
                    )
                    | Q(
                        active_revision__isnull=True,
                        replacement_revision_id__isnull=False,
                        replacement_eligible_count__isnull=False,
                        replacement_combination_policy__gt="",
                    )
                    | Q(
                        active_revision__isnull=True,
                        replacement_revision_id__isnull=True,
                        replacement_eligible_count__isnull=True,
                        replacement_combination_policy="",
                    )
                ),
                name="events_hist_slot_one_pointer",
            ),
        ]
        indexes = [
            models.Index(
                fields=("canonical_repository", "canonical_revision", "canonical_source_key"),
                name="events_hist_slot_source",
            )
        ]

    def __str__(self) -> str:
        return f"historical-slot:{self.id}"


class HistoricalRegistrationPointerDisplacement(models.Model):
    """Immutable snapshot of one pointer displaced by an aggregate replacement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    replacing_revision = models.ForeignKey(
        HistoricalRegistrationAggregateRevision,
        on_delete=models.PROTECT,
        related_name="pointer_displacements",
    )
    slot = models.ForeignKey(
        HistoricalRegistrationAggregateSlot,
        on_delete=models.PROTECT,
        related_name="displacement_history",
    )
    displaced_revision = models.ForeignKey(
        HistoricalRegistrationAggregateRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="displaced_by_replacements",
    )
    row_replacement_revision_id = models.UUIDField(null=True, blank=True)
    row_replacement_eligible_count = models.PositiveBigIntegerField(null=True, blank=True)
    row_replacement_combination_policy = models.CharField(
        max_length=24,
        choices=HistoricalRegistrationAggregateRevision.CombinationPolicy.choices,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    _IMMUTABLE_FIELDS = (
        "replacing_revision_id",
        "slot_id",
        "displaced_revision_id",
        "row_replacement_revision_id",
        "row_replacement_eligible_count",
        "row_replacement_combination_policy",
    )

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("replacing_revision", "slot"),
                name="events_hist_displacement_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        displaced_revision__isnull=False,
                        row_replacement_revision_id__isnull=True,
                        row_replacement_eligible_count__isnull=True,
                        row_replacement_combination_policy="",
                    )
                    | Q(
                        displaced_revision__isnull=True,
                        row_replacement_revision_id__isnull=False,
                        row_replacement_eligible_count__isnull=False,
                        row_replacement_combination_policy__gt="",
                    )
                ),
                name="events_hist_displacement_one_pointer",
            ),
        ]

    def __str__(self) -> str:
        return f"historical-displacement:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("historical pointer displacement is immutable")
        super().save(*args, **kwargs)


class HistoricalRegistrationTotalState(RevisionedModel):
    """One public representation revision and completeness gate per canonical event."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_repository = models.CharField(max_length=255)
    canonical_revision = models.CharField(max_length=64)
    canonical_source_key = models.CharField(max_length=512)
    canonical_slug_snapshot = models.SlugField(max_length=255)
    complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("canonical_repository", "canonical_revision", "canonical_source_key"),
                name="events_hist_total_source_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_total_revision_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"historical-total:{self.id}"


# ---------------------------------------------------------------------------
# Event registrants (attendee-level) -- see events/registrant_import.py for the
# matching service and scripts/prod/import_event_registrants.py for the entry
# point.  Three things, not two: an identity (the consolidated real person), a
# fact (one provider registration, pointing at that identity, always naming a
# specific event), and -- below, EventRegistrantInterestSignal -- a broader,
# non-event-specific signal sourced from Mailchimp's own event-category tags.
# See events/mailchimp_tag_import.py and
# scripts/prod/import_mailchimp_event_tags.py for that importer.
# ---------------------------------------------------------------------------


class EventRegistrantIdentity(models.Model):
    """The consolidated real person behind one or more provider event registrations.

    Matching happens in :mod:`events.registrant_import`, by ``normalized_email``,
    against ``accounts_customuser`` first -- the same table and field
    ``accounts.services.cmp_learner_import`` already uses for its own
    cross-source deduplication (see ``_find_cross_source_match`` there). When
    that lookup finds an account, ``account`` is set here and this row is a
    pointer onto it, never a competing profile -- this is the case the owner
    was explicit about: someone who both took a course and registered for an
    event must resolve to that one account, never two. When it finds nothing,
    and no prior registrant-only identity already claims the address, a new
    row is created with ``normalized_email`` set and ``account`` left null --
    a real identity in the same email-keyed space, but deliberately never a
    login-capable ``CustomUser`` row (self-registration is closed, see
    ``accounts.models.CustomUser`` / commit ``c237ef2``). A future import that
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
                name="events_registrant_identity_email_unique_unmatched",
            ),
            models.CheckConstraint(
                condition=(
                    Q(account__isnull=False, normalized_email__isnull=True)
                    | Q(account__isnull=True, normalized_email__isnull=False)
                ),
                name="events_registrant_identity_exactly_one_anchor",
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
    ``events.registrant_import``. One event's rows are read and written
    inside a single transaction, gated on that event's progress row not
    already being ``completed``; a killed run leaves nothing partially
    written for a later run to duplicate against, so there is nothing this
    table itself needs to deduplicate on, and no reason to keep a protected
    per-attendee token around permanently to do it with.

    The consequence, and it is the reason a refresh works the way it does: a
    newer export of an event we already hold cannot be merged row by row,
    because there is no key to merge on.  ``events.registrant_import``'s
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
        Event, on_delete=models.PROTECT, related_name="registrant_registrations"
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
            models.Index(fields=("identity",), name="events_registration_identity"),
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
                name="events_registrant_interest_signal_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"event-registrant-interest-signal:{self.id}"


class EventRegistrantImportProgress(models.Model):
    """Per-(provider, event) completion marker for the resumable registrant import.

    Unlike ``accounts.models.CmpLearnerImportProgress`` (one monotonic source
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
    which events a newer export makes stale.  ``events.registrant_import``'s
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
                name="events_registrant_import_progress_provider_external_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"registrant-import-progress:{self.provider}:{self.external_event_identifier}"
