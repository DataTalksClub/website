"""Backfill historical course-cohort membership from Mailchimp's export tags.

Reads a Mailchimp audience export's **subscribed** CSV only -- the same file
``events.mailchimp_tag_import`` and ``accounts.services.mailchimp_subscription_import``
read -- and turns a small, fixed vocabulary of course-cohort tags
(``de-zoomcamp-2025``, ``ml-zoomcamp-1``, and the like) into
:class:`courses.models.Enrollment` rows against **cohorts that already exist**
in this database. ``courses`` and ``events`` are sibling application-service
apps (see ``_docs/architecture/app-boundaries.md``) that do not import one
another, so this module does not import ``events.mailchimp_tag_import`` --
the small CSV-reading boilerplate it needs is duplicated here, the same way
``accounts.services.mailchimp_subscription_import`` and
``events.mailchimp_tag_import`` each already duplicate it rather than sharing
a base module.

**Not ``CourseInterest``.** DataTalksClub/website#286 gates a *different*,
still-undecided concept: pre-Cohort interest in a reusable ``Course`` family
with no specific ``Cohort`` yet. Every tag this module reads names a cohort
that already happened and already has a real ``Cohort`` row (from the CMP and
pre-2024 legacy-zoomcamp imports) -- a settled fact, not a design question --
so #286 does not block this module and nothing here creates a
``CourseInterest``.

**Which tags, and which cohort each one means.** :data:`TAG_COHORT_MAP` is a
small, reviewed, hardcoded table -- not derived mechanically from the tag
string -- because several of these tags are *ordinal* (``de-zoomcamp-1``,
``ml-zoomcamp-2``) and predate each course's switch to year-named tags; the
owner resolved the exact year each one means, cross-checked against the real
pre-2023 editions ``scripts/prod/legacy_zoomcamp/editions.py`` actually
imported. This is exactly the situation
``events.mailchimp_event_tag_categories`` describes for its own reviewed
table: "a fixed vocabulary ... is exactly the case a reviewed table is for."
``courses.services.course_family_identity.family_and_year_from_edition_slug``
(the mechanical ``<family>-<year>`` splitter ``cmp_content_import`` uses) is
deliberately not reused here -- it cannot parse an ordinal tag at all, and
silently guessing a year for one is exactly what the owner's review exists to
prevent. A tag not present in :data:`TAG_COHORT_MAP` is out of this module's
vocabulary entirely and is never read for anything beyond the membership
check that excludes it -- the same treatment
``events.mailchimp_tag_import`` gives a course-cohort tag from its side.
``sma-zoomcamp`` carries no tags in the export by design (owner-confirmed:
its registrations live on an external form) and is correctly absent from the
table.

**Cohort resolution never guesses.** A tag's ``(family_slug, year)`` is
looked up against ``Cohort.objects.filter(course__slug=family_slug,
year=year)`` (the ``courses_cohort_course_year_unique`` constraint makes this
lookup unambiguous). A tag whose cohort does not exist in this database is
skipped and counted under :attr:`MailchimpCourseTagImportReport.tags_missing_cohort`
-- never assigned to the nearest year, never used to create a new ``Cohort``.
This module reconciles against cohorts other importers already wrote; it does
not bootstrap the course catalogue (see ``scripts/prod/__init__.py``'s
``BOOTSTRAPPING_ENTRY_POINTS`` and the warning in its module docstring about
what happens when a reconciler runs before its upstream).

**Why only ``Enrollment``, never ``CourseRegistration``.** The two historical
importers this task was told to match handle the registration/enrollment
split differently, and the difference tracks exactly what their source data
carries:

* ``courses.services.cmp_learner_history_import`` writes *both*, because
  CMP's export contains real, per-row ``CourseRegistration`` facts --
  a real ``campaign_id``, name, role, country, region, the marketing intake
  a person actually typed into a real ``RegistrationCampaign`` form.
* ``scripts/prod/legacy_zoomcamp`` writes *only* ``Enrollment``
  (``get_or_create_enrollment`` in ``scripts/prod/legacy_zoomcamp/identity.py``)
  because its source -- historical scoring data -- carries no such facts at
  all, just "this learner did homework in this cohort."

A Mailchimp course-cohort tag is the second shape, not the first: it carries
no campaign, no name, no role, no country, no region -- nothing a
``CourseRegistration`` row needs beyond the email address. ``CourseRegistration.campaign``
is a required, non-nullable foreign key to ``RegistrationCampaign`` -- a real,
often publicly-routable marketing form (see ``courses/views/registration.py``)
that rotates which cohort it currently promotes. There is no fact in this
export saying which campaign, if any, a given historical row "registered
through," and a ``RegistrationCampaign`` is not a plain foreign-key anchor to
invent for provenance-tagging purposes -- minting one would put a fake
marketing form in front of a table every course page reads. So this module
follows the legacy-zoomcamp shape, not the CMP shape: it writes only
``Enrollment`` rows, and writes no ``CourseRegistration`` at all. This is a
deliberate divergence from a *literal* "create a CourseRegistration" reading,
grounded in which of the two existing precedents this source data actually
matches -- see the runbook entry this module implements,
``_docs/runbooks/ingest-script-inventory.md`` (course tags section), for the
settled tag table this module's :data:`TAG_COHORT_MAP` transcribes.

**Identity resolution, and why it is narrower than the event-tag importer's.**
``events.mailchimp_tag_import`` resolves a row against ``accounts_customuser``
first, then a prior ``EventRegistrantIdentity``, and only then creates a new,
login-incapable registrant-only identity -- because ``EventRegistration`` and
``EventRegistrantInterestSignal`` both point at that identity type, which can
represent a real person with no account at all. ``courses`` has no equivalent
registrant-only identity model, and ``Enrollment.student`` is a required,
non-nullable foreign key to a real ``CustomUser`` -- there is no schema slot
for a login-incapable course identity to land in. Rather than invent one, or
mint a new ``CustomUser`` account from a self-selected marketing tag (a much
lower-confidence signal than CMP's verified learner export), this module
matches *only* against an existing account by ``normalized_email`` --
the exact same restraint ``accounts.services.mailchimp_subscription_import``
already applies to its own, adjacent problem ("this importer only ever
updates an existing row"). A tag row with no matching account is counted
under :attr:`MailchimpCourseTagImportReport.no_account_match_total` and
skipped -- nothing is created for it, and no ``CustomUser`` is ever created by
this module, full stop.

**Idempotency.** ``Enrollment`` carries a ``(student, course)`` unique
constraint (``unique_together``); writing goes through ``get_or_create``, the
same call ``scripts/prod/legacy_zoomcamp/identity.py`` makes for the same
reason. A replayed row that resolves to the same account and the same cohort
writes nothing new on its second pass.

**Privacy.** Only ``Email Address`` and ``TAGS`` are ever read from a row --
the same two columns ``events.mailchimp_tag_import`` reads and no more.
``OPTIN_IP``, ``CONFIRM_IP``, ``NOTES`` and every other column of the export
are never opened here.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser
from courses.models import Cohort, Enrollment

__all__ = [
    "EMAIL_COLUMN",
    "TAGS_COLUMN",
    "TAG_COHORT_MAP",
    "MailchimpCourseTagImportError",
    "MailchimpCourseTagImportReport",
    "parse_mailchimp_tags",
    "import_mailchimp_course_tags",
]

# The two columns this importer ever reads. Every other column Mailchimp's
# export carries (OPTIN_IP, CONFIRM_IP, NOTES, MEMBER_RATING, and the rest) is
# never opened here -- same minimization default as the sibling importers.
EMAIL_COLUMN = "Email Address"
TAGS_COLUMN = "TAGS"

# The settled tag -> (course-family slug, cohort year) table. See the module
# docstring for the full source and reasoning -- transcribed from
# `_docs/runbooks/ingest-script-inventory.md` (course tags section), reviewed
# by the owner, not derived. sma-zoomcamp carries no tags in the export by
# design and is correctly absent.
TAG_COHORT_MAP: Mapping[str, tuple[str, int]] = {
    "de-zoomcamp-1": ("de-zoomcamp", 2022),
    "de-zoomcamp": ("de-zoomcamp", 2022),
    "de-zoomcamp-2": ("de-zoomcamp", 2023),
    "de-zoomcamp-2024": ("de-zoomcamp", 2024),
    "de-zoomcamp-2025": ("de-zoomcamp", 2025),
    "de-zoomcamp-2026": ("de-zoomcamp", 2026),
    "ml-zoomcamp-1": ("ml-zoomcamp", 2021),
    "ml-zoomcamp": ("ml-zoomcamp", 2021),
    "ml-zoomcamp-2": ("ml-zoomcamp", 2022),
    "ml-zoomcamp-2023": ("ml-zoomcamp", 2023),
    "ml-zoomcamp-2024": ("ml-zoomcamp", 2024),
    "ml-zoomcamp-2025": ("ml-zoomcamp", 2025),
    "mlops-zoomcamp-1": ("mlops-zoomcamp", 2022),
    "mlops-zoomcamp": ("mlops-zoomcamp", 2022),
    "mlops-zoomcamp-2023": ("mlops-zoomcamp", 2023),
    "mlops-zoomcamp-2024": ("mlops-zoomcamp", 2024),
    "mlops-zoomcamp-2025": ("mlops-zoomcamp", 2025),
    "llm-zoomcamp-2024": ("llm-zoomcamp", 2024),
    "llm-zoomcamp-2025": ("llm-zoomcamp", 2025),
    "llm-zoomcamp-2026": ("llm-zoomcamp", 2026),
    "ai-dev-tools-zoomcamp-2025": ("ai-dev-tools-zoomcamp", 2025),
}


class MailchimpCourseTagImportError(RuntimeError):
    """A fail-closed refusal that never carries a source value."""


def parse_mailchimp_tags(raw: str) -> tuple[str, ...]:
    """Split one row's ``TAGS`` cell into its individual tag strings.

    Mailchimp's export nests each tag in its own double quotes *inside* the
    cell, rather than emitting a plain comma list -- a raw ``TAGS`` value
    reads literally as ``"event","de-zoomcamp-2026"`` (quote characters
    included, as row data, not CSV structure). Splitting on ``,`` and
    stripping one layer of surrounding double quotes (plus incidental
    whitespace) from each piece recovers the clean tag strings. Duplicated
    from ``events.mailchimp_tag_import.parse_mailchimp_tags`` rather than
    imported -- see the module docstring for why ``courses`` does not import
    ``events``.
    """

    if not raw:
        return ()
    pieces = (segment.strip().strip('"').strip() for segment in raw.split(","))
    return tuple(piece for piece in pieces if piece)


def _rows(path: Path) -> Iterator[dict[str, str]]:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError:
        raise MailchimpCourseTagImportError("source-unreadable") from None
    with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        if fieldnames is None or EMAIL_COLUMN not in fieldnames or TAGS_COLUMN not in fieldnames:
            raise MailchimpCourseTagImportError("source-missing-required-column")
        yield from reader


@dataclass(frozen=True, slots=True)
class MailchimpCourseTagImportReport:
    source_rows: int
    rows_with_course_tag: int
    rows_by_tag: dict[str, int]
    tags_missing_cohort: dict[str, int]
    matched_account_total: int
    no_account_match_total: int
    enrollments_created_total: int
    enrollments_already_present_total: int
    applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_rows": self.source_rows,
            "rows_with_course_tag": self.rows_with_course_tag,
            "rows_by_tag": dict(self.rows_by_tag),
            # Tags whose (family, year) has no matching Cohort in this
            # database, with how many rows that affected -- never guessed at,
            # always reported so a human can decide whether the cohort still
            # needs importing.
            "tags_missing_cohort": dict(self.tags_missing_cohort),
            "matched_account_total": self.matched_account_total,
            "no_account_match_total": self.no_account_match_total,
            "enrollments_created_total": self.enrollments_created_total,
            "enrollments_already_present_total": self.enrollments_already_present_total,
            "applied": self.applied,
        }


def import_mailchimp_course_tags(
    *, subscribed: Path, apply: bool = True
) -> MailchimpCourseTagImportReport:
    """Backfill ``Enrollment`` rows from the subscribed export's course tags.

    With ``apply=False`` (dry run), every count -- including which cohort a
    tag resolves to, which account a row matches, and whether the resulting
    enrollment would be new -- is computed against the real, current database
    state through read-only queries only; nothing is written. With
    ``apply=True``, matched rows are written through
    ``Enrollment.objects.get_or_create``, so a replayed row is a no-op.

    Only rows carrying at least one tag from :data:`TAG_COHORT_MAP` are looked
    at beyond the tag check itself. A tag whose cohort does not exist in this
    database is skipped and counted, never guessed at. A row whose email does
    not match an existing account is skipped and counted -- this module never
    creates a ``CustomUser``. See the module docstring for the full reasoning.
    """

    source_rows = 0
    rows_with_course_tag = 0
    rows_by_tag: dict[str, int] = {tag: 0 for tag in TAG_COHORT_MAP}
    tags_missing_cohort: dict[str, int] = {}
    matched_account = 0
    no_account_match = 0
    enrollments_created = 0
    enrollments_already_present = 0

    cohort_cache: dict[tuple[str, int], Cohort | None] = {}

    def _resolve_cohort(family_slug: str, year: int) -> Cohort | None:
        key = (family_slug, year)
        if key not in cohort_cache:
            cohort_cache[key] = (
                Cohort.objects.filter(course__slug=family_slug, year=year).order_by("pk").first()
            )
        return cohort_cache[key]

    for row in _rows(subscribed):
        source_rows += 1
        tags = parse_mailchimp_tags(row.get(TAGS_COLUMN, ""))
        present_tags = tuple(dict.fromkeys(tag for tag in tags if tag in TAG_COHORT_MAP))
        if not present_tags:
            continue
        normalized_email = normalize_account_email(row.get(EMAIL_COLUMN))
        if normalized_email is None:
            continue

        rows_with_course_tag += 1
        for tag in present_tags:
            rows_by_tag[tag] += 1

        resolved_cohorts: dict[Any, Cohort] = {}
        for tag in present_tags:
            family_slug, year = TAG_COHORT_MAP[tag]
            cohort = _resolve_cohort(family_slug, year)
            if cohort is None:
                tags_missing_cohort[tag] = tags_missing_cohort.get(tag, 0) + 1
                continue
            resolved_cohorts[cohort.pk] = cohort
        if not resolved_cohorts:
            continue

        account = (
            CustomUser.objects.filter(normalized_email=normalized_email).order_by("pk").first()
        )
        if account is None:
            no_account_match += 1
            continue
        matched_account += 1

        for cohort in resolved_cohorts.values():
            if apply:
                _, created = Enrollment.objects.get_or_create(student=account, course=cohort)
            else:
                created = not Enrollment.objects.filter(student=account, course=cohort).exists()
            if created:
                enrollments_created += 1
            else:
                enrollments_already_present += 1

    return MailchimpCourseTagImportReport(
        source_rows=source_rows,
        rows_with_course_tag=rows_with_course_tag,
        rows_by_tag=rows_by_tag,
        tags_missing_cohort=tags_missing_cohort,
        matched_account_total=matched_account,
        no_account_match_total=no_account_match,
        enrollments_created_total=enrollments_created,
        enrollments_already_present_total=enrollments_already_present,
        applied=apply,
    )
