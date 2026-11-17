"""Read public event records from the database.

The public event pages read one record shape no matter where the rows live.
Since #412 the rows are ``community_base.events.Event``: identity, schedule and
description are one row, speakers are ``Host`` links resolved to site profile
paths at read time, and the DTC display vocabulary (event type, podcast season
and episode) plus the ordered link list ride in the row's ``tags`` and
``materials`` JSON.  This module is the one place that turns a row into the
record shape the views and templates read.

Only published rows appear (``events.lifecycle.PUBLISHED_STATUSES``): draft,
cancelled and archived rows never do.  Whether a published event is upcoming
or past is derived from its schedule, never from the stored status.  A page
that would have to invent a start time is impossible by construction -- the
shared row cannot exist without one.  An empty database therefore lists no
events, which is a normal state and not a failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from community_base.events.models import Event, EventHost, Host
from community_base.events.models import EventRegistration as NativeRegistration
from django.db.models import (
    Count,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce

from content.models import EventSource
from event_registrants.models import EventOperatorConfig
from event_registrants.models import EventRegistration as ProviderRegistration

from .identity import (
    EventIdentityNotFound,
    decode_display_tags,
    decode_event_tags,
    host_profile_url,
)
from .lifecycle import PUBLISHED_STATUSES, is_published


def _operator_config(event: Event) -> EventOperatorConfig | None:
    try:
        return event.operator_config
    except EventOperatorConfig.DoesNotExist:
        return None


def _record(event: Event) -> dict[str, Any]:
    event_type, season, episode = decode_event_tags(event.tags)
    public_path = f"/events/{event.public_id}/{event.slug}" if event.public_id is not None else ""
    config = _operator_config(event)
    return {
        "identity_id": str(event.content_id),
        "public_id": event.public_id,
        "slug": event.slug,
        "title": event.title,
        "public_path": public_path,
        # A manifest-only row has no DTC type yet; the page treats "" as "no
        # type stated" rather than inventing one.
        "type": event_type or "",
        "tags": list(decode_display_tags(event.tags)),
        "starts_at": event.start_datetime.isoformat(),
        "ends_at": event.end_datetime.isoformat() if event.end_datetime is not None else "",
        "season": season,
        "episode": episode,
        "description_html": event.description_html,
        "description_text": event.description,
        "registration_count": event.registration_count,
        "has_uploaded_banner": bool(config is not None and config.banner_image),
        "collect_company": bool(config is not None and config.collect_company),
        "sponsor_sharing_enabled": bool(config is not None and config.sponsor_sharing_enabled),
        "sponsor_name": config.sponsor_name if config is not None else "",
        "speakers": [
            {
                "key": link.host.external_ref,
                "name": link.host.name,
                "public_path": host_profile_url(link.host),
            }
            for link in event.event_host_links.all()
        ],
        "links": [
            {"label": entry.get("label", ""), "url": entry.get("url", "")}
            for entry in (event.materials or [])
            if isinstance(entry, dict)
        ],
        # Where this event came from.  Provenance belongs to the identity, and
        # the reviewed tuple the imports resolve by lives on the site-owned
        # ``content.EventSource`` row.
        "provenance": _provenance(event),
    }


def _provenance(event: Event) -> dict[str, Any]:
    try:
        source = event.source_identity
    except EventSource.DoesNotExist:
        source = None
    return {
        "repository": source.repository if source else (event.source_repo or ""),
        "revision": source.revision if source else (event.source_commit or ""),
        "source_key": source.source_key if source else "",
        "source_path": source.source_path if source else (event.source_path or ""),
        "checksum": source.source_checksum if source else "",
    }


#: Provider registration rows a public count includes, by provider.
ELIGIBLE_PROVIDER_STATUSES = Q(provider="luma", status="approved") | Q(
    provider="eventbrite", status="attending"
)

#: Native (site-owned) registration statuses a public count includes.
#: The number means "people who registered", so a confirmed registration
#: keeps counting once the event marks it attended or no-show (marking
#: no-shows must not lower it).  ``pending_verification`` has not confirmed an
#: address yet, and cancelled and expired rows are no longer registrations.
COUNTED_NATIVE_STATUSES = ("confirmed", "attended", "no_show")


def _registration_count() -> Any:
    """The distinct people registered for the outer ``Event``, as one expression.

    Two correlated subqueries, so the listing stays one query however many
    events it holds:

    * eligible provider rows (``event_registrants.EventRegistration``), the
      Luma/Eventbrite import facts;
    * native rows (``community_base.events.EventRegistration``) written by the
      site's own register form, with a counted status (confirmed, attended,
      no-show) -- minus any native row whose person already has an eligible
      provider row for the same event, so someone registered on Luma and on
      the site counts once.  The person
      is matched by email on the provider identity (the registrant-only
      ``normalized_email``, or the linked account's identity email) or by the
      native row's account being the identity's account.
    """

    provider_rows = ProviderRegistration.objects.filter(ELIGIBLE_PROVIDER_STATUSES)
    provider_count = (
        provider_rows.filter(event_id=OuterRef("pk"))
        .order_by()
        .values("event_id")
        .annotate(total=Count("id"))
        .values("total")
    )

    # One ``Exists`` per way a provider identity can name the person, rather
    # than one ``OR`` across the joins, so each probe stays a plain indexed
    # lookup of the event's provider rows.
    same_event = provider_rows.filter(event_id=OuterRef("event_id"))
    on_provider = (
        Exists(same_event.filter(identity__normalized_email=OuterRef("normalized_email")))
        | Exists(
            same_event.filter(
                identity__account__identity__normalized_email=OuterRef("normalized_email")
            )
        )
        | Exists(same_event.filter(identity__account_id=OuterRef("user_id")))
    )
    native_count = (
        NativeRegistration.objects.filter(
            event_id=OuterRef("pk"), status__in=COUNTED_NATIVE_STATUSES
        )
        .exclude(on_provider)
        .order_by()
        .values("event_id")
        .annotate(total=Count("id"))
        .values("total")
    )
    return Coalesce(Subquery(provider_count, output_field=IntegerField()), Value(0)) + Coalesce(
        Subquery(native_count, output_field=IntegerField()), Value(0)
    )


def _published() -> Any:
    return (
        Event.objects.filter(status__in=PUBLISHED_STATUSES)
        .annotate(registration_count=_registration_count())
        .select_related("source_identity", "operator_config")
        .prefetch_related(
            Prefetch(
                "event_host_links",
                queryset=EventHost.objects.select_related("host").order_by("position", "pk"),
            )
        )
    )


def published_event_records() -> tuple[dict[str, Any], ...]:
    """Every published event, newest first, as the record the pages read.

    Within one request the records are read once: hub, detail and member-home
    surfaces all fan out through here (directly and via
    :func:`content.event_content.event_groups`), and the per-request memo
    (``core.request_cache``) collapses those repeats.  The stamp-keyed
    ``lru_cache`` below stays the cross-request authority, so a write still
    rebuilds on the next request.  Outside a request every call reads live.
    """

    from core.request_cache import memoized

    return memoized(
        "events.queries.published_event_records",
        lambda: _cached_published_records(_published_records_stamp()),
    )


def _stamp(model: Any, field: str) -> tuple[int, str]:
    """A cheap stamp that moves whenever the table's rows could have.

    One aggregate, not a row per record.  A database failure raises, so an
    outage can never enter the cache below disguised as an empty catalogue.
    """

    agg = model.objects.aggregate(total=Count("id"), latest=Max(field))
    return (int(agg["total"] or 0), str(agg["latest"] or ""))


def _published_records_stamp() -> tuple[tuple[int, str], ...]:
    """The exact cache key for :func:`published_event_records`.

    Every table a record is derived from contributes its stamp, so a warmed
    answer cannot outlive the rows it was read from:

    * ``Event``/``Host`` carry ``updated_at`` and the content import saves the
      event row (bumping it) whenever it applies new copy, a new schedule or
      new speakers, while a host row is minted once and never renamed by a
      later import.
    * ``EventHost`` link rows carry no timestamp of their own and are only
      ever rewritten by the import above, so their count and newest id join
      the event stamp rather than standing alone.
    * Registration counts are annotated from the imported provider rows
      (``event_registrants``) and the native rows the site's own register
      form writes (``community_base.events``), so both join in too.
    * Provenance is read off the site-owned ``EventSource`` row, and a
      speaker's profile path is resolved against the synced people catalogue
      at record time, so both stamps join as well.
    """

    from content.catalogue import PEOPLE_SOURCE_SLUG, synced_stamp
    from core.request_cache import memoized

    def read() -> tuple[tuple[int, str], ...]:
        return (
            _stamp(Event, "updated_at"),
            _stamp(Host, "updated_at"),
            _stamp(EventHost, "id"),
            _stamp(ProviderRegistration, "updated_at"),
            _stamp(NativeRegistration, "updated_at"),
            _stamp(EventSource, "updated_at"),
            synced_stamp(PEOPLE_SOURCE_SLUG),
        )

    # The seven aggregates run once per request; the stamp-keyed ``lru_cache``
    # on the records stays the cross-request authority.
    return memoized("events.queries.published_records_stamp", read)


@lru_cache(maxsize=8)
def _cached_published_records(
    stamp: tuple[tuple[int, str], ...],
) -> tuple[dict[str, Any], ...]:
    """The published event records for exactly one stamp, newest first.

    The query is bound to the stamp the cache key names, so a warmed answer
    cannot outlive the rows it was read from: any write to any table the
    records derive from moves the stamp, and the next read rebuilds the entry
    instead of serving stale pages.  A zero-count stamp is an empty table --
    an empty event catalogue, not a failure -- and caches as such.
    """

    del stamp
    return tuple(_record(event) for event in _published().order_by("-start_datetime", "pk"))


def published_event_record(event_id: uuid.UUID | str) -> dict[str, Any] | None:
    """One published event's record, or ``None`` when it publishes none."""

    from core.request_cache import current_memo

    if current_memo() is not None:
        # Inside a request the full published set is already memoized above
        # (every detail page reads it first through ``event_groups``), so a
        # second filtered table scan with the same annotation and prefetches
        # would only re-read the same rows.  The lookup is over the same
        # public-status set the filtered query would return.
        wanted = str(event_id)
        return next(
            (record for record in published_event_records() if record.get("identity_id") == wanted),
            None,
        )
    event = _published().filter(content_id=event_id).first()
    return None if event is None else _record(event)


def event_public_record(event: Event) -> dict[str, Any]:
    """Return the public record for an Event, read from its own row.

    An event row always carries its schedule, so unlike the split identity /
    content rows this replaces, a resolved identity always publishes.  A row
    whose status is not public raises, keeping the "404 rather than a guess"
    contract the former content-less identity had.
    """

    if not is_published(event):
        raise EventIdentityNotFound("event_content_unavailable")
    record = published_event_record(event.content_id)
    if record is None:
        raise EventIdentityNotFound("event_content_unavailable")
    return record


def published_event_records_by_path(paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The published records these public paths address, keyed by the path given.

    An event answers to two paths: the canonical ``/events/<public id>/<slug>``
    it carries, and ``/events/<identity uuid>/<slug>``, which is what the
    catalogue's own cross-references were written with.  A caller holding a
    mixture of both should not have to know which it has, so both forms are
    resolved here, and a path this database publishes nothing for is simply
    absent from the result.
    """

    public_ids: set[int] = set()
    identity_ids: set[uuid.UUID] = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3 or parts[1] != "events":
            continue
        token = parts[2]
        if token.isdigit():
            public_ids.add(int(token))
            continue
        try:
            identity_ids.add(uuid.UUID(token))
        except ValueError:
            continue
    if not public_ids and not identity_ids:
        return {}

    resolved: dict[str, dict[str, Any]] = {}
    for event in _published().filter(Q(public_id__in=public_ids) | Q(content_id__in=identity_ids)):
        record = _record(event)
        resolved[record["public_path"]] = record
        resolved[f"/events/{record['identity_id']}/{record['slug']}"] = record
    return resolved
