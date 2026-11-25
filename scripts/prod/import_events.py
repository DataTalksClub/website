#!/usr/bin/env python3
"""Import current Event identities and content into the database.

Provider registrations are owned by ``scripts/prod/import_event_registrants.py``.
Legacy source translation stays in import scripts; runtime event reads use
database rows directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

# These reviewed staging inputs live outside this repository, at
# ~/prod/dtc-data/content-staging/ -- the owner's explicit instruction: "let's
# not have it in our code. move it outside." (issue #253 discussion; see the
# eventbrite descriptions leg below for the earlier instance of the same move,
# and _docs/architecture/database-only-content.md for the full picture).
_CONTENT_STAGING_ROOT = Path.home() / "prod" / "dtc-data" / "content-staging"
IDENTITY_MANIFEST_PATH = _CONTENT_STAGING_ROOT / "event_identity_manifest.json"

EVENT_CONTENT_PATH = _CONTENT_STAGING_ROOT / "public_projection" / "events.json"
# Deliberately *not* under temporary/content/, unlike every sibling staging
# artifact above -- the owner's explicit instruction: "let's not have it in
# our code. move it outside." Built by scripts/build_eventbrite_descriptions.py
# from external raw Eventbrite content that never enters this repository
# either; see that script's module docstring.
EVENTBRITE_DESCRIPTIONS_PATH = (
    Path.home()
    / "prod"
    / "dtc-data"
    / "eventbrite-content"
    / "staging"
    / "eventbrite_descriptions.json"
)


class EventImportError(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


class IncompletePublishedEvents(EventImportError):
    """The run would leave a published event without a type or a speaker.

    Carries the offending events' row ids, public ids and titles -- public
    facts about the event, nothing about anyone registered for it.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__("published_event_incomplete")
        self.events = events


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def import_identities(
    *, manifest: Path | None = None, content: Path | None = None, apply: bool = True
) -> dict[str, Any]:
    """Import the reviewed identity manifest atomically.

    ``content`` is the reviewed content file: an event is created already
    carrying the type and speakers its record names, because no event may exist
    without them (:func:`events.identity.create_event_identity`).
    """

    from events.identity import EventIdentityError
    from scripts.prod.identity_manifest import import_identity_manifest

    try:
        report = import_identity_manifest(
            path=manifest or IDENTITY_MANIFEST_PATH,
            content_path=content or EVENT_CONTENT_PATH,
            dry_run=not apply,
        )
    except (EventIdentityError, OSError, ValueError) as error:
        raise EventImportError("identity_manifest_invalid") from error
    return {
        "events": report.event_total,
        "events_created": report.events_created,
        "events_updated": report.events_updated,
        "replayed": report.replayed,
        "applied": apply,
    }


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def import_content(*, source: Path | None = None, apply: bool = True) -> dict[str, Any]:
    """Attach the reviewed content records to the identities imported above.

    Runs after :func:`import_identities` and never before it: this reconciles
    against identities that already exist, and a record naming an identity the
    database does not hold is a refusal rather than a new event.
    """

    from events.content_import import EventContentImportError, import_event_content

    try:
        report = import_event_content(path=source or EVENT_CONTENT_PATH, dry_run=not apply)
    except (EventContentImportError, OSError, ValueError) as error:
        raise EventImportError("event_content_invalid") from error
    return {
        "events": report.total,
        # How many of the 421 carry a reviewed description at all. The rest
        # render a page with no description region, which is correct.
        "described": report.described,
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "speakers": report.speakers,
        "links": report.links,
        "replayed": report.replayed,
        "applied": apply,
    }


def import_eventbrite_descriptions(
    *, source: Path | None = None, apply: bool = True
) -> dict[str, Any]:
    """Let a cleaned Eventbrite description win outright over the Jekyll one it replaces.

    Runs after :func:`import_content`, never before it: this overwrites a
    ``description_html``/``description_text`` an event's ``EventContent`` row
    already carries, so the row has to exist first. This is description
    *authoring* precedence -- a third concern from both content bootstrap above
    and the registration-count legs below -- reported under its own key rather
    than folded into either.

    The product owner's ruling: "eventbrite wins over jekyll. but we remove
    'about the speaker' part and about dtc footer too." Full replacement, not
    fill-only-if-missing -- see :func:`events.eventbrite_content.
    apply_eventbrite_descriptions` for the resolution and overwrite mechanics,
    and :mod:`events.eventbrite_content` for what is stripped and why.

    A missing artifact is a normal state, not a failure: it exists only after
    an operator has run ``scripts/build_eventbrite_descriptions.py --write``
    against the external raw Eventbrite content and identity resolution (both
    outside this repository, at ``~/prod/dtc-data/``), and is reported as
    ``absent`` when nobody has.

    Expect ``event_content``'s own report, just above this leg's in the run
    output, to show the same events as ``updated`` on every replay rather than
    ``unchanged``: that step replays the *reviewed* Jekyll-sourced record,
    which genuinely disagrees with a row this leg has already overridden, so
    it correctly restores it -- and this leg then correctly overrides it back.
    Final state after one full run is always the cleaned Eventbrite text; the
    two legs simply keep re-asserting their own layer every time, by design.
    """

    from events.eventbrite_content import (
        EventbriteDescriptionError,
        apply_eventbrite_descriptions,
    )

    path = source or EVENTBRITE_DESCRIPTIONS_PATH
    if not path.is_file():
        return {"present": False, "applied": apply}
    try:
        report = apply_eventbrite_descriptions(path=path, dry_run=not apply)
    except (EventbriteDescriptionError, OSError, ValueError) as error:
        raise EventImportError("eventbrite_descriptions_invalid") from error
    return {
        "present": True,
        "events": report.total,
        "applied_total": report.applied,
        "unchanged": report.unchanged,
        "no_identity": report.no_identity,
        "no_content_yet": report.no_content_yet,
        "no_eventbrite_description": report.no_eventbrite_description,
        "applied": apply,
    }


def run(
    *,
    identity_manifest: Path | None = None,
    event_content_source: Path | None = None,
    eventbrite_descriptions_source: Path | None = None,
) -> dict[str, Any]:
    """Import current Event rows and content in one transaction."""

    from django.db import transaction

    from events.publication import incomplete_published_events

    with transaction.atomic():
        report = _run_legs(
            identity_manifest=identity_manifest,
            event_content_source=event_content_source,
            eventbrite_descriptions_source=eventbrite_descriptions_source,
        )
        # A published event must carry a type and a speaker (events.publication).
        # Refusing here rolls the whole run back rather than landing a database
        # whose public events list mixes complete and bare rows.
        incomplete = incomplete_published_events()
        if incomplete:
            raise IncompletePublishedEvents(incomplete)
        return report


def _run_legs(
    *,
    identity_manifest: Path | None,
    event_content_source: Path | None,
    eventbrite_descriptions_source: Path | None,
) -> dict[str, Any]:
    """The event legs in their fixed order. Only :func:`run` may call this.

    Split out purely so the transaction boundary is one unmissable line in
    :func:`run` rather than an indent level wrapped around the legs.
    """

    identities = import_identities(
        manifest=identity_manifest, content=event_content_source, apply=True
    )
    content = import_content(source=event_content_source, apply=True)
    # Description *authoring* precedence, distinct from content bootstrap
    # above: runs only after `content` so the EventContent row it overwrites
    # already exists. See import_eventbrite_descriptions's own docstring.
    eventbrite_descriptions = import_eventbrite_descriptions(
        source=eventbrite_descriptions_source, apply=True
    )
    return {
        "identities": identities,
        "event_content": content,
        "eventbrite_descriptions": eventbrite_descriptions,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--identity-manifest", type=Path, default=IDENTITY_MANIFEST_PATH)
    parser.add_argument("--event-content", type=Path, default=EVENT_CONTENT_PATH)
    parser.add_argument(
        "--eventbrite-descriptions",
        type=Path,
        default=EVENTBRITE_DESCRIPTIONS_PATH,
        help=(
            "Cleaned Eventbrite descriptions staged by "
            "scripts/build_eventbrite_descriptions.py --write, outside this "
            "repository (~/prod/dtc-data/eventbrite-content/staging/ by "
            "default on both ends). When present, wins outright over the "
            "Jekyll-sourced description for every event whose Eventbrite id "
            "resolves. Absent until an operator has built it."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _parser()
        args = parser.parse_args(argv)
        configure_target(parser, args)
        report = run(
            identity_manifest=args.identity_manifest.resolve(),
            event_content_source=args.event_content.resolve(),
            eventbrite_descriptions_source=args.eventbrite_descriptions.resolve(),
        )
    except IncompletePublishedEvents as error:
        print(
            json.dumps({"error": str(error), "incomplete_published_events": error.events}, indent=2)
        )
        return 1
    except EventImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
