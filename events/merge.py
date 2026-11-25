"""Merge one reviewed duplicate ``events.Event`` into the event it duplicates.

A bulk load can leave two rows for one occurrence: the reviewed
``_data/events.yaml`` record (description, speakers, the older public ID) and a
provider-minted Luma record (every registration, nothing else).  This module is
the application service that folds the duplicate into the kept event.  It never
decides *which* rows are duplicates -- a person names each ``keep:duplicate``
pair, and the dry run shows what the two records hold before anything moves.

Per pair, inside one transaction:

1. ``event_registrants.EventRegistration`` rows move to the kept event.  A row
   whose registrant identity is already registered for the kept event is
   dropped rather than doubled.  A kept event that already holds rows from the
   same provider is refused: a later provider refresh replaces that provider's
   rows on the event wholesale, so two provider events sharing one event would
   overwrite each other.
2. The duplicate's source identity (for a Luma record, its provider event id)
   is recorded in ``content.EventMergedIdentity`` against the kept event, with
   its public ID, UUID and calendar UID.  Provider discovery and the registrant
   importers resolve that identity to the kept event from then on, so a later
   export neither recreates the duplicate nor strands its registrations.
3. Speaker/host links the kept event lacks are appended; package registrations
   and an operator config move when they do not collide; an *empty* Q&A session
   (no question, no co-host invite) is deleted, a non-empty one is refused;
   merge records that pointed at the duplicate are re-pointed.  Any other
   relation holding rows refuses the pair.
4. The retired public URL ``/events/<duplicate public id>/<slug>`` answers with
   a one-hop ``301`` to the kept event (``content.public_views``).
5. The duplicate row is deleted and one ``core.AuditEvent`` is appended.

The kept event's own fields -- title, slug, description, start time -- are not
touched, unless the operator explicitly asks for the duplicate's start time.

Idempotent: a pair whose duplicate is already merged into the same kept event
reports ``already_merged`` and writes nothing.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from community_base.events.models import Event, EventHost
from community_base.events.models import EventRegistration as PackageRegistration
from django.db import transaction

from content.models import EventMergedIdentity, EventSource
from event_qna.models import EventQnaSession
from event_registrants.models import EventOperatorConfig, EventRegistration

AUDIT_ACTION = "events.duplicate_merged"
AUDIT_TARGET_TYPE = "events.event"
DEFAULT_ACTOR_REF = "system:merge-duplicate-events"
DEFAULT_MAX_DAYS_APART = 1

#: Reverse relations of ``events.Event`` this service knows how to carry over.
#: A duplicate holding rows in any relation not listed here is refused, so a
#: relation added later can never be silently cascaded away by a merge.
HANDLED_RELATIONS = frozenset(
    {
        "source_identity",
        "merged_identities",
        "event_host_links",
        "registrations",
        "qna_session",
        "registrant_registrations",
        "operator_config",
    }
)


class EventMergeRefused(ValueError):
    """A pair this service will not merge; ``code`` names why, never a personal value."""

    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class EventMergeRunAborted(RuntimeError):
    """A pair raised mid-run.  Carries the pairs that had already finished.

    Each pair commits on its own, so an error in pair N leaves pairs 1..N-1
    applied.  The operator needs that list to know what is already done; the
    failing pair itself rolled back with its transaction.
    """

    def __init__(self, *, completed: list[dict[str, Any]], failed_pair: str, error: str) -> None:
        super().__init__(f"merge run aborted at pair {failed_pair}: {error}")
        self.completed = completed
        self.failed_pair = failed_pair
        self.error = error


@dataclass(frozen=True, slots=True)
class MergePair:
    """One reviewed pair, named by database row id."""

    keep_id: int
    duplicate_id: int

    @classmethod
    def parse(cls, value: str) -> MergePair:
        keep, separator, duplicate = value.partition(":")
        if (
            separator != ":"
            or not keep.isdigit()
            or not duplicate.isdigit()
            or int(keep) < 1
            or int(duplicate) < 1
        ):
            raise ValueError("a pair is KEEP_ID:DUPLICATE_ID, two positive event row ids")
        return cls(keep_id=int(keep), duplicate_id=int(duplicate))

    def label(self) -> str:
        return f"{self.keep_id}:{self.duplicate_id}"


# --------------------------------------------------------------------------
# Describing a record
# --------------------------------------------------------------------------


def _source(event: Event) -> EventSource | None:
    try:
        return event.source_identity
    except EventSource.DoesNotExist:
        return None


def describe_event(event: Event) -> dict[str, Any]:
    """What a person needs to compare two records.  No attendee value, only counts."""

    source = _source(event)
    registrations = EventRegistration.objects.filter(event=event)
    by_provider = Counter(registrations.values_list("provider", flat=True))
    by_status = Counter(registrations.values_list("status", flat=True))
    session = EventQnaSession.objects.filter(event=event).first()
    hosts = [
        {"host": link.host.external_ref or link.host.name, "role": link.role}
        for link in event.event_host_links.select_related("host").order_by("position", "pk")
    ]
    return {
        "row_id": event.pk,
        "public_id": event.public_id,
        "content_id": str(event.content_id) if event.content_id else None,
        "path": f"/events/{event.public_id}/{event.slug}" if event.public_id else None,
        "title": event.title,
        "slug": event.slug,
        "status": event.status,
        "start_datetime": event.start_datetime.isoformat(),
        "end_datetime": event.end_datetime.isoformat() if event.end_datetime else None,
        "timezone": event.timezone,
        "source": (
            {
                "repository": source.repository,
                "revision": source.revision,
                "source_key": source.source_key,
            }
            if source
            else None
        ),
        "has_description": bool(event.description_html or event.description),
        "description_length": len(event.description_html or event.description or ""),
        "hosts": hosts,
        "registrations": {
            "total": sum(by_provider.values()),
            "by_provider": dict(sorted(by_provider.items())),
            "by_status": dict(sorted(by_status.items())),
        },
        "package_registrations": PackageRegistration.objects.filter(event=event).count(),
        "qna_session": (
            {
                "state": session.state,
                "questions": session.questions.count(),
                "cohost_invites": session.cohost_invites.count(),
            }
            if session
            else None
        ),
        "merged_identities": EventMergedIdentity.objects.filter(event=event).count(),
    }


def _diff(keep: dict[str, Any], duplicate: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "title",
        "start_datetime",
        "end_datetime",
        "status",
        "has_description",
        "hosts",
        "registrations",
        "qna_session",
    )
    return {
        field: {
            "keep": keep[field],
            "duplicate": duplicate[field],
            "same": keep[field] == duplicate[field],
        }
        for field in fields
    }


# --------------------------------------------------------------------------
# Planning: read-only, shared by the dry run and the apply
# --------------------------------------------------------------------------


def _unhandled_relation_rows(event: Event) -> dict[str, int]:
    found: dict[str, int] = {}
    for relation in Event._meta.related_objects:
        accessor = relation.get_accessor_name()
        if accessor in HANDLED_RELATIONS:
            continue
        total = relation.related_model._default_manager.filter(
            **{relation.field.name: event}
        ).count()
        if total:
            found[accessor] = total
    return found


def _plan(
    keep: Event, duplicate: Event, *, max_days_apart: int, allow_date_mismatch: bool
) -> dict[str, Any]:
    """Every count the merge will produce, or :class:`EventMergeRefused`.  Writes nothing."""

    if keep.pk == duplicate.pk:
        raise EventMergeRefused("same_event")

    gap = abs(keep.start_datetime - duplicate.start_datetime)
    if gap > timedelta(days=max_days_apart) and not allow_date_mismatch:
        raise EventMergeRefused(
            "start_dates_too_far_apart",
            gap_hours=round(gap.total_seconds() / 3600, 2),
            max_days_apart=max_days_apart,
        )

    unhandled = _unhandled_relation_rows(duplicate)
    if unhandled:
        raise EventMergeRefused("duplicate_has_unhandled_rows", relations=unhandled)

    # Registrations.
    duplicate_rows = list(
        EventRegistration.objects.filter(event=duplicate).values_list("provider", "identity_id")
    )
    duplicate_providers = {provider for provider, _ in duplicate_rows}
    kept_providers = set(
        EventRegistration.objects.filter(event=keep).values_list("provider", flat=True).distinct()
    )
    shared_providers = sorted(duplicate_providers & kept_providers)
    if shared_providers:
        raise EventMergeRefused(
            "kept_event_has_same_provider_registrations", providers=shared_providers
        )
    kept_identities = set(
        EventRegistration.objects.filter(event=keep).values_list("identity_id", flat=True)
    )
    moved = sum(1 for _, identity in duplicate_rows if identity not in kept_identities)
    dropped = len(duplicate_rows) - moved
    identity_counts = Counter(identity for _, identity in duplicate_rows)
    repeated_within_duplicate = sum(count - 1 for count in identity_counts.values() if count > 1)

    # Package (on-site) registrations: moved only when nothing collides.
    package_rows = list(PackageRegistration.objects.filter(event=duplicate))
    if package_rows:
        kept_emails = set(
            PackageRegistration.objects.filter(event=keep).values_list(
                "normalized_email", flat=True
            )
        )
        kept_users = set(
            PackageRegistration.objects.filter(event=keep, user__isnull=False).values_list(
                "user_id", flat=True
            )
        )
        if any(
            row.normalized_email in kept_emails or (row.user_id and row.user_id in kept_users)
            for row in package_rows
        ):
            raise EventMergeRefused("package_registration_collision")

    # Hosts.
    kept_links = set(keep.event_host_links.values_list("host_id", "role"))
    hosts_added = sum(
        1
        for host_id, role in duplicate.event_host_links.values_list("host_id", "role")
        if (host_id, role) not in kept_links
    )

    # Q&A: only an empty session may go.
    session = EventQnaSession.objects.filter(event=duplicate).first()
    qna_deleted = 0
    if session is not None:
        questions = session.questions.count()
        invites = session.cohost_invites.count()
        if questions or invites:
            raise EventMergeRefused(
                "duplicate_qna_session_not_empty", questions=questions, cohost_invites=invites
            )
        qna_deleted = 1

    # Operator config (reminders, sponsor flags, uploaded banner).
    operator_config = "none"
    # Every event now has a picture (events.pictures), so both normally hold a
    # config: the kept event keeps its own, and only a kept event written
    # before the picture rule takes the duplicate's picture.
    duplicate_config = EventOperatorConfig.objects.filter(event=duplicate).first()
    keep_config = EventOperatorConfig.objects.filter(event=keep).first()
    if duplicate_config is not None:
        if keep_config is None:
            operator_config = "moved"
        elif not keep_config.banner_image and duplicate_config.banner_image:
            operator_config = "picture_moved"
        else:
            operator_config = "dropped_duplicate"

    source = _source(duplicate)
    return {
        "start_gap_hours": round(gap.total_seconds() / 3600, 2),
        "registrations_moved": moved,
        "registrations_dropped_already_registered": dropped,
        "registrations_repeated_within_duplicate": repeated_within_duplicate,
        "package_registrations_moved": len(package_rows),
        "hosts_added": hosts_added,
        "qna_sessions_deleted": qna_deleted,
        "operator_config": operator_config,
        "merged_identities_repointed": EventMergedIdentity.objects.filter(event=duplicate).count(),
        "retired_public_id": duplicate.public_id,
        "retired_path": (
            f"/events/{duplicate.public_id}/{duplicate.slug}" if duplicate.public_id else None
        ),
        "redirect_target": f"/events/{keep.public_id}/{keep.slug}" if keep.public_id else None,
        "source_identity_moved": (
            {
                "repository": source.repository,
                "revision": source.revision,
                "source_key": source.source_key,
            }
            if source
            else None
        ),
    }


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def _apply(
    keep: Event,
    duplicate: Event,
    plan: dict[str, Any],
    *,
    start_from_duplicate: bool,
    actor_ref: str,
    keep_before: dict[str, Any],
    duplicate_before: dict[str, Any],
) -> None:
    from core.audit import AuditWriteContext, record_audit_event

    # 1. Registrations.
    kept_identities = set(
        EventRegistration.objects.filter(event=keep).values_list("identity_id", flat=True)
    )
    dropped = EventRegistration.objects.filter(event=duplicate, identity_id__in=kept_identities)
    dropped_total, _ = dropped.delete()
    moved_total = EventRegistration.objects.filter(event=duplicate).update(event=keep)
    if (moved_total, dropped_total) != (
        plan["registrations_moved"],
        plan["registrations_dropped_already_registered"],
    ):
        raise EventMergeRefused("registrations_changed_during_merge")
    PackageRegistration.objects.filter(event=duplicate).update(event=keep)

    # 2. Hosts the kept event lacks, appended after its own.
    kept_links = set(keep.event_host_links.values_list("host_id", "role"))
    position = max(keep.event_host_links.values_list("position", flat=True), default=-1) + 1
    for link in duplicate.event_host_links.order_by("position", "pk"):
        if (link.host_id, link.role) in kept_links:
            continue
        EventHost.objects.create(
            event=keep, host_id=link.host_id, role=link.role, position=position
        )
        kept_links.add((link.host_id, link.role))
        position += 1

    # 3. The (verified empty) Q&A session, the operator config, earlier merges.
    EventQnaSession.objects.filter(event=duplicate).delete()
    if plan["operator_config"] == "moved":
        EventOperatorConfig.objects.filter(event=duplicate).update(event=keep)
    elif plan["operator_config"] == "picture_moved":
        picture = EventOperatorConfig.objects.get(event=duplicate)
        EventOperatorConfig.objects.filter(event=keep).update(
            banner_image=picture.banner_image,
            banner_content_type=picture.banner_content_type,
            banner_updated_at=picture.banner_updated_at,
        )
    EventMergedIdentity.objects.filter(event=duplicate).update(event=keep)

    # 4. The kept start time, only on explicit request.
    if start_from_duplicate and keep.start_datetime != duplicate.start_datetime:
        shift = duplicate.start_datetime - keep.start_datetime
        keep.start_datetime = duplicate.start_datetime
        if keep.end_datetime is not None:
            keep.end_datetime = keep.end_datetime + shift
        keep.ics_sequence += 1
        keep.save(update_fields=["start_datetime", "end_datetime", "ics_sequence", "updated_at"])

    # 5. Retire the duplicate's identity onto the kept event, then delete it.
    source = _source(duplicate)
    merged = EventMergedIdentity(
        event=keep,
        merged_row_id=duplicate.pk,
        public_id=duplicate.public_id,
        content_id=duplicate.content_id or uuid.uuid4(),
        slug=duplicate.slug,
        calendar_uid=duplicate.calendar_uid or "",
        repository=source.repository if source else "",
        revision=source.revision if source else "",
        source_key=source.source_key if source else "",
        source_path=source.source_path if source else "",
        source_checksum=source.source_checksum if source else "",
    )
    duplicate.delete()
    merged.save()

    record_audit_event(
        action=AUDIT_ACTION,
        target_type=AUDIT_TARGET_TYPE,
        target_id=keep.content_id,
        target_label=f"event {keep.public_id} <- {merged.public_id}",
        outcome="succeeded",
        context=AuditWriteContext(actor_ref=actor_ref),
        changes={**plan, "start_from_duplicate": start_from_duplicate},
        metadata={
            "keep_row_id": keep.pk,
            "duplicate_row_id": merged.merged_row_id,
            "duplicate_content_id": str(merged.content_id),
            "duplicate_calendar_uid": merged.calendar_uid,
            "duplicate_source": plan["source_identity_moved"],
            "keep_before": keep_before,
            "duplicate_before": duplicate_before,
        },
    )


def merge_pair(
    pair: MergePair,
    *,
    apply: bool = False,
    allow_date_mismatch: bool = False,
    max_days_apart: int = DEFAULT_MAX_DAYS_APART,
    start_from_duplicate: bool = False,
    actor_ref: str = DEFAULT_ACTOR_REF,
) -> dict[str, Any]:
    """Plan, and with ``apply`` perform, one reviewed merge.  One transaction per pair."""

    result: dict[str, Any] = {"pair": pair.label(), "applied": False}
    with transaction.atomic():
        duplicate = Event.objects.select_for_update().filter(pk=pair.duplicate_id).first()
        if duplicate is None:
            merged = EventMergedIdentity.objects.filter(merged_row_id=pair.duplicate_id).first()
            if merged is not None and merged.event_id == pair.keep_id:
                return {
                    **result,
                    "outcome": "already_merged",
                    "retired_public_id": merged.public_id,
                    "merged_at": merged.merged_at.isoformat(),
                }
            if merged is not None:
                return {
                    **result,
                    "outcome": "refused",
                    "reason": "duplicate_merged_into_another_event",
                }
            return {**result, "outcome": "refused", "reason": "duplicate_unknown"}
        keep = Event.objects.select_for_update().filter(pk=pair.keep_id).first()
        if keep is None:
            return {**result, "outcome": "refused", "reason": "keep_unknown"}

        keep_before = describe_event(keep)
        duplicate_before = describe_event(duplicate)
        result["keep"] = keep_before
        result["duplicate"] = duplicate_before
        result["diff"] = _diff(keep_before, duplicate_before)
        try:
            plan = _plan(
                keep,
                duplicate,
                max_days_apart=max_days_apart,
                allow_date_mismatch=allow_date_mismatch,
            )
        except EventMergeRefused as refusal:
            return {
                **result,
                "outcome": "refused",
                "reason": refusal.code,
                "detail": refusal.detail,
            }
        result["plan"] = plan
        result["start_from_duplicate"] = start_from_duplicate
        if not apply:
            return {**result, "outcome": "would_merge"}

        _apply(
            keep,
            duplicate,
            plan,
            start_from_duplicate=start_from_duplicate,
            actor_ref=actor_ref,
            keep_before=keep_before,
            duplicate_before=duplicate_before,
        )
        keep.refresh_from_db()
        result["keep_after"] = describe_event(keep)
        return {**result, "outcome": "merged", "applied": True}


def merge_pairs(pairs: list[MergePair], **options: Any) -> dict[str, Any]:
    """Run every pair independently; one refusal does not roll back another pair."""

    duplicates = [pair.duplicate_id for pair in pairs]
    if len(set(duplicates)) != len(duplicates):
        raise ValueError("a duplicate may appear in only one pair per run")
    if set(duplicates) & {pair.keep_id for pair in pairs}:
        raise ValueError("an event cannot be both kept and merged away in one run")
    results: list[dict[str, Any]] = []
    for pair in pairs:
        try:
            results.append(merge_pair(pair, **options))
        except Exception as error:
            # Only the class name crosses this boundary: a database error's
            # message can quote row values.
            raise EventMergeRunAborted(
                completed=results, failed_pair=pair.label(), error=type(error).__name__
            ) from error
    outcomes = Counter(result["outcome"] for result in results)
    return {
        "mechanism": "reviewed_event_duplicate_merge",
        "applied": bool(options.get("apply")),
        "pair_total": len(pairs),
        "outcomes": dict(sorted(outcomes.items())),
        "pairs": results,
    }
