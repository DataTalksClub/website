"""Backfill: apply the same redaction the write path applies today.

`e67948c` ("Keep addresses and answer keys out of logs and audit rows") made
`DatamailerSendAudit.response_payload`/`.error` and
`DatamailerOutboxEvent.response_payload`/`.last_error` redact on write, but a
row written before that landed still holds whatever was frozen at the time:
a raw recipient address, a rendered body carrying a live unsubscribe token, or
an exception message with the address and the request URL in it. This walks
every existing row and applies the exact same transform those two fields go
through on write, so a pre-fix row ends up shaped exactly like a fresh one.

Two things make a second pass over an already-migrated row safe rather than
harmful:

* `audit_response_payload` derives `message.email_fingerprint` from the raw
  `response` it is handed. A pre-fix row's stored `response_payload` *is*
  that raw response, so calling it once is exactly what a live write would
  have done. Calling it again with the now-redacted payload as the "response"
  would hash the literal string `"[REDACTED]"` and overwrite a working
  fingerprint with a useless one -- so a payload that already carries a
  fingerprint is left alone.
* `audit_error_text` always appends a `[error_fingerprint=...]` tag. Once
  that tag is on the string, leaving it alone is the only idempotent choice:
  reapplying the function would hash the *already-tagged* string as if it
  were a new raw error and append a second, unrelated tag.

Neither field is touched at all unless it still contains something the
shared policy flags as sensitive -- an address, a URL, a token -- so a
benign historical value (nothing was ever wrong with it) is left as it was
written, and a row with nothing in the field to begin with is a no-op.

`DatamailerOutboxEvent.payload` -- the delivery instruction Studio's admin
form already excludes from view -- is read here (its `dry_run` flag decides
whether a rendered body may survive) but never written.
"""

from __future__ import annotations

import re

from django.db import migrations

from core.redaction import is_sensitive_text
from course_management.datamailer.sync.audit_redaction import (
    EMAIL_FINGERPRINT_KEY,
    FINGERPRINT_LENGTH,
    MESSAGE_KEY,
    audit_error_text,
    audit_response_payload,
)

BATCH_SIZE = 500
_FINGERPRINT_TAG_RE = re.compile(
    rf"\[error_fingerprint=[0-9a-f]{{{FINGERPRINT_LENGTH}}}\]$"
)


def _contains_sensitive_leaf(value: object) -> bool:
    """Whether any string anywhere in ``value`` still trips the shared policy."""

    if isinstance(value, str):
        return is_sensitive_text(value)
    if isinstance(value, dict):
        return any(_contains_sensitive_leaf(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_leaf(item) for item in value)
    return False


def _redacted_response_payload(current: object, request_payload: object) -> tuple[object, bool]:
    """Idempotent backfill of one ``response_payload`` value.

    Returns ``(value, changed)``. ``value`` is the input unchanged when
    nothing needs to move.
    """

    if not isinstance(current, dict) or not current:
        return current, False

    message = current.get(MESSAGE_KEY)
    if isinstance(message, dict) and message.get(EMAIL_FINGERPRINT_KEY):
        # Already produced by this backfill (or by the write path): the
        # address alongside the fingerprint is already "[REDACTED]", so
        # nothing sensitive can still be here.
        return current, False

    if not _contains_sensitive_leaf(current):
        return current, False

    migrated = audit_response_payload(request_payload, current)
    return migrated, migrated != current


def _redacted_error_text(current: str) -> tuple[str, bool]:
    """Idempotent backfill of one ``error``/``last_error`` value."""

    if not current:
        return current, False

    if _FINGERPRINT_TAG_RE.search(current):
        # Already produced by this backfill (or by the write path): a raw
        # exception message essentially never happens to end in this exact
        # tag, so its presence means there is nothing left to do here.
        return current, False

    if not is_sensitive_text(current):
        # Nothing the shared policy would strip -- e.g. "Datamailer is not
        # configured", which never carried a member's data. Leave it as it
        # was written rather than tagging text that was never a problem.
        return current, False

    return audit_error_text(current), True


def _redact_queryset(model, *, response_field: str, error_field: str, payload_field: str | None):
    to_update: list = []
    queryset = model.objects.all()
    for row in queryset.iterator(chunk_size=BATCH_SIZE):
        request_payload = getattr(row, payload_field) if payload_field else {}
        new_response, response_changed = _redacted_response_payload(
            getattr(row, response_field), request_payload
        )
        new_error, error_changed = _redacted_error_text(getattr(row, error_field))

        if not response_changed and not error_changed:
            continue

        if response_changed:
            setattr(row, response_field, new_response)
        if error_changed:
            setattr(row, error_field, new_error)
        to_update.append(row)

        if len(to_update) >= BATCH_SIZE:
            model.objects.bulk_update(to_update, [response_field, error_field])
            to_update = []

    if to_update:
        model.objects.bulk_update(to_update, [response_field, error_field])


def redact_datamailer_audit_rows(apps, schema_editor):
    DatamailerSendAudit = apps.get_model("data", "DatamailerSendAudit")
    DatamailerOutboxEvent = apps.get_model("data", "DatamailerOutboxEvent")

    # `DatamailerSendAudit` has no separate request-payload field: only the
    # stored exchange survives, so dry-run detection falls back to whatever
    # flags the stored response itself carries -- the same fallback
    # `audit_response_payload` already uses when no request-side payload is
    # available.
    _redact_queryset(
        DatamailerSendAudit,
        response_field="response_payload",
        error_field="error",
        payload_field=None,
    )
    # `DatamailerOutboxEvent.payload` is the real delivery instruction and is
    # read here only to recover the `dry_run` flag the write path itself
    # reads from it -- it is never assigned or included in `bulk_update`.
    _redact_queryset(
        DatamailerOutboxEvent,
        response_field="response_payload",
        error_field="last_error",
        payload_field="payload",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("data", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            redact_datamailer_audit_rows,
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
