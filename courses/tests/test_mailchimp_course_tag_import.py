"""Tests for the Mailchimp course-cohort-tag backfill importer.

Every email used here is a synthetic ``example.invalid`` address -- no real
Mailchimp export is read in this suite. ``TAGS`` cell values are written the
same way the real export encodes them (each tag individually double-quoted
inside the cell), matching ``events/tests/test_mailchimp_tag_import.py``.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from django.test import TestCase

from accounts.models import CustomUser
from courses.models import Cohort, Course, CourseRegistration, Enrollment
from courses.services.mailchimp_course_tag_import import (
    EMAIL_COLUMN,
    TAG_COHORT_MAP,
    TAGS_COLUMN,
    import_mailchimp_course_tags,
    parse_mailchimp_tags,
)


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


def _cohort(family_slug: str, year: int) -> Cohort:
    family, _ = Course.objects.get_or_create(
        slug=family_slug, defaults={"title": family_slug.replace("-", " ").title()}
    )
    slug = f"{family_slug}-{year}"
    return Cohort.objects.create(
        course=family,
        slug=slug,
        identifier=str(year),
        year=year,
        title=f"{family.title} {year}",
        description="Seeded for test.",
    )


class ParseMailchimpTagsTests(TestCase):
    def test_empty_cell_is_no_tags(self) -> None:
        self.assertEqual(parse_mailchimp_tags(""), ())

    def test_single_quoted_tag(self) -> None:
        self.assertEqual(parse_mailchimp_tags('"de-zoomcamp-2025"'), ("de-zoomcamp-2025",))

    def test_multiple_quoted_tags(self) -> None:
        self.assertEqual(
            parse_mailchimp_tags('"event","de-zoomcamp-2026"'),
            ("event", "de-zoomcamp-2026"),
        )


class TagCohortMapTests(TestCase):
    """The settled tag -> (family, year) table, exactly as given."""

    EXPECTED: dict[str, tuple[str, int]] = {
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

    def test_map_matches_the_settled_table_exactly(self) -> None:
        self.assertEqual(dict(TAG_COHORT_MAP), self.EXPECTED)

    def test_sma_zoomcamp_is_not_in_the_map(self) -> None:
        self.assertFalse(any(family == "sma-zoomcamp" for family, _year in TAG_COHORT_MAP.values()))


class MailchimpCourseTagImportTests(TestCase):
    def setUp(self) -> None:
        self.subscribed_path = _write_csv([])
        self.addCleanup(self.subscribed_path.unlink, missing_ok=True)

    def _run(self, rows: list[dict[str, str]], **kwargs):
        self.subscribed_path.unlink(missing_ok=True)
        self.subscribed_path = _write_csv(rows)
        return import_mailchimp_course_tags(subscribed=self.subscribed_path, **kwargs)

    def test_non_course_tag_only_is_completely_ignored(self) -> None:
        result = self._run(
            [{EMAIL_COLUMN: "event-only@example.invalid", TAGS_COLUMN: _tags("event-podcast")}]
        )
        self.assertEqual(result.rows_with_course_tag, 0)
        self.assertEqual(result.source_rows, 1)
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_tag_with_no_matching_cohort_is_skipped_and_reported(self) -> None:
        # de-zoomcamp exists nowhere in this database.
        account = CustomUser.objects.create(
            username="learner-a", email="learner-a@example.invalid"
        )
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "learner-a@example.invalid",
                    TAGS_COLUMN: _tags("de-zoomcamp-2025"),
                }
            ]
        )
        self.assertEqual(result.rows_with_course_tag, 1)
        self.assertEqual(result.tags_missing_cohort, {"de-zoomcamp-2025": 1})
        self.assertEqual(result.matched_account_total, 0)
        self.assertEqual(result.enrollments_created_total, 0)
        self.assertEqual(Enrollment.objects.filter(student=account).count(), 0)

    def test_tag_matching_existing_account_and_cohort_creates_enrollment(self) -> None:
        cohort = _cohort("de-zoomcamp", 2025)
        account = CustomUser.objects.create(
            username="learner-b", email="learner-b@example.invalid"
        )
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "learner-b@example.invalid",
                    TAGS_COLUMN: _tags("de-zoomcamp-2025"),
                }
            ]
        )
        self.assertEqual(result.matched_account_total, 1)
        self.assertEqual(result.enrollments_created_total, 1)
        self.assertTrue(Enrollment.objects.filter(student=account, course=cohort).exists())
        # Never a CourseRegistration -- see the module docstring.
        self.assertEqual(CourseRegistration.objects.count(), 0)

    def test_ordinal_tag_resolves_to_the_reviewed_year(self) -> None:
        cohort = _cohort("ml-zoomcamp", 2021)
        account = CustomUser.objects.create(
            username="learner-c", email="learner-c@example.invalid"
        )
        self._run(
            [{EMAIL_COLUMN: "learner-c@example.invalid", TAGS_COLUMN: _tags("ml-zoomcamp-1")}]
        )
        self.assertTrue(Enrollment.objects.filter(student=account, course=cohort).exists())

    def test_tag_with_no_matching_account_is_skipped_and_never_creates_one(self) -> None:
        _cohort("mlops-zoomcamp", 2023)
        before_accounts = CustomUser.objects.count()
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "brand-new@example.invalid",
                    TAGS_COLUMN: _tags("mlops-zoomcamp-2023"),
                }
            ]
        )
        self.assertEqual(result.no_account_match_total, 1)
        self.assertEqual(result.matched_account_total, 0)
        self.assertEqual(CustomUser.objects.count(), before_accounts)
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_multiple_course_tags_on_one_row_enroll_in_every_matching_cohort(self) -> None:
        de_cohort = _cohort("de-zoomcamp", 2024)
        llm_cohort = _cohort("llm-zoomcamp", 2024)
        account = CustomUser.objects.create(
            username="learner-d", email="learner-d@example.invalid"
        )
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "learner-d@example.invalid",
                    TAGS_COLUMN: _tags("de-zoomcamp-2024", "llm-zoomcamp-2024"),
                }
            ]
        )
        self.assertEqual(result.enrollments_created_total, 2)
        self.assertTrue(Enrollment.objects.filter(student=account, course=de_cohort).exists())
        self.assertTrue(Enrollment.objects.filter(student=account, course=llm_cohort).exists())

    def test_rerun_is_idempotent(self) -> None:
        cohort = _cohort("de-zoomcamp", 2026)
        account = CustomUser.objects.create(
            username="learner-e", email="learner-e@example.invalid"
        )
        rows = [
            {EMAIL_COLUMN: "learner-e@example.invalid", TAGS_COLUMN: _tags("de-zoomcamp-2026")}
        ]
        first = self._run(rows)
        self.assertEqual(first.enrollments_created_total, 1)

        second = self._run(rows)
        self.assertEqual(second.enrollments_created_total, 0)
        self.assertEqual(second.enrollments_already_present_total, 1)
        self.assertEqual(second.matched_account_total, 1)

        self.assertEqual(Enrollment.objects.filter(student=account, course=cohort).count(), 1)

    def test_dry_run_reports_without_writing(self) -> None:
        _cohort("ai-dev-tools-zoomcamp", 2025)
        CustomUser.objects.create(username="learner-f", email="learner-f@example.invalid")
        before = Enrollment.objects.count()
        result = self._run(
            [
                {
                    EMAIL_COLUMN: "learner-f@example.invalid",
                    TAGS_COLUMN: _tags("ai-dev-tools-zoomcamp-2025"),
                }
            ],
            apply=False,
        )
        self.assertEqual(Enrollment.objects.count(), before)
        self.assertEqual(result.enrollments_created_total, 1)
        self.assertFalse(result.applied)

    def test_dry_run_then_real_run_agree(self) -> None:
        _cohort("ml-zoomcamp", 2024)
        CustomUser.objects.create(username="learner-g", email="learner-g@example.invalid")
        rows = [
            {EMAIL_COLUMN: "learner-g@example.invalid", TAGS_COLUMN: _tags("ml-zoomcamp-2024")}
        ]
        dry = self._run(rows, apply=False)
        real = self._run(rows)

        self.assertEqual(dry.enrollments_created_total, real.enrollments_created_total)
        self.assertEqual(dry.matched_account_total, real.matched_account_total)
