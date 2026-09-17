"""Import the reviewed event content records into the shared ``events.Event`` row.

Since #412 the identity, schedule, description, speakers and links one public
event page prints all live on one ``community_base.events.Event`` row. This
module is the reviewed door that content arrives through: it reconciles
validated records onto existing rows and never creates an event -- minting an
identity is :func:`events.identity.create_event_identity`'s job alone.

Where the records come from
---------------------------

``~/prod/dtc-data/content-staging/public_projection/events.json`` (outside this
repository -- see ``_docs/architecture/database-only-content.md``) is a
**staging artifact**, not a serving path and not a synchronization path. It
exists for exactly one
reason: to be pumped into the database once. It was built offline from the
legacy ``_data/events.yaml`` and then *reviewed and rewritten* -- the event
description bridge matched 159 events to their Luma descriptions, stripped the
"about the speaker" biography and the platform boilerplate from each one, and
bound every surviving link to a reviewed destination
(``_docs/event-description-bridge.md``). What is checked in is the reviewed
result, and there is nowhere else it exists.

So importing it reads nothing outside this repository and re-establishes no
dependency on the retired site: the record *records* the legacy tuple as
provenance, the same way the identity manifest does. This importer re-checks
that tuple against the event's ``content.EventSource`` row rather than trusting
it, so a record can only ever land on the event it was reviewed against.

The reviewed description provenance is a *gate*, not a column: it is what
lets this module refuse a description that arrived without the bridge behind
it. The durable provenance a reader needs afterwards -- repository, revision,
source key, checksum -- lives on the ``EventSource`` row, and the next source
to edit an event will not carry a bridge digest at all.

Replaying is safe. The row is updated to the record's state, and speakers and
links are replaced as a set, so a second run reports ``replayed`` and changes
nothing.

The second staging artifact
---------------------------

The corpus above is frozen at 421 records and cannot grow: its descriptions come
through the bridge, and the bridge matches on the legacy tuple, which an event
discovered in a provider export does not have. :func:`import_new_event_content`
is the other door -- the same validate-everything-then-reconcile shape, reading
an artifact built by ``scripts/staging/`` for identities minted from a provider
export instead. It reconciles against the identity's own source triple, so a
record can only ever land on an event minted from the export it was built from,
and never on one of the 421.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction

from community_base.events.models import Event, EventHost, Host

from content.models import EventSource

from .identity import decode_event_tags, encode_event_tags, package_kind_for_type

#: The record shape this module reads. Version 2 is the reviewed-description
#: shape: every record carries ``description_html``/``description_text`` and a
#: ``description_provenance`` that is null when no description was matched.
REVIEWED_RECORD_SCHEMA_VERSION = 2

_RECORD_FIELDS = frozenset(
    {
        "description_html",
        "description_provenance",
        "description_text",
        "ends_at",
        "episode",
        "identity_id",
        "links",
        "provenance",
        "public_path",
        "record_schema_version",
        "season",
        "slug",
        "speakers",
        "starts_at",
        "title",
        "type",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {"checksum", "repository", "revision", "source_key", "source_path", "source_url"}
)
#: The record shape :func:`import_new_event_content` reads. It carries no legacy
#: tuple -- the identity it names was minted from a provider export, so its
#: provenance is that export's own source triple -- and it carries the type
#: review that decided the row's ``type``, which the corpus above never needed
#: because the legacy source stated one.
NEW_EVENT_RECORD_SCHEMA_VERSION = 1

_NEW_EVENT_RECORD_FIELDS = frozenset(
    {
        "description_html",
        "description_provenance",
        "description_text",
        "ends_at",
        "episode",
        "identity_id",
        "links",
        "provenance",
        "record_schema_version",
        "season",
        "speakers",
        "starts_at",
        "type",
        "type_provenance",
    }
)
_NEW_EVENT_PROVENANCE_FIELDS = frozenset({"repository", "revision", "source_key"})
_NEW_EVENT_ARTIFACT_FIELDS = frozenset(
    {"content_sha256", "counts", "events", "schema_version", "source"}
)
_NEW_EVENT_ARTIFACT_SCHEMA_VERSION = 1

_SPEAKER_FIELDS = frozenset({"key", "name", "public_path"})
_LINK_FIELDS = frozenset({"label", "url"})
_TYPES = frozenset({"webinar", "workshop", "podcast", "conference"})


class EventContentImportError(ValueError):
    """A bounded record failure that carries a condition code, never a value."""


@dataclass(frozen=True, slots=True)
class ReviewedSpeaker:
    key: str
    name: str
    public_path: str


@dataclass(frozen=True, slots=True)
class ReviewedLink:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class ReviewedProvenance:
    repository: str
    revision: str
    source_key: str
    source_path: str
    checksum: str


@dataclass(frozen=True, slots=True)
class ReviewedEventContent:
    identity_id: uuid.UUID
    title: str
    slug: str
    type: str
    starts_at: datetime
    ends_at: datetime | None
    season: int | None
    episode: int | None
    description_html: str
    description_text: str
    description_provenance: dict[str, Any]
    speakers: tuple[ReviewedSpeaker, ...]
    links: tuple[ReviewedLink, ...]
    provenance: ReviewedProvenance


@dataclass(frozen=True, slots=True)
class NewEventSourceIdentity:
    """The source triple the identity this record names was minted under."""

    repository: str
    revision: str
    source_key: str


@dataclass(frozen=True, slots=True)
class NewEventContent:
    identity_id: uuid.UUID
    type: str
    starts_at: datetime
    ends_at: datetime | None
    season: int | None
    episode: int | None
    description_html: str
    description_text: str
    description_provenance: dict[str, Any]
    type_provenance: dict[str, Any]
    speakers: tuple[ReviewedSpeaker, ...]
    links: tuple[ReviewedLink, ...]
    provenance: NewEventSourceIdentity


@dataclass(frozen=True, slots=True)
class EventContentImportReport:
    total: int
    described: int
    created: int
    updated: int
    unchanged: int
    speakers: int
    links: int
    replayed: bool
    dry_run: bool


def _text(value: Any, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise EventContentImportError(f"event_content_{field}_invalid")
    if required and not value:
        raise EventContentImportError(f"event_content_{field}_invalid")
    return value


def _instant(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EventContentImportError(f"event_content_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise EventContentImportError(f"event_content_{field}_invalid") from error
    # A naive instant would be read back in whatever the server's zone happens
    # to be, which silently moves an event by hours.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventContentImportError(f"event_content_{field}_not_aware")
    return parsed


def _position(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EventContentImportError(f"event_content_{field}_invalid")
    return value


def _speaker(value: Any) -> ReviewedSpeaker:
    if not isinstance(value, dict) or set(value) != _SPEAKER_FIELDS:
        raise EventContentImportError("event_content_speaker_shape_invalid")
    return ReviewedSpeaker(
        key=_text(value["key"], field="speaker_key", maximum=255),
        name=_text(value["name"], field="speaker_name", maximum=255),
        # Empty is meaningful: it says this speaker has no person page, which is
        # a fact about the people catalogue rather than about the event.  The
        # shared row stores no path at all -- the profile link is resolved from
        # the catalogue at read time.
        public_path=_text(
            value["public_path"], field="speaker_path", maximum=1_024, required=False
        ),
    )


def _link(value: Any) -> ReviewedLink:
    if not isinstance(value, dict) or set(value) != _LINK_FIELDS:
        raise EventContentImportError("event_content_link_shape_invalid")
    url = _text(value["url"], field="link_url", maximum=2_048)
    split = urlsplit(url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise EventContentImportError("event_content_link_url_invalid")
    return ReviewedLink(label=_text(value["label"], field="link_label", maximum=255), url=url)


def _provenance(value: Any) -> ReviewedProvenance:
    if not isinstance(value, dict) or set(value) - _PROVENANCE_FIELDS:
        raise EventContentImportError("event_content_provenance_shape_invalid")
    return ReviewedProvenance(
        repository=_text(value.get("repository"), field="repository", maximum=255),
        revision=_text(value.get("revision"), field="revision", maximum=64),
        source_key=_text(value.get("source_key"), field="source_key", maximum=512),
        source_path=_text(value.get("source_path"), field="source_path", maximum=512),
        checksum=_text(value.get("checksum"), field="checksum", maximum=64),
    )


def _record(value: Any) -> ReviewedEventContent:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise EventContentImportError("event_content_record_shape_invalid")
    if value["record_schema_version"] != REVIEWED_RECORD_SCHEMA_VERSION:
        raise EventContentImportError("event_content_record_schema_version_invalid")

    identity_id = value["identity_id"]
    if not isinstance(identity_id, str):
        raise EventContentImportError("event_content_identity_id_invalid")
    try:
        parsed_id = uuid.UUID(identity_id)
    except ValueError as error:
        raise EventContentImportError("event_content_identity_id_invalid") from error
    if str(parsed_id) != identity_id:
        raise EventContentImportError("event_content_identity_id_invalid")

    kind = value["type"]
    if kind not in _TYPES:
        raise EventContentImportError("event_content_type_invalid")

    starts_at = _instant(value["starts_at"], field="starts_at")
    ends_at = None if value["ends_at"] == "" else _instant(value["ends_at"], field="ends_at")
    if ends_at is not None and ends_at < starts_at:
        raise EventContentImportError("event_content_ends_before_start")

    season = _position(value["season"], field="season")
    episode = _position(value["episode"], field="episode")
    if (season is None) != (episode is None):
        raise EventContentImportError("event_content_season_episode_incomplete")

    description_html = _text(
        value["description_html"], field="description_html", maximum=1_000_000, required=False
    )
    description_text = _text(
        value["description_text"], field="description_text", maximum=1_000_000, required=False
    )
    # The two halves are one description rendered two ways. Half of it is a
    # page that prints markup with no plain-text summary, or the reverse.
    if bool(description_html) != bool(description_text):
        raise EventContentImportError("event_content_description_incomplete")

    provenance_value = value["description_provenance"]
    if provenance_value is None:
        provenance_value = {}
    if not isinstance(provenance_value, dict):
        raise EventContentImportError("event_content_description_provenance_invalid")
    # A description without the reviewed provenance behind it is unreviewed
    # copy, and this is the only place that can still refuse it.
    if bool(description_html) != bool(provenance_value):
        raise EventContentImportError("event_content_description_provenance_missing")

    speakers = value["speakers"]
    links = value["links"]
    if not isinstance(speakers, list) or not isinstance(links, list):
        raise EventContentImportError("event_content_collection_invalid")
    parsed_speakers = tuple(_speaker(item) for item in speakers)
    if len({speaker.key for speaker in parsed_speakers}) != len(parsed_speakers):
        raise EventContentImportError("event_content_speaker_key_duplicated")

    return ReviewedEventContent(
        identity_id=parsed_id,
        title=_text(value["title"], field="title", maximum=1_000),
        slug=_text(value["slug"], field="slug", maximum=255),
        type=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        season=season,
        episode=episode,
        description_html=description_html,
        description_text=description_text,
        description_provenance=provenance_value,
        speakers=parsed_speakers,
        links=tuple(_link(item) for item in links),
        provenance=_provenance(value["provenance"]),
    )


def parse_reviewed_event_content(payload: Any) -> tuple[ReviewedEventContent, ...]:
    """Validate the complete staged candidate before any of it is applied."""

    if not isinstance(payload, list) or not payload:
        raise EventContentImportError("event_content_payload_invalid")
    records = tuple(_record(item) for item in payload)
    if len({record.identity_id for record in records}) != len(records):
        raise EventContentImportError("event_content_identity_id_duplicated")
    return records


def load_reviewed_event_content(path: Path) -> tuple[ReviewedEventContent, ...]:
    """Read and fully check the staged records."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EventContentImportError("event_content_source_unreadable") from error
    return parse_reviewed_event_content(payload)


def _matches(event: Event, record: ReviewedEventContent | NewEventContent) -> bool:
    # Both record shapes decide the same row state, so both legs ask the same
    # question of an existing row -- which is what makes a replay a no-op for
    # either of them.
    stored_type, season, episode = decode_event_tags(event.tags)
    return (
        stored_type == record.type
        and event.start_datetime == record.starts_at
        and event.end_datetime == record.ends_at
        and season == record.season
        and episode == record.episode
        and event.description_html == record.description_html
        and event.description == record.description_text
        and [
            (link.host.external_ref, link.host.name, link.position)
            for link in event.event_host_links.all()
        ]
        == [
            (speaker.key, speaker.name, position)
            for position, speaker in enumerate(record.speakers)
        ]
        and (event.materials or [])
        == [
            {"label": link.label, "url": link.url} for link in record.links
        ]
    )


def _apply(event: Event, record: ReviewedEventContent | NewEventContent) -> None:
    """Write one reviewed record onto its event row, replacing speakers and links."""

    event.start_datetime = record.starts_at
    event.end_datetime = record.ends_at
    event.description_html = record.description_html
    event.description = record.description_text
    event.kind = package_kind_for_type(record.type)
    event.tags = encode_event_tags(
        event_type=record.type, season=record.season, episode=record.episode
    )
    event.materials = [{"label": link.label, "url": link.url} for link in record.links]
    event.save(
        update_fields=(
            "start_datetime",
            "end_datetime",
            "description_html",
            "description",
            "kind",
            "tags",
            "materials",
            "updated_at",
        )
    )
    # Speakers are an ordered set the record owns outright: a re-run replaces
    # them rather than merging into them.  The host row itself is shared across
    # events -- one person is one row, keyed by the source key a credit names
    # them with.
    event.event_host_links.all().delete()
    for position, speaker in enumerate(record.speakers):
        # The display name is the host's own attribute, global across every
        # event that credits the key, so the first import to mint the row
        # decides it and later records never flip it back.
        host, _ = Host.objects.get_or_create(
            slug=speaker.key,
            defaults={
                "name": speaker.name,
                "kind": "speaker",
                "external_ref": speaker.key,
            },
        )
        EventHost.objects.create(event=event, host=host, position=position, role="speaker")


def _preflight(
    records: tuple[ReviewedEventContent | NewEventContent, ...],
    *,
    error_prefix: str,
) -> dict[uuid.UUID, Event]:
    events = {
        event.content_id: event
        for event in Event.objects.filter(content_id__in=[record.identity_id for record in records])
    }
    # Preflight the whole candidate before writing one row: a record that names
    # an unknown identity, or one whose reviewed provenance no longer matches
    # the identity it was reviewed against, must not partially apply.
    for record in records:
        event = events.get(record.identity_id)
        if event is None:
            raise EventContentImportError(f"{error_prefix}_identity_unknown")
        try:
            source = event.source_identity
        except EventSource.DoesNotExist as exc:
            raise EventContentImportError(f"{error_prefix}_provenance_conflict") from exc
        if (
            source.repository != record.provenance.repository
            or source.revision != record.provenance.revision
            or source.source_key != record.provenance.source_key
        ):
            raise EventContentImportError(f"{error_prefix}_provenance_conflict")
        if isinstance(record, ReviewedEventContent):
            if (
                source.source_path != record.provenance.source_path
                or source.source_checksum != record.provenance.checksum
            ):
                raise EventContentImportError(f"{error_prefix}_provenance_conflict")
            if event.title != record.title or event.slug != record.slug:
                raise EventContentImportError(f"{error_prefix}_identity_conflict")
    return events


@transaction.atomic
def import_event_content(*, path: Path, dry_run: bool = False) -> EventContentImportReport:
    """Attach the reviewed content to the events already in the database.

    This reconciles rather than bootstraps: identity is imported first, and a
    record naming an identity this database does not hold is a refusal, not a
    new event. Creating events is :func:`events.identity.create_event_identity`'s
    job alone.
    """

    records = load_reviewed_event_content(path)
    events = _preflight(records, error_prefix="event_content")

    created = updated = unchanged = 0
    for record in records:
        event = events[record.identity_id]
        if _matches(event, record):
            unchanged += 1
            continue
        if _row_has_content(event):
            updated += 1
        else:
            created += 1
        if dry_run:
            continue
        _apply(event, record)

    return EventContentImportReport(
        total=len(records),
        described=sum(1 for record in records if record.description_html),
        created=created,
        updated=updated,
        unchanged=unchanged,
        speakers=sum(len(record.speakers) for record in records),
        links=sum(len(record.links) for record in records),
        replayed=created == 0 and updated == 0,
        dry_run=dry_run,
    )


def _row_has_content(event: Event) -> bool:
    """Whether the row already carries content, for the created/updated split.

    An event imported from the manifest alone has an empty description and no
    host links; the first content leg to reach it therefore *creates* its
    content rather than updating something that was never there.
    """

    return bool(event.description_html) or event.event_host_links.exists()


def _new_event_provenance(value: Any) -> NewEventSourceIdentity:
    if not isinstance(value, dict) or set(value) != _NEW_EVENT_PROVENANCE_FIELDS:
        raise EventContentImportError("new_event_content_provenance_shape_invalid")
    return NewEventSourceIdentity(
        repository=_text(value["repository"], field="repository", maximum=255),
        revision=_text(value["revision"], field="revision", maximum=64),
        source_key=_text(value["source_key"], field="source_key", maximum=512),
    )


def _new_event_record(value: Any) -> NewEventContent:
    # The per-field validators are the ones above, so their condition codes stay
    # the shared ``event_content_*`` ones: they say the same thing about the same
    # field either way. Only what is specific to this leg is renamed.
    if not isinstance(value, dict) or set(value) != _NEW_EVENT_RECORD_FIELDS:
        raise EventContentImportError("new_event_content_record_shape_invalid")
    if value["record_schema_version"] != NEW_EVENT_RECORD_SCHEMA_VERSION:
        raise EventContentImportError("new_event_content_record_schema_version_invalid")

    identity_id = value["identity_id"]
    if not isinstance(identity_id, str):
        raise EventContentImportError("new_event_content_identity_id_invalid")
    try:
        parsed_id = uuid.UUID(identity_id)
    except ValueError as error:
        raise EventContentImportError("new_event_content_identity_id_invalid") from error
    if str(parsed_id) != identity_id:
        raise EventContentImportError("new_event_content_identity_id_invalid")

    kind = value["type"]
    if kind not in _TYPES:
        raise EventContentImportError("event_content_type_invalid")

    starts_at = _instant(value["starts_at"], field="starts_at")
    ends_at = None if value["ends_at"] == "" else _instant(value["ends_at"], field="ends_at")
    if ends_at is not None and ends_at < starts_at:
        raise EventContentImportError("event_content_ends_before_start")

    season = _position(value["season"], field="season")
    episode = _position(value["episode"], field="episode")
    if (season is None) != (episode is None):
        raise EventContentImportError("event_content_season_episode_incomplete")

    # A record with no description is the one thing this artifact cannot be for:
    # the corpus above exists to carry the 421 events' schedules whether or not
    # they were described, and this exists only to carry a description in.
    description_html = _text(value["description_html"], field="description_html", maximum=1_000_000)
    description_text = _text(value["description_text"], field="description_text", maximum=1_000_000)

    description_provenance = value["description_provenance"]
    if not isinstance(description_provenance, dict) or not description_provenance:
        raise EventContentImportError("new_event_content_description_provenance_missing")
    # The type is the one field on the row no source stated, so the review that
    # decided it has to arrive with it or the row is unreviewed copy.
    type_provenance = value["type_provenance"]
    if not isinstance(type_provenance, dict) or not type_provenance:
        raise EventContentImportError("new_event_content_type_provenance_missing")

    speakers = value["speakers"]
    links = value["links"]
    if not isinstance(speakers, list) or not isinstance(links, list):
        raise EventContentImportError("event_content_collection_invalid")
    parsed_speakers = tuple(_speaker(item) for item in speakers)
    if len({speaker.key for speaker in parsed_speakers}) != len(parsed_speakers):
        raise EventContentImportError("event_content_speaker_key_duplicated")

    return NewEventContent(
        identity_id=parsed_id,
        type=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        season=season,
        episode=episode,
        description_html=description_html,
        description_text=description_text,
        description_provenance=description_provenance,
        type_provenance=type_provenance,
        speakers=parsed_speakers,
        links=tuple(_link(item) for item in links),
        provenance=_new_event_provenance(value["provenance"]),
    )


def parse_new_event_content(payload: Any) -> tuple[NewEventContent, ...]:
    """Validate the complete staged candidate, envelope included, before applying any of it.

    The builder digests the artifact and this recomputes it. That is deliberate
    duplication: the receiving end checks what it was handed rather than trusting
    that whatever produced the file also produced the number beside it.
    """

    if not isinstance(payload, dict) or set(payload) != _NEW_EVENT_ARTIFACT_FIELDS:
        raise EventContentImportError("new_event_content_artifact_shape_invalid")
    if payload["schema_version"] != _NEW_EVENT_ARTIFACT_SCHEMA_VERSION:
        raise EventContentImportError("new_event_content_artifact_schema_version_invalid")
    # Which source built it is provenance a reader wants, not a value this module
    # may have an opinion about: a second builder is a different string, not a
    # different importer.
    _text(payload["source"], field="source", maximum=255)
    events = payload["events"]
    if not isinstance(events, list):
        raise EventContentImportError("new_event_content_artifact_events_invalid")
    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload["content_sha256"] != hashlib.sha256(encoded.encode("utf-8")).hexdigest():
        raise EventContentImportError("new_event_content_artifact_digest_mismatch")
    counts = payload["counts"]
    if not isinstance(counts, dict) or counts.get("events") != len(events):
        raise EventContentImportError("new_event_content_artifact_count_mismatch")

    records = tuple(_new_event_record(item) for item in events)
    if len({record.identity_id for record in records}) != len(records):
        raise EventContentImportError("new_event_content_identity_id_duplicated")
    return records


def load_new_event_content(path: Path) -> tuple[NewEventContent, ...]:
    """Read and fully check the staged records."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EventContentImportError("new_event_content_source_unreadable") from error
    return parse_new_event_content(payload)


@transaction.atomic
def import_new_event_content(*, path: Path, dry_run: bool = False) -> EventContentImportReport:
    """Attach staged content to identities minted from a provider export.

    Same posture as :func:`import_event_content`: it reconciles rather than
    bootstraps, and a record naming an identity this database does not hold is a
    refusal, never a new event. It reconciles on the identity's own source triple
    instead of the legacy tuple, which is what keeps it off the 421 -- their
    triples name the legacy repository and can never equal a provider one.
    """

    records = load_new_event_content(path)
    events = _preflight(records, error_prefix="new_event_content")

    created = updated = unchanged = 0
    for record in records:
        event = events[record.identity_id]
        if _matches(event, record):
            unchanged += 1
            continue
        if _row_has_content(event):
            updated += 1
        else:
            created += 1
        if dry_run:
            continue
        _apply(event, record)

    return EventContentImportReport(
        total=len(records),
        described=sum(1 for record in records if record.description_html),
        created=created,
        updated=updated,
        unchanged=unchanged,
        speakers=sum(len(record.speakers) for record in records),
        links=sum(len(record.links) for record in records),
        replayed=created == 0 and updated == 0,
        dry_run=dry_run,
    )
