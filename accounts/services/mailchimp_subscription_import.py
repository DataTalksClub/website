"""Import Mailchimp newsletter subscription status onto existing accounts.

Reads a Mailchimp audience export's **subscribed** CSV only and writes a
single fact onto matching accounts: ``CustomUser.newsletter_subscribed``. See
the field's own docstring in ``accounts/models.py`` for the default (``True``,
unconditional, applied regardless of how the account was created).

The matching rule, exactly (owner's own words, quoted in the issue that scoped
this importer, and narrowed once more after an initial draft covered all
three of Mailchimp's export files -- see below): "when we import we set
subscribed only to those who are subscribed in mailchimp."

* A Mailchimp row matched by ``normalized_email`` against an existing account
  in the **subscribed** export -> that account's ``newsletter_subscribed`` is
  set ``True``, explicitly. This is usually a no-op against the model
  default, but it is written anyway: Mailchimp's subscribed file is the
  authoritative confirmation, not just an absence of contrary evidence.
* **A recorded local decision always wins (audit BE-15).** An account whose
  ``newsletter_preference_changed_at`` is non-null has had its preference
  deliberately changed in this application, and no audience snapshot --
  however fresh -- may override it. Such accounts are skipped and counted as
  ``skipped_local_decisions``; the usual case is a member who opted out
  locally after the export was cut, whose ``False`` a replay must preserve.
  The import writes through ``bulk_update`` precisely so its own migration
  writes never set that stamp.
* Everything else -- no match in the subscribed export at all, or a match
  only in Mailchimp's separate unsubscribed/cleaned exports -- is left
  completely untouched, at whatever value the account already holds (the
  model default, ``True``, for an account no earlier run has touched). This
  importer never opens, parses, or matches against the unsubscribed or
  cleaned files at all -- an earlier draft did, writing ``False`` for a match
  there, but the owner narrowed scope to subscribed-only before that shipped;
  nothing here reads those two files for any purpose.
* A Mailchimp row with no matching account at all (a subscriber who never
  took a course, or whose course account has some other email) -> **no
  account is created**. This importer only ever updates an existing row. Such
  rows are counted and reported for the caller to relay -- see
  ``_docs/runbooks/ingest-script-inventory.md`` section 10 for why: a
  registrant-only identity model is being designed separately (section 9),
  and this importer does not pre-empt that design with a second one.

If more than one existing account shares the same ``normalized_email`` (a
pre-reconciliation duplicate -- this can happen; the identity-state unique
constraint on ``normalized_email`` only applies to ``identity_state=active``
rows), every one of them is updated, not just one arbitrarily chosen row. The
alternative -- silently favouring one duplicate -- would leave the other one
stale until a human reconciles them, for no benefit.

Privacy: only ``Email Address`` is ever read from a row. ``OPTIN_IP``,
``CONFIRM_IP`` and ``NOTES`` are never stored anywhere -- real signup-IP PII
this migration otherwise carries nowhere, and a structural check of the real
export found ``NOTES`` empty on every single row across all three of
Mailchimp's files (subscribed included). ``TAGS`` was read structurally only,
while building this importer, to inform that same decision; it is not stored
either -- there is no importer-facing use for it today, and minimizing what
gets carried into the accounts table is the safer default absent one.

Batching, not resumability: unlike ``accounts/services/cmp_learner_import.py``
(which must survive a `kill -9` mid-run against a slow, dependent multi-table
import), this importer's own matching step is a fast, idempotent point
lookup against an already-indexed column (``normalized_email``). A bare
per-row loop is impractical at 130k+ rows, so this streams the source CSV in
batches and resolves each batch with one ``normalized_email__in`` query plus
one ``bulk_update`` -- but a full re-run from row zero is cheap enough that no
persisted watermark is needed. Running twice produces identical state: the
second run's per-row matches are unchanged, but every field value already
matches its target, so ``bulk_update`` has nothing to write.

Provenance and the cutoff contract (audit BE-15): every invocation --
dry runs included -- records a ``MailchimpSubscriptionImportRun`` row
carrying the subscribed CSV's SHA-256 digest, size, file name, and the
operator-declared snapshot date (``as_of``). Together with the per-account
``newsletter_preference_changed_at`` stamp this is the migration-cutoff
contract: the run row proves which snapshot was applied and when, and any
preference changed locally after that snapshot still wins, because the
import skips stamped accounts outright rather than comparing timestamps.
"""

from __future__ import annotations

import csv
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser, MailchimpSubscriptionImportRun

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "EMAIL_COLUMN",
    "MailchimpFileReport",
    "MailchimpImportError",
    "MailchimpSubscriptionImportResult",
    "import_mailchimp_subscriptions",
]

DEFAULT_BATCH_SIZE = 2000

# The one column this importer ever reads from a Mailchimp row. Everything
# else -- OPTIN_IP, CONFIRM_IP, NOTES, TAGS, MEMBER_RATING, and the rest --
# is deliberately never looked at; see the module docstring.
EMAIL_COLUMN = "Email Address"


class MailchimpImportError(RuntimeError):
    """A fail-closed refusal that never carries a source value."""


@dataclass(frozen=True, slots=True)
class MailchimpFileReport:
    file: str
    source_rows: int
    matched_rows: int
    unmatched_rows: int
    accounts_changed: int
    skipped_local_decisions: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "source_rows": self.source_rows,
            "matched_rows": self.matched_rows,
            "unmatched_rows": self.unmatched_rows,
            "accounts_changed": self.accounts_changed,
            "skipped_local_decisions": self.skipped_local_decisions,
        }


@dataclass(frozen=True, slots=True)
class MailchimpSubscriptionImportResult:
    subscribed: MailchimpFileReport
    applied: bool = True
    run_id: uuid.UUID | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "subscribed": self.subscribed.as_dict(),
            "applied": self.applied,
            "run_id": str(self.run_id) if self.run_id is not None else None,
        }


def _rows(path: Path) -> Iterator[dict[str, str]]:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError:
        raise MailchimpImportError("source-unreadable") from None
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or EMAIL_COLUMN not in reader.fieldnames:
            raise MailchimpImportError("source-missing-email-column")
        yield from reader


def _batched(iterable: Iterator[dict[str, str]], size: int) -> Iterator[list[dict[str, str]]]:
    batch: list[dict[str, str]] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _source_digest(path: Path) -> tuple[str, int, str]:
    """The subscribed CSV's SHA-256, byte size, and file name -- once per run.

    The digest is the snapshot's identity: two runs over the same file share
    one, and a re-cut export never can. Only the file's name is kept for the
    provenance row, never its containing path.
    """

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size, path.name


def _process_subscribed_file(path: Path, *, batch_size: int, apply: bool) -> MailchimpFileReport:
    source_rows = 0
    matched_rows = 0
    unmatched_rows = 0
    accounts_changed = 0
    skipped_local_decisions = 0

    for batch in _batched(_rows(path), batch_size):
        normalized_by_row = [normalize_account_email(row.get(EMAIL_COLUMN)) for row in batch]
        wanted = {value for value in normalized_by_row if value}
        accounts = (
            list(
                CustomUser.objects.filter(normalized_email__in=wanted).only(
                    "pk",
                    "normalized_email",
                    "newsletter_subscribed",
                    "newsletter_preference_changed_at",
                )
            )
            if wanted
            else []
        )
        by_email: dict[str, list[CustomUser]] = {}
        for account in accounts:
            by_email.setdefault(account.normalized_email, []).append(account)

        to_update: dict[int, CustomUser] = {}
        for normalized in normalized_by_row:
            source_rows += 1
            matches = by_email.get(normalized) if normalized else None
            if not matches:
                unmatched_rows += 1
                continue
            matched_rows += 1
            for account in matches:
                if account.newsletter_preference_changed_at is not None:
                    # BE-15: a recorded local decision outranks any audience
                    # snapshot.  The usual case is a member who opted out
                    # locally after the export was cut; replaying the export
                    # must preserve that newer decision.
                    skipped_local_decisions += 1
                    continue
                if not account.newsletter_subscribed:
                    account.newsletter_subscribed = True
                    to_update[account.pk] = account

        if to_update:
            accounts_changed += len(to_update)
            if apply:
                CustomUser.objects.bulk_update(
                    to_update.values(), ["newsletter_subscribed"], batch_size=batch_size
                )

    return MailchimpFileReport(
        file="subscribed",
        source_rows=source_rows,
        matched_rows=matched_rows,
        unmatched_rows=unmatched_rows,
        accounts_changed=accounts_changed,
        skipped_local_decisions=skipped_local_decisions,
    )


def import_mailchimp_subscriptions(
    *,
    subscribed: Path,
    as_of: date,
    batch_size: int = DEFAULT_BATCH_SIZE,
    apply: bool = True,
) -> MailchimpSubscriptionImportResult:
    """Apply Mailchimp's subscribed-export status onto matching accounts.

    Only ``subscribed`` is read -- see the module docstring for why
    Mailchimp's separate unsubscribed/cleaned exports are out of scope here,
    not merely unused. Never creates an account. Never touches an account
    with no match in ``subscribed``. Safe to run more than once.

    ``as_of`` is the operator-declared date the export was cut; it is
    recorded on the run's provenance row together with the file's digest.
    Accounts whose ``newsletter_preference_changed_at`` is non-null carry a
    local decision and are skipped entirely -- see the module docstring for
    the cutoff contract (audit BE-15).

    With ``apply=False`` (dry run), every count is still computed against the
    real, current database state -- including ``accounts_changed``, read as
    "would change" -- but nothing is written. Every invocation, dry runs
    included, records its provenance row.
    """

    resolved = subscribed.expanduser().resolve(strict=True)
    source_sha256, source_bytes, source_name = _source_digest(resolved)
    report = _process_subscribed_file(resolved, batch_size=batch_size, apply=apply)
    run = MailchimpSubscriptionImportRun.objects.create(
        source_name=source_name,
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        as_of=as_of,
        applied=apply,
        report=report.as_dict(),
    )
    return MailchimpSubscriptionImportResult(subscribed=report, applied=apply, run_id=run.id)
