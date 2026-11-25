#!/usr/bin/env python3
"""Make the free Academy sessions ordinary site events, and move their signups.

The free session series (``/academy/sessions/``, "Hireable With AI") used to
take registrations through its own form, posted to the Academy signup backend
(AWS Lambda, double opt-in; its rows never lived in this database).  The
sessions are ordinary site events now: people register on the event page like
for any other event, the page counts them, and the Zoom link reaches only
people with a seat.  This entry point does the two one-time halves of that
switch, both reconciling against rows that already exist:

**Events.**  The synced sessions page (``academy/sessions.md``) names each
session's number and Zoom meeting.  Each session is matched to exactly one
event -- the event already linked to the series at that position, else the one
event whose title starts ``"<series name> #<number>:"``; none or several is
reported under ``unresolved_sessions`` and never guessed.  A matched event is
linked to the event series whose slug is the sessions page's
``calendar_slug`` (created if absent, cadence-free and public) at the session's
position -- which is how ``academy.presentation.session_event_paths`` finds
the event page to send people to -- and, through the supported
``events.admin_services.update_event`` service (audited; the join URL is
recorded only as a digest), gets ``platform="zoom"``, the session's Zoom
meeting as ``zoom_join_url`` and its materials minus any link to the retired
``/academy/sessions/`` signup (the legacy "Event link").

**Signups** (only with ``--signups``).  An export of the Academy backend's
session signups, pulled by an operator into ``~/prod`` (outside the
repository, never copied into a worktree): a JSON array or JSON Lines of
objects.  Per row only these keys are read -- every other key the backend
stores (track, status, years, location, Bildungsgutschein interest, free text,
campaign parameters) is not opened:

``email``            the address (required)
``sessions``         the chosen session ids (``["session-1", ...]`` or ``"a,b"``)
``confirmed`` / ``status == "confirmed"`` / ``confirmed_at``
                     the double opt-in; an unconfirmed row is skipped
``name``             optional first name
``created_at``, ``confirmed_at``  ISO 8601 or epoch seconds, optional
``consent_version``  the Academy privacy-notice version the signup accepted
``academy_optin``    the separate "tell me about the program" opt-in

For every confirmed row and every chosen session whose event is still
upcoming, one native ``community_base.events.EventRegistration`` is written,
``confirmed`` (the backend already verified the address), linked to the
account holding that address when there is one.  It records the Academy
consent it was given -- ``privacy_notice_version`` ``academy:<version>`` with
the signup time, and ``academy_optin`` with the consent version in
``acquisition_metadata`` -- and no newsletter consent, which the Academy form
never asked for.  An address already registered for that event is left
exactly as it is.  Nothing here sends mail: no confirmation, no
verification.  (Reminders, where an event has them, treat these seats like
any other confirmed seat -- the Academy form promised a reminder.)

Dry run by default: every change is computed inside a transaction that is
rolled back, and the report says what ``--apply`` would write.  Replaying is
safe: a linked event, an equal field and an existing registration are left
alone, so a second ``--apply`` reports nothing created or changed.  The report
holds counts, session ids and public event ids only -- never an address, a
name or the join URL.

    uv run --frozen python scripts/prod/import_academy_sessions.py \\
        --database .tmp/production-prep-dataset/dataset.sqlite3 \\
        --signups ~/prod/dtc-data/academy-signups/<export>.json

    uv run --frozen python scripts/prod/import_academy_sessions.py \\
        --database .tmp/production-prep-dataset/dataset.sqlite3 \\
        --signups ~/prod/dtc-data/academy-signups/<export>.json --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
# Reconciles: it matches the synced sessions page against events that already
# exist and writes nothing for a session it cannot match.
BOOTSTRAPS_EMPTY_DATABASE = False

#: The retired signup page the legacy site linked these events to.
ACADEMY_SESSIONS_HOSTS = frozenset({"datatalks.club", "www.datatalks.club"})
ACADEMY_SESSIONS_PATH = "/academy/sessions/"
#: ``acquisition_metadata["source"]`` on every registration this writes.
REGISTRATION_SOURCE = "academy-sessions"


class AcademySessionsImportError(RuntimeError):
    """A refusal that carries a condition code, never a source value."""


@dataclass
class Report:
    applied: bool
    series_created: bool = False
    sessions: int = 0
    events_linked: list[int] = field(default_factory=list)
    events_updated: list[int] = field(default_factory=list)
    events_unchanged: list[int] = field(default_factory=list)
    unresolved_sessions: dict[str, str] = field(default_factory=dict)
    signup_rows: int = 0
    signup_rows_unconfirmed: int = 0
    signup_rows_without_sessions: int = 0
    signup_rows_invalid_email: int = 0
    session_selections: int = 0
    selections_unknown_session: int = 0
    selections_past_session: int = 0
    selections_unresolved_session: int = 0
    registrations_created: dict[str, int] = field(default_factory=dict)
    registrations_already_present: dict[str, int] = field(default_factory=dict)
    registrations_linked_to_account: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "series_created": self.series_created,
            "sessions": self.sessions,
            "events_linked": self.events_linked,
            "events_updated": self.events_updated,
            "events_unchanged": self.events_unchanged,
            "unresolved_sessions": self.unresolved_sessions,
            "signup_rows": self.signup_rows,
            "signup_rows_unconfirmed": self.signup_rows_unconfirmed,
            "signup_rows_without_sessions": self.signup_rows_without_sessions,
            "signup_rows_invalid_email": self.signup_rows_invalid_email,
            "session_selections": self.session_selections,
            "selections_unknown_session": self.selections_unknown_session,
            "selections_past_session": self.selections_past_session,
            "selections_unresolved_session": self.selections_unresolved_session,
            "registrations_created": self.registrations_created,
            "registrations_already_present": self.registrations_already_present,
            "registrations_linked_to_account": self.registrations_linked_to_account,
        }


def is_academy_sessions_link(url: object) -> bool:
    parts = urlsplit(str(url or ""))
    return (parts.hostname or "").lower() in ACADEMY_SESSIONS_HOSTS and (
        parts.path == ACADEMY_SESSIONS_PATH
    )


# --- the signup export ------------------------------------------------------


def read_signups(path: Path) -> list[dict[str, Any]]:
    """The export's rows, as a JSON array or JSON Lines; nothing is echoed back."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AcademySessionsImportError("signups_unreadable") from error
    stripped = text.lstrip()
    try:
        if stripped.startswith("["):
            rows = json.loads(stripped)
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise AcademySessionsImportError("signups_not_json") from error
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise AcademySessionsImportError("signups_not_a_list_of_objects")
    return rows


def _instant(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return None


def _confirmed(row: dict[str, Any]) -> bool:
    flag = row.get("confirmed")
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str) and flag.strip().lower() in {"true", "yes", "1"}:
        return True
    if str(row.get("status") or "").strip().lower() == "confirmed":
        return True
    return _instant(row.get("confirmed_at")) is not None


def _session_ids(value: object) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = [item for item in value if isinstance(item, str)]
    else:
        return []
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


def _optin(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"yes", "true", "1", "on"}


# --- the run ------------------------------------------------------------------


def _sessions_page() -> dict[str, Any]:
    from academy.presentation import sessions_page_of
    from content import catalogue

    page = sessions_page_of(catalogue.academy_pages())
    if page is None:
        raise AcademySessionsImportError("academy_sessions_page_missing")
    return page["data"]


def _match_event(series: Any, name: str, number: int) -> tuple[Any | None, str]:
    from community_base.events.models import Event

    linked = list(Event.objects.filter(event_series=series, series_position=number)[:2])
    if len(linked) == 1:
        return linked[0], ""
    candidates = list(
        Event.objects.filter(title__startswith=f"{name} #{number}:").exclude(status="draft")[:2]
    )
    if not candidates:
        return None, "no_event_with_that_title"
    if len(candidates) > 1:
        return None, "several_events_with_that_title"
    if candidates[0].event_series_id not in (None, getattr(series, "pk", None)):
        return None, "event_in_another_series"
    return candidates[0], ""


def _reconcile_events(data: dict[str, Any], report: Report) -> dict[str, Any]:
    """Link and update each session's event; return ``{session id: event}``."""

    from community_base.events.models import Event, EventSeries

    from events.admin_services import EventAdminError, update_event

    name = data["series"]["name"]
    series = EventSeries.objects.filter(slug=data["series"]["calendar_slug"]).first()
    if series is None:
        series = EventSeries.objects.create(
            name=name,
            slug=data["series"]["calendar_slug"],
            cadence="none",
            timezone="Europe/Berlin",
        )
        report.series_created = True
    matched: dict[str, Any] = {}
    for session in data["sessions"]:
        report.sessions += 1
        number = int(session["number"])
        event, reason = _match_event(series, name, number)
        if event is None:
            report.unresolved_sessions[session["id"]] = reason
            continue
        if event.event_series_id != series.pk or event.series_position != number:
            Event.objects.filter(pk=event.pk).update(event_series=series, series_position=number)
            report.events_linked.append(event.public_id)
        materials = [
            item
            for item in (event.materials or [])
            if not (isinstance(item, dict) and is_academy_sessions_link(item.get("url")))
        ]
        changes: dict[str, Any] = {}
        if event.platform != "zoom":
            changes["platform"] = "zoom"
        if session.get("zoom_url") and event.zoom_join_url != session["zoom_url"]:
            changes["zoom_join_url"] = session["zoom_url"]
        if materials != (event.materials or []):
            changes["materials"] = materials
        if changes:
            try:
                update_event(changes, event_id=event.pk)
            except EventAdminError as error:
                report.unresolved_sessions[session["id"]] = (
                    getattr(error, "code", "") or "event_update_refused"
                )
                continue
            report.events_updated.append(event.public_id)
        elif event.public_id not in report.events_linked:
            report.events_unchanged.append(event.public_id)
        matched[session["id"]] = Event.objects.get(pk=event.pk)
    return matched


def _import_signups(
    rows: list[dict[str, Any]],
    *,
    sessions: dict[str, dict[str, Any]],
    events: dict[str, Any],
    report: Report,
) -> None:
    from community_base.events.models import EventRegistration
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    from django.utils import timezone

    user_model = get_user_model()
    now = timezone.now()
    for row in rows:
        report.signup_rows += 1
        if not _confirmed(row):
            report.signup_rows_unconfirmed += 1
            continue
        chosen = _session_ids(row.get("sessions"))
        if not chosen:
            report.signup_rows_without_sessions += 1
            continue
        email = str(row.get("email") or "").strip()
        normalized = email.lower()
        try:
            validate_email(normalized)
        except ValidationError:
            report.signup_rows_invalid_email += 1
            continue
        created_at = _instant(row.get("created_at"))
        confirmed_at = _instant(row.get("confirmed_at")) or created_at or now
        consent_version = str(row.get("consent_version") or "").strip()[:30]
        account = user_model.objects.filter(email__iexact=normalized).first()
        for session_id in chosen:
            report.session_selections += 1
            if session_id not in sessions:
                report.selections_unknown_session += 1
                continue
            event = events.get(session_id)
            if event is None:
                report.selections_unresolved_session += 1
                continue
            if not event.is_upcoming:
                report.selections_past_session += 1
                continue
            existing = EventRegistration.objects.filter(event=event, normalized_email=normalized)
            if account is not None:
                existing = existing | EventRegistration.objects.filter(event=event, user=account)
            if existing.exists():
                report.registrations_already_present[session_id] = (
                    report.registrations_already_present.get(session_id, 0) + 1
                )
                continue
            EventRegistration.objects.create(
                event=event,
                user=account,
                original_email=email,
                normalized_email=normalized,
                display_name=str(row.get("name") or "").strip()[:200],
                status=EventRegistration.Status.CONFIRMED,
                verified_at=confirmed_at,
                privacy_notice_version=f"academy:{consent_version}" if consent_version else "",
                privacy_acknowledged_at=created_at or confirmed_at,
                newsletter_consent=None,
                acquisition_metadata={
                    "source": REGISTRATION_SOURCE,
                    "academy_session": session_id,
                    "academy_optin": _optin(row.get("academy_optin")),
                    "academy_consent_version": consent_version,
                },
            )
            report.registrations_created[session_id] = (
                report.registrations_created.get(session_id, 0) + 1
            )
            if account is not None:
                report.registrations_linked_to_account += 1


def run(*, signups: Path | None, apply: bool) -> Report:
    """Reconcile the events and import the signups; roll back unless ``apply``."""

    from django.db import transaction

    rows = read_signups(signups) if signups is not None else []
    report = Report(applied=apply)
    with transaction.atomic():
        data = _sessions_page()
        events = _reconcile_events(data, report)
        _import_signups(
            rows,
            sessions={session["id"]: session for session in data["sessions"]},
            events=events,
            report=report,
        )
        if not apply:
            transaction.set_rollback(True)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_target_arguments(parser)
    parser.add_argument(
        "--signups",
        type=Path,
        default=None,
        help=(
            "The Academy backend's session-signup export (JSON array or JSON Lines), "
            "read in place under ~/prod. Without it only the events are reconciled."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without it the run is a dry run that writes nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.signups is not None and not args.signups.expanduser().is_file():
        print(json.dumps({"error": "signups_not_a_file"}, indent=2))
        return 1
    configure_target(parser, args)
    try:
        report = run(
            signups=args.signups.expanduser() if args.signups is not None else None,
            apply=args.apply,
        )
    except AcademySessionsImportError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
