"""Whether an event is upcoming, in progress, or past -- derived, never stored.

The shared ``community_base.events.Event`` row stores a ``status`` column with
the values ``draft``, ``upcoming``, ``completed``, ``cancelled`` and
``archived``.  Only part of that is editorial:

* ``draft``, ``cancelled`` and ``archived`` are decisions a person makes, and
  they stay stored.
* ``upcoming`` and ``completed`` both mean "published": the event is scheduled
  and may be shown.  Whether it has *happened* follows from its start and end
  time, so this site reads the two stored values as one editorial state and
  never trusts either to say where the event sits in time.  A stored
  ``upcoming`` on a 2020 event is therefore not wrong, only uninformative.

Every site reader that needs "upcoming vs past" calls this module instead of
comparing ``Event.status`` (or the package's status-gated ``is_upcoming`` /
``is_past`` properties) itself.  Site writers store :data:`PUBLISHED_STATUS`
for a published event and never write ``completed``.

The time boundaries, shared by every reader:

* **upcoming** -- the start is now or later (``start >= now``).  This is the
  split the public events index and detail pages already draw.
* **in progress** -- the start has passed but the event has not ended.
* **past** -- the effective end has passed: the stored end, or one hour after
  the start when no end is stored (the package's ``effective_end_datetime``).

Registration stays open while an event is upcoming or in progress.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from django.db.models import Q
from django.utils import timezone

#: The stored value a site writer uses for a published, scheduled event.
PUBLISHED_STATUS: Final = "upcoming"

#: Stored values that mean "published".  ``completed`` is a legacy synonym the
#: site no longer writes; it carries no time meaning here.
PUBLISHED_STATUSES: Final = frozenset({"upcoming", "completed"})

DRAFT: Final = "draft"
CANCELLED: Final = "cancelled"
ARCHIVED: Final = "archived"

#: Editorial states that are stored and reported as they are.
EDITORIAL_STATUSES: Final = frozenset({DRAFT, CANCELLED, ARCHIVED})

UPCOMING: Final = "upcoming"
IN_PROGRESS: Final = "in_progress"
PAST: Final = "past"

#: Time-derived states of a published event.
TIME_STATUSES: Final = (UPCOMING, IN_PROGRESS, PAST)

#: Every value :func:`derived_status` can return.
DERIVED_STATUSES: Final = (DRAFT, UPCOMING, IN_PROGRESS, PAST, CANCELLED, ARCHIVED)

#: How long an event without a stored end is taken to last (the package's rule).
DEFAULT_DURATION: Final = timedelta(hours=1)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else timezone.now()


def effective_end(start: datetime, end: datetime | None) -> datetime:
    """The stored end, or :data:`DEFAULT_DURATION` after the start."""

    return end if end is not None else start + DEFAULT_DURATION


def has_started(start: datetime, now: datetime | None = None) -> bool:
    """True once the start has passed; at the exact start it is still upcoming."""

    return start < _now(now)


def has_ended(start: datetime, end: datetime | None, now: datetime | None = None) -> bool:
    """True once the effective end has been reached."""

    return _now(now) >= effective_end(start, end)


def time_status(start: datetime, end: datetime | None, now: datetime | None = None) -> str:
    """``upcoming``, ``in_progress`` or ``past`` for a schedule, against now."""

    current = _now(now)
    if not has_started(start, current):
        return UPCOMING
    if not has_ended(start, end, current):
        return IN_PROGRESS
    return PAST


def is_published(event: Any) -> bool:
    """True when the stored status is a published one (not draft/cancelled/archived)."""

    return event.status in PUBLISHED_STATUSES


def derived_status(event: Any, now: datetime | None = None) -> str:
    """The one status a reader should show for an event.

    Editorial states (``draft``, ``cancelled``, ``archived``) pass through; a
    published event reports its time-derived state.  An unknown stored value
    is reported as it is rather than guessed at.
    """

    if is_published(event):
        return time_status(event.start_datetime, event.end_datetime, now)
    return str(event.status)


def accepts_registrations(event: Any, now: datetime | None = None) -> bool:
    """A published event takes registrations until it has ended."""

    return is_published(event) and not has_ended(event.start_datetime, event.end_datetime, now)


def status_q(status: str, now: datetime | None = None) -> Q:
    """A queryset filter selecting events whose :func:`derived_status` is ``status``.

    Raises ``ValueError`` for a value :func:`derived_status` never returns.
    """

    if status in EDITORIAL_STATUSES:
        return Q(status=status)
    if status not in TIME_STATUSES:
        raise ValueError(f"unknown event status {status!r}")
    current = _now(now)
    published = Q(status__in=PUBLISHED_STATUSES)
    # The effective end is ``end`` when stored and ``start + 1h`` otherwise; the
    # null case is written against the start so no annotation is needed.
    ended = Q(end_datetime__isnull=False, end_datetime__lte=current) | Q(
        end_datetime__isnull=True, start_datetime__lte=current - DEFAULT_DURATION
    )
    not_ended = Q(end_datetime__isnull=False, end_datetime__gt=current) | Q(
        end_datetime__isnull=True, start_datetime__gt=current - DEFAULT_DURATION
    )
    if status == UPCOMING:
        return published & Q(start_datetime__gte=current)
    if status == IN_PROGRESS:
        return published & Q(start_datetime__lt=current) & not_ended
    return published & ended
