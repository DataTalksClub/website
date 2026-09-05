"""Tests for the Mailchimp event-category-tag importer.

Every email used here is a synthetic ``example.invalid`` address -- no real
Mailchimp export is read in this suite. ``TAGS`` cell values are written the
same way the real export encodes them (each tag individually double-quoted
inside the cell, e.g. ``"event","event-conference"``), confirmed against a
real structural read of the export while building this importer.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from django.test import TestCase

from accounts.models import CustomUser
from events.mailchimp_event_tag_categories import MAILCHIMP_EVENT_TAG_CATEGORIES
from events.mailchimp_tag_import import (
    EMAIL_COLUMN,
    TAGS_COLUMN,
    import_mailchimp_event_tags,
    parse_mailchimp_tags,
)
from events.models import EventRegistrantIdentity, EventRegistrantInterestSignal


def _tags(*names: str) -> str:
    """Encode tag names the way Mailchimp's own export cell does."""

    return ",".join(f'"{name}"' for name in names)


def _write_csv(rows: list[dict[str, str]]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    fieldnames = [EMAIL_COLUMN, "Name", "MEMBER_RATING", TAGS_COLUMN]
    writer = csv.DictWriter(tmp, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.close()
    return Path(tmp.name)


class ParseMailchimpTagsTests(TestCase):
    def test_empty_cell_is_no_tags(self) -> None:
        self.assertEqual(parse_mailchimp_tags(""), ())

    def test_single_quoted_tag(self) -> None:
        self.assertEqual(parse_mailchimp_tags('"event"'), ("event",))

    def test_multiple_quoted_tags(self) -> None:
        self.assertEqual(
            parse_mailchimp_tags('"registered-in-slack","event","de-zoomcamp-2026"'),
            ("registered-in-slack", "event", "de-zoomcamp-2026"),
        )


class MailchimpEventTagImportTests(TestCase):
    def setUp(self) -> None:
        self.subscribed_path = _write_csv([])
        self.addCleanup(self.subscribed_path.unlink, missing_ok=True)

    def _run(self, rows: list[dict[str, str]], **kwargs):
        self.subscribed_path.unlink(missing_ok=True)
        self.subscribed_path = _write_csv(rows)
        return import_mailchimp_event_tags(subscribed=self.subscribed_path, **kwargs)

    def test_course_tag_only_is_completely_ignored(self) -> None:
        before_identities = EventRegistrantIdentity.objects.count()
        result = self._run(
            [{EMAIL_COLUMN: "course-only@example.invalid", TAGS_COLUMN: _tags("de-zoomcamp-2026")}]
        )
        self.assertEqual(EventRegistrantIdentity.objects.count(), before_identities)
        self.assertEqual(EventRegistrantInterestSignal.objects.count(), 0)
        self.assertEqual(result.rows_with_event_tag, 0)
        self.assertEqual(result.source_rows, 1)

    def test_dropped_tag_only_is_completely_ignored(self) -> None:
        before_identities = EventRegistrantIdentity.objects.count()
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "dropped-only@example.invalid",
                    TAGS_COLUMN: _tags("registered-in-slack"),
                }
            ]
        )
        self.assertEqual(EventRegistrantIdentity.objects.count(), before_identities)
        self.assertEqual(EventRegistrantInterestSignal.objects.count(), 0)
        self.assertEqual(result.rows_with_event_tag, 0)

    def test_event_tag_matching_existing_account_attaches_to_it(self) -> None:
        account = CustomUser.objects.create(
            username="existing-learner", email="learner@example.invalid"
        )
        result = self._run(
            [{EMAIL_COLUMN: "learner@example.invalid", TAGS_COLUMN: _tags("event-podcast")}]
        )

        self.assertEqual(result.matched_account_total, 1)
        self.assertEqual(result.matched_prior_identity_total, 0)
        self.assertEqual(result.new_identity_total, 0)
        identity = EventRegistrantIdentity.objects.get(account=account)
        self.assertIsNone(identity.normalized_email)
        signal = EventRegistrantInterestSignal.objects.get(identity=identity)
        self.assertEqual(signal.category, EventRegistrantInterestSignal.Category.PODCAST)
        self.assertEqual(signal.source, EventRegistrantInterestSignal.Source.MAILCHIMP_TAG)

    def test_event_tag_matching_existing_registrant_only_identity_reuses_it(self) -> None:
        prior = EventRegistrantIdentity.objects.create(normalized_email="attendee@example.invalid")
        result = self._run(
            [{EMAIL_COLUMN: "attendee@example.invalid", TAGS_COLUMN: _tags("event-conference")}]
        )

        self.assertEqual(result.matched_account_total, 0)
        self.assertEqual(result.matched_prior_identity_total, 1)
        self.assertEqual(result.new_identity_total, 0)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 1)
        signal = EventRegistrantInterestSignal.objects.get(identity=prior)
        self.assertEqual(signal.category, EventRegistrantInterestSignal.Category.CONFERENCE)

    def test_event_tag_matching_neither_creates_new_registrant_only_identity(self) -> None:
        result = self._run(
            [{EMAIL_COLUMN: "brand-new@example.invalid", TAGS_COLUMN: _tags("event-analytics")}]
        )

        self.assertEqual(result.matched_account_total, 0)
        self.assertEqual(result.matched_prior_identity_total, 0)
        self.assertEqual(result.new_identity_total, 1)
        identity = EventRegistrantIdentity.objects.get(normalized_email="brand-new@example.invalid")
        self.assertIsNone(identity.account)
        signal = EventRegistrantInterestSignal.objects.get(identity=identity)
        self.assertEqual(signal.category, EventRegistrantInterestSignal.Category.ANALYTICS)

    def test_all_eight_event_tags_map_to_the_reviewed_categories(self) -> None:
        for index, (tag, category) in enumerate(MAILCHIMP_EVENT_TAG_CATEGORIES.items()):
            email = f"tag-{index}@example.invalid"
            self._run([{EMAIL_COLUMN: email, TAGS_COLUMN: _tags(tag)}])
            identity = EventRegistrantIdentity.objects.get(normalized_email=email)
            signal = EventRegistrantInterestSignal.objects.get(identity=identity)
            self.assertEqual(signal.category, category)

    def test_multiple_event_tags_on_one_row_create_multiple_signals(self) -> None:
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "multi@example.invalid",
                    TAGS_COLUMN: _tags("event", "event-podcast", "event-conference"),
                }
            ]
        )
        identity = EventRegistrantIdentity.objects.get(normalized_email="multi@example.invalid")
        categories = set(
            EventRegistrantInterestSignal.objects.filter(identity=identity).values_list(
                "category", flat=True
            )
        )
        self.assertEqual(
            categories,
            {
                EventRegistrantInterestSignal.Category.GENERAL,
                EventRegistrantInterestSignal.Category.PODCAST,
                EventRegistrantInterestSignal.Category.CONFERENCE,
            },
        )
        self.assertEqual(result.signals_created_total, 3)

    def test_event_tag_alongside_a_course_tag_only_imports_the_event_tag(self) -> None:
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "mixed@example.invalid",
                    TAGS_COLUMN: _tags("de-zoomcamp-2026", "event-data", "registered-in-slack"),
                }
            ]
        )
        identity = EventRegistrantIdentity.objects.get(normalized_email="mixed@example.invalid")
        self.assertEqual(
            list(
                EventRegistrantInterestSignal.objects.filter(identity=identity).values_list(
                    "category", flat=True
                )
            ),
            [EventRegistrantInterestSignal.Category.DATA],
        )
        self.assertEqual(result.rows_by_tag["event-data"], 1)

    def test_rerun_is_idempotent(self) -> None:
        rows = [
            {EMAIL_COLUMN: "repeat@example.invalid", TAGS_COLUMN: _tags("events-soft")},
        ]
        first = self._run(rows)
        self.assertEqual(first.signals_created_total, 1)
        self.assertEqual(first.new_identity_total, 1)

        second = self._run(rows)
        self.assertEqual(second.signals_created_total, 0)
        self.assertEqual(second.signals_already_present_total, 1)
        self.assertEqual(second.matched_prior_identity_total, 1)
        self.assertEqual(second.new_identity_total, 0)

        self.assertEqual(EventRegistrantIdentity.objects.count(), 1)
        self.assertEqual(EventRegistrantInterestSignal.objects.count(), 1)

    def test_dry_run_reports_without_writing(self) -> None:
        before_identities = EventRegistrantIdentity.objects.count()
        before_signals = EventRegistrantInterestSignal.objects.count()
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "dry-run@example.invalid",
                    TAGS_COLUMN: _tags("events-data-science"),
                }
            ],
            apply=False,
        )
        self.assertEqual(EventRegistrantIdentity.objects.count(), before_identities)
        self.assertEqual(EventRegistrantInterestSignal.objects.count(), before_signals)
        self.assertEqual(result.new_identity_total, 1)
        self.assertEqual(result.signals_created_total, 1)
        self.assertFalse(result.applied)

    def test_dry_run_then_real_run_agree(self) -> None:
        rows = [
            {EMAIL_COLUMN: "agree@example.invalid", TAGS_COLUMN: _tags("event-production")},
        ]
        dry = self._run(rows, apply=False)
        real = self._run(rows)

        self.assertEqual(dry.new_identity_total, real.new_identity_total)
        self.assertEqual(dry.signals_created_total, real.signals_created_total)
