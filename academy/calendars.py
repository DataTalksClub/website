"""The session calendars (``/academy/sessions/<id>.ics``), drawn from the sessions page.

Every value comes from the synced sessions page: the series name, each
session's times, title and Zoom link, and the calendar's own identity (its
``PRODID``, the ``UID`` domain and the stamp).  Changing the identity would make
calendar clients treat an updated file as new events, so it is content the
page carries rather than something derived here.

The text rules are RFC 5545 as the retired Astro generator applied them: a
backslash, comma and line break are escaped (a semicolon is written as is), and
lines are folded at 75 octets with CRLF and one space.  The output is byte for
byte what that generator produced for the same data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    parts: list[str] = []
    current = ""
    size = 0
    for character in line:
        width = len(character.encode("utf-8"))
        if size + width > (74 if parts else 75):
            parts.append(current)
            current = ""
            size = 0
        current += character
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def _stamp(instant: str) -> str:
    # 2026-11-23T16:00:00Z -> 20261123T160000Z
    return instant.replace("-", "").replace(":", "")


def _event(session: Mapping[str, Any], page: Mapping[str, Any]) -> list[str]:
    calendar = page["calendar"]
    series = page["series"]["name"]
    title = f"{series} #{session['number']}: {session['title']}"
    zoom = f"{calendar['zoom_label']} {session['zoom_url']}"
    description = f"{session['title']}\n\n{zoom}\n\n{calendar['note']}"
    return [
        "BEGIN:VEVENT",
        f"UID:{session['id']}@{calendar['uid_domain']}",
        f"DTSTAMP:{calendar['stamp']}",
        f"DTSTART:{_stamp(session['starts'])}",
        f"DTEND:{_stamp(session['ends'])}",
        f"SUMMARY:{_escape(title)}",
        f"DESCRIPTION:{_escape(description)}",
        f"LOCATION:{_escape(session['zoom_url'])}",
        f"URL:{session['zoom_url']}",
        "STATUS:CONFIRMED",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape(calendar['alarm_prefix'] + ' ' + session['title'])}",
        "TRIGGER:-PT15M",
        "END:VALARM",
        "END:VEVENT",
    ]


def calendar_for(page: Mapping[str, Any], sessions: Iterable[Mapping[str, Any]]) -> str:
    """One ``text/calendar`` document holding ``sessions`` of the sessions ``page``."""

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{page['calendar']['prodid']}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for session in sessions:
        lines.extend(_event(session, page))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
