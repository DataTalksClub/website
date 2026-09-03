"""Import Mailchimp's event-category tags as broad interest signals.

Reads a Mailchimp audience export's **subscribed** CSV only -- the same file
``accounts.services.mailchimp_subscription_import`` reads, and the same
conventions (read in place, ``utf-8-sig``, batching not needed here since only
a small subset of rows ever qualify). This module is the one place that turns
that file's ``TAGS`` column into anything -- every other importer that touches
this export leaves ``TAGS`` unread, by explicit design (see
``accounts.services.mailchimp_subscription_import``'s module docstring).

**What a tag is, and is not.** A Luma/Eventbrite registrant row
(``events.registrant_import``) names one specific event: "this identity
registered for event X on date Y." A Mailchimp tag like ``event-podcast``
names no event at all -- it is a self-selected or campaign-applied label
meaning "this person is broadly associated with podcast-related events."
Those are different kinds of fact, so they land in different tables:
:class:`events.models.EventRegistration` for the former,
:class:`events.models.EventRegistrantInterestSignal` for the latter. See
both models' docstrings for the full reasoning.

**Which tags.** The export's ``TAGS`` column carries 32 distinct values.
Exactly 8 of them are event-category tags, listed as the keys of
:data:`events.mailchimp_event_tag_categories.MAILCHIMP_EVENT_TAG_CATEGORIES`
-- the only tags this module ever maps to anything. Course-cohort tags
(``de-zoomcamp-2026`` and the like) are never read for any purpose beyond the
membership check that excludes them; they are out of scope, blocked on a
separate, unresolved decision gate, and nothing about their content is ever
stored. Three more tags (``registered-in-slack``, ``Berlin DataTalks Club
Group``, ``ai-bootcamp-free-email-course``) are dropped entirely by owner
decision -- see :data:`events.mailchimp_event_tag_categories.DROPPED_MAILCHIMP_TAGS`.
A row carrying none of the 8 event tags is skipped before any identity
lookup happens at all: no identity is created, no signal is written, nothing
about that row is stored anywhere.

**Matching.** For a row that does carry at least one of the 8 tags, the
address is consolidated through the exact same discipline
``events.registrant_import`` already established for Luma/Eventbrite rows --
reused, not reinvented, via
:func:`events.registrant_import.resolve_registrant_identity`:
``normalized_email`` against ``accounts_customuser`` first (an existing
account always wins), then against an existing registrant-only
``EventRegistrantIdentity`` (from the Luma/Eventbrite import, or from an
earlier run of this same importer), and only then a brand-new
registrant-only identity. There is no separate identity space for
Mailchimp-tag people; they resolve into the exact same pool source #9
(the Luma/Eventbrite registrant import) already populates.

**Idempotency.** ``EventRegistrantInterestSignal`` carries a
``(identity, category, source)`` unique constraint; writing is always through
``get_or_create``, so a replayed row that resolves to the same identity and
the same categories writes nothing new on its second pass. Identity
resolution has been idempotent since ``events.registrant_import`` established
it: a second run's "new" identity lookups instead find the row the first run
created.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from django.db import transaction

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser

from .mailchimp_event_tag_categories import MAILCHIMP_EVENT_TAG_CATEGORIES
from .models import EventRegistrantIdentity, EventRegistrantInterestSignal
from .registrant_import import resolve_registrant_identity

__all__ = [
    "EMAIL_COLUMN",
    "TAGS_COLUMN",
    "MailchimpEventTagImportError",
    "MailchimpEventTagImportReport",
    "parse_mailchimp_tags",
    "import_mailchimp_event_tags",
]

# The two columns this importer ever reads. Every other column Mailchimp's
# export carries (OPTIN_IP, CONFIRM_IP, NOTES, MEMBER_RATING, and the rest) is
# never opened here, same minimization default as the newsletter-subscription
# importer.
EMAIL_COLUMN = "Email Address"
TAGS_COLUMN = "TAGS"


class MailchimpEventTagImportError(RuntimeError):
    """A fail-closed refusal that never carries a source value."""


def parse_mailchimp_tags(raw: str) -> tuple[str, ...]:
    """Split one row's ``TAGS`` cell into its individual tag strings.

    Mailchimp's export nests each tag in its own double quotes *inside* the
    cell, rather than emitting a plain comma list -- confirmed against the
    real export: a CSV-unquoted ``TAGS`` value reads literally as
    ``"event","event-podcast"`` (quote characters included, as row data, not
    CSV structure). Splitting on ``,`` and stripping one layer of
    surrounding double quotes (plus incidental whitespace) from each piece
    recovers the clean tag strings. The vocabulary is small and fixed (32
    values total, per the structural check
    ``accounts.services.mailchimp_subscription_import`` already did), so
    there is no embedded-comma-within-a-tag case to guard against.
    """

    if not raw:
        return ()
    pieces = (segment.strip().strip('"').strip() for segment in raw.split(","))
    return tuple(piece for piece in pieces if piece)


def _rows(path: Path) -> Iterator[dict[str, str]]:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError:
        raise MailchimpEventTagImportError("source-unreadable") from None
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        if fieldnames is None or EMAIL_COLUMN not in fieldnames or TAGS_COLUMN not in fieldnames:
            raise MailchimpEventTagImportError("source-missing-required-column")
        yield from reader


def _classify_read_only(normalized_email: str) -> tuple[EventRegistrantIdentity | None, str]:
    """Dry-run counterpart of ``resolve_registrant_identity`` that never writes.

    Mirrors that function's lookup order exactly (account first, then a
    prior registrant-only identity) but stops short of creating anything --
    a would-be-new identity is reported as ``None``, never inserted.
    """

    account = CustomUser.objects.filter(normalized_email=normalized_email).order_by("pk").first()
    if account is not None:
        identity = EventRegistrantIdentity.objects.filter(account=account).first()
        return identity, "matched_account"

    existing = (
        EventRegistrantIdentity.objects.filter(normalized_email=normalized_email, account__isnull=True)
        .order_by("id")
        .first()
    )
    if existing is not None:
        return existing, "matched_prior_identity"

    return None, "new_identity"


@dataclass(frozen=True, slots=True)
class MailchimpEventTagImportReport:
    source_rows: int
    rows_with_event_tag: int
    rows_by_tag: dict[str, int]
    matched_account_total: int
    matched_prior_identity_total: int
    new_identity_total: int
    signals_created_total: int
    signals_already_present_total: int
    applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_rows": self.source_rows,
            "rows_with_event_tag": self.rows_with_event_tag,
            "rows_by_tag": dict(self.rows_by_tag),
            "matched_account_total": self.matched_account_total,
            "matched_prior_identity_total": self.matched_prior_identity_total,
            "new_identity_total": self.new_identity_total,
            "signals_created_total": self.signals_created_total,
            "signals_already_present_total": self.signals_already_present_total,
            "applied": self.applied,
        }


def import_mailchimp_event_tags(
    *, subscribed: Path, apply: bool = True
) -> MailchimpEventTagImportReport:
    """Import event-category interest signals from the subscribed export.

    With ``apply=False`` (dry run), every count -- including which identity
    a row would resolve to and which categories would be newly written -- is
    computed against the real, current database state through read-only
    queries only (:func:`_classify_read_only`); nothing is written. With
    ``apply=True``, resolution goes through
    :func:`events.registrant_import.resolve_registrant_identity`, the same
    function the Luma/Eventbrite importer uses, so a new identity created
    here is indistinguishable from one created there.

    Only rows carrying at least one of the 8 reviewed event tags are looked
    at beyond the tag check itself -- see the module docstring. Safe to run
    more than once: a replayed row finds the same identity and the same
    already-present signals, so a second run's ``signals_created_total`` is
    0 for every row a first run already processed.
    """

    source_rows = 0
    rows_with_event_tag = 0
    rows_by_tag: dict[str, int] = {tag: 0 for tag in MAILCHIMP_EVENT_TAG_CATEGORIES}
    matched_account = matched_prior = new_identity = 0
    signals_created = signals_already_present = 0

    for row in _rows(subscribed):
        source_rows += 1
        tags = parse_mailchimp_tags(row.get(TAGS_COLUMN, ""))
        present_tags = tuple(dict.fromkeys(tag for tag in tags if tag in MAILCHIMP_EVENT_TAG_CATEGORIES))
        if not present_tags:
            continue
        categories = tuple(sorted({MAILCHIMP_EVENT_TAG_CATEGORIES[tag] for tag in present_tags}))
        normalized_email = normalize_account_email(row.get(EMAIL_COLUMN))
        if normalized_email is None:
            continue

        rows_with_event_tag += 1
        for tag in present_tags:
            rows_by_tag[tag] += 1

        if apply:
            with transaction.atomic():
                identity, match_kind = resolve_registrant_identity(normalized_email)
                for category in categories:
                    _, created = EventRegistrantInterestSignal.objects.get_or_create(
                        identity=identity, category=category
                    )
                    if created:
                        signals_created += 1
                    else:
                        signals_already_present += 1
        else:
            identity, match_kind = _classify_read_only(normalized_email)
            existing_categories: frozenset[str] = frozenset()
            if identity is not None:
                existing_categories = frozenset(
                    EventRegistrantInterestSignal.objects.filter(identity=identity).values_list(
                        "category", flat=True
                    )
                )
            for category in categories:
                if category in existing_categories:
                    signals_already_present += 1
                else:
                    signals_created += 1

        if match_kind == "matched_account":
            matched_account += 1
        elif match_kind == "matched_prior_identity":
            matched_prior += 1
        else:
            new_identity += 1

    return MailchimpEventTagImportReport(
        source_rows=source_rows,
        rows_with_event_tag=rows_with_event_tag,
        rows_by_tag=rows_by_tag,
        matched_account_total=matched_account,
        matched_prior_identity_total=matched_prior,
        new_identity_total=new_identity,
        signals_created_total=signals_created,
        signals_already_present_total=signals_already_present,
        applied=apply,
    )
