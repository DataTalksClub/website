"""Datamailer contact failures that cannot carry a member's email address.

Datamailer defines `GET /api/contacts/status` and `GET /api/contacts/preferences`
with `email` as a query parameter, so for those two calls the address is part
of the request URL.  `requests` builds the `HTTPError` raised by
`raise_for_status()` with `response.url` inside its message, which means the
address is in the exception text, in every traceback that exception appears in,
and in anything that formats the exception.

That matters here more than it looks: this deployment configures no logging
handlers, so `logger.info` and `extra={...}` go nowhere, and a traceback
reaching `logging.lastResort` is the one log channel that is actually live.  A
handler can log `user_id=%s` as carefully as it likes and still write the
address to stderr through `logger.exception`.

The request shape is Datamailer's contract, not this repository's, so the fix
is not to reshape the call.  It is to make sure the failure that leaves this
package names only the operation and the HTTP status.  `email_app.relay_links`
already does the same thing one module away, dropping a `RequestException`
rather than chaining it so that a URL-borne unsubscribe token cannot reach a
traceback.

`DatamailerContactError` subclasses `requests.RequestException` so that callers
which already handle a request failure keep working unchanged; it simply has
nothing sensitive to say.
"""

from __future__ import annotations

import requests


class DatamailerContactError(requests.RequestException):
    """A Datamailer contact call failed.  Carries no URL and no address."""

    def __init__(self, operation: str, status_code: int | None = None):
        self.operation = operation
        self.status_code = status_code
        detail = f"HTTP {status_code}" if status_code is not None else "no response"
        super().__init__(f"Datamailer {operation} failed ({detail})")


def redacted_contact_error(
    operation: str, error: BaseException
) -> DatamailerContactError:
    """Restate a request failure without the URL that identifies the contact."""

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return DatamailerContactError(operation, status_code)
