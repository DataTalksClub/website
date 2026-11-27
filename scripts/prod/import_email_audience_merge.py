#!/usr/bin/env python3
"""Merge newsletter consent from CMP, Mailchimp, Relay and the website into one audience.

The rules live in ``_docs/architecture/email-audience-merge.md``; this script is
their only implementation.  In short, it keys every address by its normalized
email and collects, per address, the consent evidence and the opt-out evidence
each source holds, with timestamps:

* **CMP export** (SQLite, read in place): a course registration with
  ``accepted_newsletter`` and a confirmed address is consent, because the
  registration form required the box.  Bulk-imported rows without the box, and
  unconfirmed addresses, are counted but are not consent.  The retired
  Datamailer contact events in the same export contribute unsubscribes, hard
  bounces and complaints.
* **Mailchimp audience export**, either the UI's ``Export Audience`` ZIP
  (``subscribed_*``/``unsubscribed_*``/``cleaned_*`` CSVs) or a Marketing API
  members dump (``<audience>_subscribed.csv`` and so on): subscribed is consent
  at its confirm (opt-in) time, unsubscribed is an opt-out, and cleaned is a
  bounce.  The API dump has no unsubscribe or clean time, so the member's
  ``last_changed`` stands in for it.
* **Relay export** (``relay-contacts.csv`` and, optionally,
  ``relay-category-preferences.csv``, in the shape described by
  ``~/prod/dtc-data/requests/relay-audience-export-*.md``): a ``subscribed``
  subscription in audience ``dtc`` is consent.  An unsubscribed subscription in
  any audience, a global unsubscribe, a hard bounce or a complaint is an
  opt-out.
* **The website database** (the ``--database``/``--deployment-target``
  selection): an event registration's recorded newsletter consent, and a
  member's own stamped newsletter decision.

Any unsubscribe, bounce or complaint wins and is never resubscribed.  Anyone
with neither consent nor an opt-out is left out of the newsletter.  The script
never creates an account: a Relay- or Mailchimp-only address stays a
newsletter-only contact.

What it writes
--------------

Every run writes into ``--output-dir``, which must be outside the repository
(use a fresh ``~/prod/dtc-data/email-audience/run-<timestamp>/``):

* ``report.json``: counts only, safe to paste;
* ``relay-import-dtc.csv``: the merged audience in the column set Relay's
  ``import_audience_csv`` accepts, for ``--organization datatalksclub
  --audience dtc --client dtc-newsletter``.  Relay keeps existing opt-outs on
  import, and this file never asks it to resubscribe anyone;
* ``evidence.jsonl``: per address, the latest event of each kind from each
  source.  It holds real addresses, which is why the directory must stay under
  ``~/prod``;
* ``site-account-changes.json``: the account ids whose
  ``newsletter_subscribed`` would change, or did change.  Ids only.

Without ``--apply`` nothing is written to the database.  With ``--apply`` each
matching account's ``User.newsletter_subscribed`` is set to the merged verdict
through ``bulk_update``.  That path does not stamp
``newsletter_preference_changed_at``, so the merge is never mistaken for a
member's decision.  An account carrying a member's own decision keeps it,
unless an opt-out exists somewhere, because opt-outs win.  Absorbed and
quarantined accounts are never written.  A second ``--apply`` changes nothing.

    uv run --frozen python scripts/prod/import_email_audience_merge.py \\
        --database .tmp/audience/dataset.sqlite3 \\
        --cmp-export /data/tmp/rds-export/cmp/rds-prod-20261126-153117.db \\
        --mailchimp-export-dir ~/prod/dtc-data/mailchimp-export \\
        --relay-export-dir ~/prod/dtc-data/relay-export-<date> \\
        --output-dir ~/prod/dtc-data/email-audience/run-<timestamp>

Each source flag is optional, but at least one is required.  A missing Relay
export is reported as ``"relay": "missing"`` so that the counts are never
mistaken for a complete merge.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
# It reconciles consent onto accounts other importers created and never
# creates one, so on an empty database it only writes its output files.
BOOTSTRAPS_EMPTY_DATABASE = False

NEWSLETTER_AUDIENCE = "dtc"
RELAY_IMPORT_TARGET = {
    "organization": "datatalksclub",
    "audience": NEWSLETTER_AUDIENCE,
    "client": "dtc-newsletter",
}
RELAY_IMPORT_COLUMNS = (
    "email",
    "subscription_status",
    "verified",
    "unsubscribed",
    "global_unsubscribed",
    "hard_bounced",
    "complained",
    "unsubscribe_reason",
    "tags",
)

SOURCES = ("cmp", "mailchimp", "relay", "site")
CONSENT = "consent"
UNSUBSCRIBED = "unsubscribed"
BOUNCED = "bounced"
COMPLAINED = "complained"
OPT_OUTS = frozenset({UNSUBSCRIBED, BOUNCED, COMPLAINED})
# Recorded for the audit and the counts, but never consent.
NEUTRAL_KINDS = frozenset(
    {
        "cmp_registration_without_box",
        "cmp_consent_unconfirmed_email",
        "relay_pending",
        "relay_course_audience_subscribed",
        "relay_course_category_opt_out",
        "site_declined",
    }
)

# The retired Datamailer contact events CMP still holds, by the opt-out they mean.
CMP_OPT_OUT_EVENTS = {
    "subscription.unsubscribed": UNSUBSCRIBED,
    "contact.global_unsubscribed": UNSUBSCRIBED,
    "contact.unsubscribed": UNSUBSCRIBED,
    "contact.hard_bounced": BOUNCED,
    "contact.complained": COMPLAINED,
    "message.complained": COMPLAINED,
}
# A synthetic load test wrote into the same table; it is not anyone's address.
CMP_IGNORED_AUDIENCES = frozenset({"relay240-loadtest"})

MAILCHIMP_STATES = ("subscribed", "unsubscribed", "cleaned")


@dataclass(frozen=True)
class MailchimpLayout:
    """Where one shape of Mailchimp audience export keeps each fact."""

    name: str
    pattern: str  # glob per state, with ``{state}``
    email: str
    consent_at: tuple[str, ...]  # first non-empty wins
    opt_out_at: dict[str, str]  # per opt-out state
    changed_at: str | None  # the subscribed file's last change
    status: str | None  # a per-row status column to check against the file


MAILCHIMP_LAYOUTS = (
    # Audience -> Export Audience in the Mailchimp UI.
    MailchimpLayout(
        name="ui-export",
        pattern="{state}_email_audience_export_*.csv",
        email="Email Address",
        consent_at=("CONFIRM_TIME", "OPTIN_TIME"),
        opt_out_at={"unsubscribed": "UNSUB_TIME", "cleaned": "CLEAN_TIME"},
        changed_at="LAST_CHANGED",
        status=None,
    ),
    # A members dump through the Marketing API (``<audience>_<status>.csv``).
    # It has no unsubscribe or clean time; the member's ``last_changed`` is the
    # nearest one, because the status change is the member's last change.
    MailchimpLayout(
        name="members-api",
        pattern="*_{state}.csv",
        email="email_address",
        consent_at=("timestamp_opt", "timestamp_signup"),
        opt_out_at={"unsubscribed": "last_changed", "cleaned": "last_changed"},
        changed_at="last_changed",
        status="status",
    ),
)
RELAY_CONTACTS_FILE = "relay-contacts.csv"
RELAY_CATEGORY_FILE = "relay-category-preferences.csv"
RELAY_CONTACT_COLUMNS = frozenset(
    {
        "email",
        "audience",
        "client",
        "subscription_status",
        "subscription_created_at",
        "subscription_verified_at",
        "unsubscribed_at",
        "global_unsubscribed_at",
        "hard_bounced_at",
        "complained_at",
    }
)

WRITE_BATCH_SIZE = 500
_SHORT_OFFSET = re.compile(r"[+-]\d\d$")


class AudienceMergeError(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


@dataclass(frozen=True, order=True)
class Evidence:
    """One fact about one address from one source.  ``detail`` is never PII."""

    source: str
    kind: str
    at: datetime | None = None
    detail: str = ""


@dataclass(frozen=True)
class Verdict:
    status: str  # subscribed | unsubscribed | suppressed | no_evidence
    consent_sources: frozenset[str]
    opt_out_sources: frozenset[str]
    conflict: bool
    reconsent_after_opt_out: bool
    kinds: frozenset[str]


@dataclass
class Ledger:
    evidence: dict[str, set[Evidence]] = field(default_factory=lambda: defaultdict(set))
    inputs: dict[str, Any] = field(default_factory=dict)
    duplicates: Counter[str] = field(default_factory=Counter)

    def add(self, email: str | None, item: Evidence) -> None:
        key = normalize_email(email)
        if key:
            self.evidence[key].add(item)


def normalize_email(value: object) -> str:
    text = str(value or "").strip().lower()
    if "@" not in text or text.startswith("@") or text.endswith("@"):
        return ""
    return text


def parse_timestamp(value: object) -> datetime | None:
    """Read the sources' timestamps as aware UTC; a naive value is UTC (CMP, Mailchimp)."""

    text = str(value or "").strip()
    if not text:
        return None
    if _SHORT_OFFSET.search(text):
        text = f"{text}:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# --- sources -----------------------------------------------------------------


def read_cmp(path: Path, ledger: Ledger) -> None:
    uri = f"file:{path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise AudienceMergeError("cmp-export-unreadable") from error
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"courses_courseregistration", "courses_registrationcampaign"}
        if not required <= tables:
            raise AudienceMergeError("cmp-export-missing-registration-tables")
        counts: Counter[str] = Counter()
        per_email: Counter[str] = Counter()
        rows = connection.execute(
            "SELECT r.email_normalized, r.email, r.accepted_newsletter, r.email_confirmed, "
            "r.created_at, COALESCE(c.slug, '') "
            "FROM courses_courseregistration r "
            "LEFT JOIN courses_registrationcampaign c ON c.id = r.campaign_id"
        )
        for normalized, raw, accepted, confirmed, created_at, campaign in rows:
            email = normalized or raw
            key = normalize_email(email)
            if not key:
                counts["registrations_without_email"] += 1
                continue
            per_email[key] += 1
            if not accepted:
                kind = "cmp_registration_without_box"
            elif not confirmed:
                kind = "cmp_consent_unconfirmed_email"
            else:
                kind = CONSENT
            counts[f"registrations_{kind}"] += 1
            ledger.add(
                key, Evidence("cmp", kind, parse_timestamp(created_at), f"course:{campaign}")
            )
        counts["registrations"] = sum(per_email.values())
        counts["registration_emails"] = len(per_email)
        ledger.duplicates["cmp_emails_with_several_registrations"] = sum(
            1 for value in per_email.values() if value > 1
        )

        if "data_datamailercontactevent" in tables:
            events = connection.execute(
                "SELECT email, event_type, occurred_at, COALESCE(audience, '') "
                "FROM data_datamailercontactevent"
            )
            for email, event_type, occurred_at, audience in events:
                opt_out_kind = CMP_OPT_OUT_EVENTS.get(event_type)
                if opt_out_kind is None or audience in CMP_IGNORED_AUDIENCES:
                    continue
                counts[f"datamailer_{opt_out_kind}_events"] += 1
                ledger.add(
                    email,
                    Evidence(
                        "cmp",
                        opt_out_kind,
                        parse_timestamp(occurred_at),
                        f"datamailer:{event_type}",
                    ),
                )
        else:
            counts["datamailer_events_table_missing"] = 1

        if "accounts_customuser" in tables:
            accounts = {
                normalize_email(row[0])
                for row in connection.execute("SELECT email FROM accounts_customuser")
            }
            accounts.discard("")
            counts["accounts"] = len(accounts)
            counts["accounts_without_any_registration"] = len(accounts - set(per_email))
    except sqlite3.Error as error:
        raise AudienceMergeError("cmp-export-unreadable") from error
    finally:
        connection.close()
    ledger.inputs["cmp"] = {"file": path.name, **dict(sorted(counts.items()))}


def _mailchimp_files(directory: Path) -> tuple[MailchimpLayout, dict[str, Path]]:
    found: list[tuple[MailchimpLayout, dict[str, Path]]] = []
    for layout in MAILCHIMP_LAYOUTS:
        files: dict[str, Path] = {}
        for state in MAILCHIMP_STATES:
            matches = sorted(directory.glob(layout.pattern.format(state=state)))
            if len(matches) > 1:
                raise AudienceMergeError(f"mailchimp-export-ambiguous-{state}")
            if matches:
                files[state] = matches[0]
        if files:
            found.append((layout, files))
    if len(found) > 1:
        raise AudienceMergeError("mailchimp-export-mixed-layouts")
    if not found:
        raise AudienceMergeError("mailchimp-export-missing-subscribed")
    layout, files = found[0]
    for state in MAILCHIMP_STATES:
        if state not in files:
            raise AudienceMergeError(f"mailchimp-export-missing-{state}")
    return layout, files


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def _first_timestamp(row: dict[str, str], columns: Iterable[str]) -> datetime | None:
    for column in columns:
        at = parse_timestamp(row.get(column))
        if at is not None:
            return at
    return None


def read_mailchimp(directory: Path, ledger: Ledger) -> None:
    layout, files = _mailchimp_files(directory)
    counts: Counter[str] = Counter()
    latest = None
    for state, path in files.items():
        # The list id (UI export) or the audience name (API dump); never PII.
        if layout.name == "ui-export":
            list_label = path.stem.rsplit("_", 1)[-1]
        else:
            list_label = path.stem.rsplit("_", 1)[0]
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            time_column = layout.opt_out_at.get(state, layout.consent_at[0])
            needed = {layout.email, time_column} | ({layout.status} if layout.status else set())
            if needed - set(reader.fieldnames or ()):
                raise AudienceMergeError(f"mailchimp-export-missing-columns-{state}")
            for row in reader:
                email = row.get(layout.email)
                if not normalize_email(email):
                    counts[f"{state}_without_email"] += 1
                    continue
                if layout.status and (row.get(layout.status) or "").strip() != state:
                    raise AudienceMergeError(f"mailchimp-export-status-mismatch-{state}")
                counts[state] += 1
                detail = f"list:{list_label}"
                if state == "subscribed":
                    at = _first_timestamp(row, layout.consent_at)
                    ledger.add(email, Evidence("mailchimp", CONSENT, at, detail))
                    changed = parse_timestamp(row.get(layout.changed_at or ""))
                else:
                    at = parse_timestamp(row.get(layout.opt_out_at[state]))
                    kind = UNSUBSCRIBED if state == "unsubscribed" else BOUNCED
                    ledger.add(email, Evidence("mailchimp", kind, at, detail))
                    changed = at
                if changed is not None and (latest is None or changed > latest):
                    latest = changed
    ledger.inputs["mailchimp"] = {
        "directory": directory.name,
        "layout": layout.name,
        "latest_member_change": latest.date().isoformat() if latest else None,
        **dict(sorted(counts.items())),
    }


def read_relay(directory: Path, ledger: Ledger) -> None:
    contacts_path = directory / RELAY_CONTACTS_FILE
    if not contacts_path.is_file():
        raise AudienceMergeError("relay-export-missing-contacts-file")
    counts: Counter[str] = Counter()
    emails: set[str] = set()
    with contacts_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = RELAY_CONTACT_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise AudienceMergeError("relay-export-missing-columns")
        for row in reader:
            email = normalize_email(row.get("email"))
            if not email:
                counts["rows_without_email"] += 1
                continue
            emails.add(email)
            audience = (row.get("audience") or "").strip()
            client = (row.get("client") or "").strip()
            scope = f"{audience or '-'}/{client or '-'}"
            for column, kind in (
                ("global_unsubscribed_at", UNSUBSCRIBED),
                ("hard_bounced_at", BOUNCED),
                ("complained_at", COMPLAINED),
            ):
                at = parse_timestamp(row.get(column))
                if at is not None:
                    ledger.add(email, Evidence("relay", kind, at, f"contact:{column}"))
            status = (row.get("subscription_status") or "").strip().lower()
            if not status:
                continue
            counts[f"subscriptions_{audience or '-'}_{status}"] += 1
            if status == "unsubscribed":
                at = parse_timestamp(row.get("unsubscribed_at"))
                ledger.add(email, Evidence("relay", UNSUBSCRIBED, at, f"subscription:{scope}"))
            elif status == "subscribed" and audience == NEWSLETTER_AUDIENCE:
                at = parse_timestamp(row.get("subscription_verified_at")) or parse_timestamp(
                    row.get("subscription_created_at")
                )
                ledger.add(email, Evidence("relay", CONSENT, at, f"subscription:{scope}"))
            elif status == "subscribed":
                at = parse_timestamp(row.get("subscription_created_at"))
                ledger.add(
                    email,
                    Evidence(
                        "relay", "relay_course_audience_subscribed", at, f"subscription:{scope}"
                    ),
                )
            elif status == "pending":
                at = parse_timestamp(row.get("subscription_created_at"))
                ledger.add(email, Evidence("relay", "relay_pending", at, f"subscription:{scope}"))
    counts["contacts"] = len(emails)

    category_path = directory / RELAY_CATEGORY_FILE
    if category_path.is_file():
        for row in _csv_rows(category_path):
            if (row.get("enabled") or "").strip().lower() in {"f", "false", "0", "no"}:
                tag = (row.get("tag") or "").strip()
                counts[f"category_opt_out_{tag}"] += 1
                ledger.add(
                    row.get("email"),
                    Evidence(
                        "relay",
                        "relay_course_category_opt_out",
                        parse_timestamp(row.get("updated_at")),
                        f"category:{tag}",
                    ),
                )
    else:
        counts["category_preferences_file_missing"] = 1
    ledger.inputs["relay"] = {"directory": directory.name, **dict(sorted(counts.items()))}


@dataclass(frozen=True)
class SiteAccount:
    pk: int
    email: str
    subscribed: bool
    stamped: bool
    identity_state: str


def read_site(ledger: Ledger) -> list[SiteAccount]:
    from community_base.events.models import EventRegistration
    from django.contrib.auth import get_user_model

    from accounts_ext.models import IdentityState

    states = dict(IdentityState.objects.values_list("user_id", "identity_state"))
    accounts: list[SiteAccount] = []
    counts: Counter[str] = Counter()
    per_email: Counter[str] = Counter()
    users = (
        get_user_model()
        .objects.order_by("pk")
        .values_list("pk", "email", "newsletter_subscribed", "newsletter_preference_changed_at")
    )
    for pk, email, subscribed, changed_at in users.iterator(chunk_size=2000):
        key = normalize_email(email)
        state = states.get(pk, IdentityState.States.LEGACY)
        counts["accounts"] += 1
        counts[f"accounts_newsletter_subscribed_{bool(subscribed)}"] += 1
        if not key:
            counts["accounts_without_email"] += 1
            continue
        per_email[key] += 1
        accounts.append(SiteAccount(pk, key, bool(subscribed), changed_at is not None, state))
        if changed_at is not None:
            counts["accounts_with_own_decision"] += 1
            # A stamped "no" is either an account-page opt-out or an event
            # registration whose box was left unticked; the site cannot tell
            # which, so it is recorded but is not an unsubscribe.
            kind = CONSENT if subscribed else "site_declined"
            ledger.add(key, Evidence("site", kind, changed_at, "account:own-decision"))
    ledger.duplicates["site_emails_shared_by_several_accounts"] = sum(
        1 for value in per_email.values() if value > 1
    )
    counts["accounts_absorbed"] = sum(1 for a in accounts if a.identity_state == "absorbed")
    counts["accounts_quarantined"] = sum(1 for a in accounts if a.identity_state == "quarantined")

    registrations = EventRegistration.objects.filter(newsletter_consent=True).values_list(
        "normalized_email", "newsletter_consented_at", "newsletter_consent_source"
    )
    for email, consented_at, source in registrations.iterator(chunk_size=2000):
        counts["event_registration_consents"] += 1
        ledger.add(email, Evidence("site", CONSENT, consented_at, f"event:{source or 'unknown'}"))
    ledger.inputs["site"] = dict(sorted(counts.items()))
    return accounts


# --- rules -------------------------------------------------------------------


def decide(evidence: Iterable[Evidence]) -> Verdict:
    items = list(evidence)
    kinds = frozenset(item.kind for item in items)
    consent = [item for item in items if item.kind == CONSENT]
    opt_outs = [item for item in items if item.kind in OPT_OUTS]
    consent_sources = frozenset(item.source for item in consent)
    opt_out_sources = frozenset(item.source for item in opt_outs)
    if kinds & {BOUNCED, COMPLAINED}:
        status = "suppressed"
    elif UNSUBSCRIBED in kinds:
        status = "unsubscribed"
    elif consent:
        status = "subscribed"
    else:
        status = "no_evidence"
    conflict = bool(opt_outs) and bool(consent_sources - opt_out_sources)
    latest_consent = max((i.at for i in consent if i.at is not None), default=None)
    latest_opt_out = max((i.at for i in opt_outs if i.at is not None), default=None)
    reconsent = bool(latest_consent and latest_opt_out and latest_consent > latest_opt_out)
    return Verdict(status, consent_sources, opt_out_sources, conflict, reconsent, kinds)


def subscribed_bucket(verdict: Verdict) -> str:
    return "+".join(source for source in SOURCES if source in verdict.consent_sources)


def relay_import_row(email: str, verdict: Verdict) -> dict[str, str] | None:
    if verdict.status == "no_evidence":
        return None
    tags = [f"consent-{source}" for source in SOURCES if source in verdict.consent_sources]
    if verdict.status == "subscribed":
        return {
            "email": email,
            "subscription_status": "subscribed",
            "verified": "true",
            "unsubscribed": "false",
            "global_unsubscribed": "false",
            "hard_bounced": "false",
            "complained": "false",
            "unsubscribe_reason": "",
            "tags": ";".join(tags),
        }
    reason_sources = "+".join(s for s in SOURCES if s in verdict.opt_out_sources)
    return {
        "email": email,
        "subscription_status": "unsubscribed",
        "verified": "false",
        "unsubscribed": "true",
        "global_unsubscribed": "false",
        "hard_bounced": "true" if BOUNCED in verdict.kinds else "false",
        "complained": "true" if COMPLAINED in verdict.kinds else "false",
        "unsubscribe_reason": f"audience-merge:{verdict.status}:{reason_sources}",
        "tags": ";".join(tags),
    }


def site_target(account: SiteAccount, verdict: Verdict | None) -> bool | None:
    """The value the account should hold, or ``None`` to leave it alone."""

    if account.identity_state in {"absorbed", "quarantined"}:
        return None
    status = verdict.status if verdict is not None else "no_evidence"
    if status in {"suppressed", "unsubscribed"}:
        return False
    if account.stamped:
        return None
    return status == "subscribed"


# --- run ---------------------------------------------------------------------


def _evidence_record(email: str, verdict: Verdict, items: Iterable[Evidence]) -> dict[str, Any]:
    latest: dict[tuple[str, str], Evidence] = {}
    for item in items:
        key = (item.source, item.kind)
        current = latest.get(key)
        if current is None or (item.at or datetime.min.replace(tzinfo=UTC)) > (
            current.at or datetime.min.replace(tzinfo=UTC)
        ):
            latest[key] = item
    return {
        "email": email,
        "status": verdict.status,
        "conflict": verdict.conflict,
        "reconsent_after_opt_out": verdict.reconsent_after_opt_out,
        "latest": [
            {
                "source": item.source,
                "kind": item.kind,
                "at": item.at.isoformat() if item.at else None,
                "detail": item.detail,
            }
            for _, item in sorted(latest.items())
        ],
    }


def _private_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents:
        raise AudienceMergeError("output-dir-inside-repository")
    resolved.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved, 0o700)
    return resolved


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # an existing file keeps its old mode otherwise
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def run(
    *,
    cmp_export: Path | None,
    mailchimp_export_dir: Path | None,
    relay_export_dir: Path | None,
    output_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    from django.contrib.auth import get_user_model
    from django.db import transaction

    output = _private_directory(output_dir)
    ledger = Ledger()
    if cmp_export is not None:
        read_cmp(cmp_export, ledger)
    else:
        ledger.inputs["cmp"] = "missing"
    if mailchimp_export_dir is not None:
        read_mailchimp(mailchimp_export_dir, ledger)
    else:
        ledger.inputs["mailchimp"] = "missing"
    if relay_export_dir is not None:
        read_relay(relay_export_dir, ledger)
    else:
        ledger.inputs["relay"] = "missing"
    accounts = read_site(ledger)

    verdicts = {email: decide(items) for email, items in ledger.evidence.items()}
    status_counts: Counter[str] = Counter(v.status for v in verdicts.values())
    subscribed_by_source: Counter[str] = Counter(
        subscribed_bucket(v) for v in verdicts.values() if v.status == "subscribed"
    )
    no_evidence_reasons: Counter[str] = Counter(
        "+".join(sorted(v.kinds)) or "none" for v in verdicts.values() if v.status == "no_evidence"
    )
    opt_out_kind_by_source: Counter[str] = Counter()
    for items in ledger.evidence.values():
        for source, kind in {(i.source, i.kind) for i in items if i.kind in OPT_OUTS}:
            opt_out_kind_by_source[f"{source}:{kind}"] += 1
    conflicts: Counter[str] = Counter()
    for v in verdicts.values():
        if v.conflict:
            conflicts["consent_in_one_source_opt_out_in_another"] += 1
            pair = (
                "+".join(s for s in SOURCES if s in v.consent_sources - v.opt_out_sources)
                + " vs "
                + "+".join(s for s in SOURCES if s in v.opt_out_sources)
            )
            conflicts[f"consent {pair}"] += 1
        if v.reconsent_after_opt_out:
            conflicts["consent_newer_than_latest_opt_out"] += 1
        if v.status == "subscribed" and "site_declined" in v.kinds:
            conflicts["site_declined_but_subscribed_elsewhere"] += 1

    account_emails = {a.email for a in accounts}
    subscribed = {e for e, v in verdicts.items() if v.status == "subscribed"}
    summary = {
        "both_cmp_and_relay_subscribed": sum(
            1 for e in subscribed if {"cmp", "relay"} <= verdicts[e].consent_sources
        ),
        "cmp_only": subscribed_by_source.get("cmp", 0),
        "relay_only": subscribed_by_source.get("relay", 0),
        "mailchimp_only": subscribed_by_source.get("mailchimp", 0),
        "site_only": subscribed_by_source.get("site", 0),
        "subscribed_total": status_counts.get("subscribed", 0),
        "unsubscribed_somewhere": status_counts.get("unsubscribed", 0),
        "bounced_or_complained": status_counts.get("suppressed", 0),
        "conflicting": conflicts.get("consent_in_one_source_opt_out_in_another", 0),
        "no_consent_evidence": status_counts.get("no_evidence", 0),
        "addresses_total": len(verdicts),
    }

    changes: dict[str, list[int]] = {"to_true": [], "to_false": []}
    kept_own_decision = 0
    skipped_identity = 0
    for account in accounts:
        target = site_target(account, verdicts.get(account.email))
        if target is None:
            if account.identity_state in {"absorbed", "quarantined"}:
                skipped_identity += 1
            else:
                kept_own_decision += 1
            continue
        if target != account.subscribed:
            changes["to_true" if target else "to_false"].append(account.pk)

    if apply:
        user_model = get_user_model()
        with transaction.atomic():
            for value, key in ((True, "to_true"), (False, "to_false")):
                ids = changes[key]
                for start in range(0, len(ids), WRITE_BATCH_SIZE):
                    batch = list(
                        user_model.objects.filter(pk__in=ids[start : start + WRITE_BATCH_SIZE])
                    )
                    for user in batch:
                        user.newsletter_subscribed = value
                    user_model.objects.bulk_update(batch, ["newsletter_subscribed"])

    rows = [
        row
        for email in sorted(verdicts)
        if (row := relay_import_row(email, verdicts[email])) is not None
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=RELAY_IMPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _write_private(output / "relay-import-dtc.csv", buffer.getvalue())
    _write_private(
        output / "evidence.jsonl",
        "".join(
            json.dumps(
                _evidence_record(email, verdicts[email], ledger.evidence[email]), sort_keys=True
            )
            + "\n"
            for email in sorted(verdicts)
        ),
    )
    _write_private(
        output / "site-account-changes.json",
        json.dumps({"applied": apply, **changes}, indent=2, sort_keys=True) + "\n",
    )

    report: dict[str, Any] = {
        "applied": apply,
        "inputs": ledger.inputs,
        "buckets": summary,
        "status": dict(sorted(status_counts.items())),
        "subscribed_by_consent_sources": dict(sorted(subscribed_by_source.items())),
        "opt_outs_by_source_and_kind": dict(sorted(opt_out_kind_by_source.items())),
        "conflicts": dict(sorted(conflicts.items())),
        "no_evidence_reasons": dict(sorted(no_evidence_reasons.items())),
        "duplicates": dict(sorted(ledger.duplicates.items())),
        "site_accounts": {
            "subscribed_with_account": len(subscribed & account_emails),
            "subscribed_newsletter_only_contacts": len(subscribed - account_emails),
            "to_true": len(changes["to_true"]),
            "to_false": len(changes["to_false"]),
            "kept_own_decision": kept_own_decision,
            "skipped_absorbed_or_quarantined": skipped_identity,
        },
        "relay_import": {
            **RELAY_IMPORT_TARGET,
            "rows": len(rows),
            "subscribed": sum(1 for r in rows if r["subscription_status"] == "subscribed"),
            "unsubscribed": sum(1 for r in rows if r["subscription_status"] == "unsubscribed"),
        },
    }
    _write_private(output / "report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_target_arguments(parser)
    parser.add_argument("--cmp-export", type=Path, default=None, help="CMP SQLite export.")
    parser.add_argument(
        "--mailchimp-export-dir",
        type=Path,
        default=None,
        help="Directory with Mailchimp's subscribed/unsubscribed/cleaned CSVs.",
    )
    parser.add_argument(
        "--relay-export-dir",
        type=Path,
        default=None,
        help=f"Directory with {RELAY_CONTACTS_FILE} (and optionally {RELAY_CATEGORY_FILE}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "A fresh directory outside the repository, "
            "e.g. ~/prod/dtc-data/email-audience/run-<ts>."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write User.newsletter_subscribed. Without it no database row is written.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if (
        args.cmp_export is None
        and args.mailchimp_export_dir is None
        and args.relay_export_dir is None
    ):
        print(json.dumps({"error": "no-source-selected"}, indent=2))
        return 1
    for name in ("cmp_export", "mailchimp_export_dir", "relay_export_dir"):
        value = getattr(args, name)
        if value is not None:
            value = value.expanduser()
            exists = value.is_file() if name == "cmp_export" else value.is_dir()
            if not exists:
                print(json.dumps({"error": f"{name.replace('_', '-')}-not-found"}, indent=2))
                return 1
            setattr(args, name, value)
    configure_target(parser, args)
    try:
        report = run(
            cmp_export=args.cmp_export,
            mailchimp_export_dir=args.mailchimp_export_dir,
            relay_export_dir=args.relay_export_dir,
            output_dir=args.output_dir,
            apply=args.apply,
        )
    except AudienceMergeError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
