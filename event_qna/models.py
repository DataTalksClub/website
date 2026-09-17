"""Event-linked Q&A models.

These models moved verbatim out of the ``events`` app (#412) so their rows
survive the P5 rebuild that swaps the ``events`` label to
``community_base.events``.  The session points at ``events.Event`` -- the
identity row that label names both before and after the swap -- by a database
integer key from the swap onward; until then it is the site event's UUID.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import F, Q
from django.utils import timezone


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
        "events.Event",
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
                name="event_qna_session_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(backend_key__gt=""),
                name="event_qna_session_backend_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(q_answered__lte=F("q_total")),
                name="event_qna_answered_lte_total",
            ),
            models.CheckConstraint(
                condition=Q(retention_days__isnull=True) | Q(retention_days__gte=1),
                name="event_qna_retention_positive",
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
                name="event_qna_question_content_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0),
                name="event_qna_question_score_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=("session", "status", "created_at"),
                name="event_qna_q_status_time",
            ),
            models.Index(fields=("session", "pinned", "score"), name="event_qna_q_rank"),
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
                name="event_qna_vote_participant_unique",
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
                name="event_qna_cohost_name_unique",
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
                name="event_qna_rate_window_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("scope_digest", "window_seconds"), name="event_qna_rate_scope"),
        ]

    def __str__(self) -> str:
        return f"qna-rate:{self.scope_digest[:12]}"
