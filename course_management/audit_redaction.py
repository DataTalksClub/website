"""What a send-audit row is allowed to keep.

`DatamailerSendAudit` exists so an operator can answer "did this send go out,
and if not, why not?".  It used to answer that by freezing the whole Datamailer
exchange: `str(exc)` verbatim, and `response_payload` verbatim.  That payload
carries `message.email`, and on a dry-run send it carries `rendered.html_body`
as well.  None of it passed through `core.redaction`, whose `_SENSITIVE_KEYS`
already classifies `body` and `emailbody` as sensitive — so the project's own
policy was bypassed on exactly the path that handles member addresses.

Two things make that worse than a stale copy of an address.  Relay embeds a
per-recipient unsubscribe and tracking URL in every mail it renders, so a
stored `html_body` plausibly carries a live recipient token into the database.
And the row is rendered by Django admin (`data/admin.py`), which is not Studio —
the one place a member's address is allowed to be seen.

So the row keeps what diagnoses a failure and drops what identifies a person:

* `error` keeps its exception class and status code with the address, URL or
  token spans masked, plus a fingerprint of the original so two occurrences of
  the same failure can still be recognised as the same failure;
* `response_payload` goes through `core.redaction`, which already knows that a
  `*_body` key and an address-shaped or URL-shaped string are sensitive;
* the rendered subject and bodies survive only on a send that did not deliver.

That last exception is narrow and deliberate.  `DATAMAILER_TRANSACTIONAL_DRY_RUN`
runs the full production path while Datamailer renders the mail and returns it
inline *without delivering*, and the e2e smoke suite verifies rendered email
over HTTP that way (`e2e/api_client.py`).  Removing the rendered bodies outright
would delete that capability from a non-delivering target for a risk that only
exists on a delivering one.  A production deployment does not set the flag, so
production audit rows hold no rendered body at all.
"""

from __future__ import annotations

import hashlib
from typing import Any

from core.redaction import mask_sensitive_spans, redact

# Kept verbatim on a dry-run send only.  Everything else in the payload is
# redacted whether the send delivered or not.
RENDERED_KEY = "rendered"
MESSAGE_KEY = "message"
EMAIL_KEY = "email"
EMAIL_FINGERPRINT_KEY = "email_fingerprint"
FINGERPRINT_LENGTH = 12


def recipient_fingerprint(email: str) -> str:
    """A stable, non-reversible stand-in for one recipient address.

    "Was this person mailed?" is a question an operator has to be able to ask,
    and it was answered by storing the address.  The same question is answered
    by hashing the address on the way in and hashing the asked-about address on
    the way out, which keeps the lookup and keeps the address out of the row.
    """

    normalized = (email or "").strip().casefold()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def audit_error_text(error: str) -> str:
    """A failure an operator can act on, with nothing that identifies a member."""

    if not error:
        return ""
    masked = mask_sensitive_spans(error)
    fingerprint = hashlib.sha256(error.encode("utf-8", "replace")).hexdigest()
    return f"{masked} [error_fingerprint={fingerprint[:FINGERPRINT_LENGTH]}]"


def is_dry_run_exchange(payload: Any, response: Any) -> bool:
    """True when this send rendered the mail without delivering it."""

    if isinstance(payload, dict) and payload.get("dry_run") is True:
        return True
    if not isinstance(response, dict):
        return False
    if response.get("dry_run") is True:
        return True
    # Datamailer reports a non-delivering render as `would_deliver: false`.
    return response.get("would_deliver") is False


def audit_response_payload(payload: Any, response: Any) -> dict[str, Any]:
    """The stored exchange: redacted, and bounded by the shared policy."""

    if not isinstance(response, dict):
        return {}

    redacted = redact(response)
    if not isinstance(redacted, dict):
        return {}

    if is_dry_run_exchange(payload, response) and RENDERED_KEY in response:
        rendered = response[RENDERED_KEY]
        if isinstance(rendered, dict):
            redacted[RENDERED_KEY] = dict(rendered)

    _add_recipient_fingerprint(redacted, response)
    return redacted


def _add_recipient_fingerprint(redacted: dict[str, Any], response: dict) -> None:
    message = response.get(MESSAGE_KEY)
    if not isinstance(message, dict):
        return
    fingerprint = recipient_fingerprint(message.get(EMAIL_KEY) or "")
    if not fingerprint:
        return
    redacted_message = redacted.get(MESSAGE_KEY)
    if not isinstance(redacted_message, dict):
        redacted_message = {}
        redacted[MESSAGE_KEY] = redacted_message
    redacted_message[EMAIL_FINGERPRINT_KEY] = fingerprint
