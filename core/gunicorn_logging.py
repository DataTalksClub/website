"""Access-log redaction for paths that carry a recipient token.

The deployed access format already excludes the query string, the client address
and every request header, because until now the only secrets in a request lived
there.  Relay's recipient links break that assumption: the open, click and
unsubscribe links it renders into mail carry an opaque per-recipient token in
the *path*, and the path is the one part of the request the access log keeps.

A token identifies one person and is enough on its own to read their unsubscribe
page or to unsubscribe them, so it must not be written to an access log that is
shipped, retained and searchable.  This logger keeps the route -- which is what
the log is for -- and replaces the token segment with a fixed marker.

This module is imported by gunicorn's arbiter before Django is configured, so it
imports nothing from Django and reads no settings.
"""

from __future__ import annotations

import re

from gunicorn.glogging import Logger  # type: ignore[import-untyped]

REDACTED_SEGMENT = "[token]"
# Kept in step with email_app/urls.py. Written as literal route shapes rather
# than a generic "redact the last segment of every path" rule, so a path is
# redacted because someone decided it should be, not by accident.
#
# Matched anywhere in an atom rather than against a whole value, so the request
# line and the path atom are both covered even though the deployed format uses
# only the path.
TOKEN_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/t/o/[A-Za-z0-9._~%-]+\.gif"), f"/t/o/{REDACTED_SEGMENT}.gif"),
    (re.compile(r"/t/c/[A-Za-z0-9._~%-]+"), f"/t/c/{REDACTED_SEGMENT}"),
    (re.compile(r"/unsubscribe/[A-Za-z0-9._~%-]+"), f"/unsubscribe/{REDACTED_SEGMENT}"),
)


def redact_token_path(value: str) -> str:
    """Replace the token segment of a recipient link, leaving the route intact."""

    if not isinstance(value, str) or not value:
        return value
    for pattern, replacement in TOKEN_PATH_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def is_token_bearing_path(path: object) -> bool:
    return isinstance(path, str) and any(pattern.search(path) for pattern, _ in TOKEN_PATH_PATTERNS)


class RecipientTokenSafeLogger(Logger):
    """Gunicorn access logger that never writes a recipient token.

    Gunicorn builds an atom for every request header, response header and WSGI
    environment variable, so the path appears under several keys, not only the
    one the deployed format uses.  Redacting every string atom of a recipient
    request keeps a future format change from reintroducing the leak, and the
    single cheap path test keeps that work off the other 99% of requests.
    """

    def atoms(self, resp, req, environ, request_time):  # type: ignore[no-untyped-def]
        values = super().atoms(resp, req, environ, request_time)
        if not is_token_bearing_path(environ.get("PATH_INFO")):
            return values
        return {
            key: redact_token_path(value) if isinstance(value, str) else value
            for key, value in values.items()
        }
