"""CMP learner-profile reconciliation: identity-matched, blank-filling, dry-run first."""

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from django.test import TestCase

from accounts.models import CmpLearnerClaim, User
from accounts.services.cmp_learner_profile_import import (
    CmpLearnerProfileImportError,
    import_cmp_learner_profiles,
)
from courses.models.learner_profile import LearnerProfile


def _export(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE accounts_customuser (
            id INTEGER, certificate_name TEXT, country TEXT, region TEXT,
            registration_role TEXT, github_url TEXT, linkedin_url TEXT,
            personal_website_url TEXT, about_me TEXT);
        CREATE TABLE courses_courseregistration (
            id INTEGER, user_id INTEGER, name TEXT, company_name TEXT, country TEXT,
            region TEXT, role TEXT, linkedin_url TEXT, github_url TEXT,
            created_at TEXT, updated_at TEXT);
        INSERT INTO accounts_customuser VALUES
            (11, 'Cmp Name', 'Spain', 'Europe', '', NULL, NULL, NULL, NULL),
            (12, NULL, '', '', '', NULL, NULL, NULL, NULL),
            (13, 'Same Name', 'Spain', 'Europe', 'other', NULL, NULL, NULL, NULL);
        INSERT INTO courses_courseregistration VALUES
            (1, 11, 'Old Typed', 'Old Co', 'Spain', 'Europe', 'data_analyst', '', '',
             '2026-07-01 10:00:00+00', '2026-07-01 10:00:00+00'),
            (2, 11, 'Typed Name', 'Acme', 'Spain', 'Europe', 'data_engineer',
             'https://linkedin.com/in/x', '',
             '2026-09-01 10:00:00+00', '2026-09-02 10:00:00+00'),
            (3, NULL, 'Anon', 'Anon Co', 'Spain', 'Europe', 'other', '', '',
             '2026-09-01 10:00:00+00', '2026-09-01 10:00:00+00'),
            (4, 13, 'Same Name', 'Nope Inc', 'Spain', 'Europe', 'other', '', '',
             '2026-09-01 10:00:00+00', '2026-09-01 10:00:00+00');
        """
    )
    connection.commit()
    connection.close()


class CmpLearnerProfileImportTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "cmp.db"
        _export(self.source)
        self.learner = User.objects.create_user(username="l11", email="l11@example.com")
        self.other = User.objects.create_user(username="l12", email="l12@example.com")
        CmpLearnerClaim.objects.create(source_id=11, user_id=self.learner.pk)
        CmpLearnerClaim.objects.create(source_id=12, user_id=self.other.pk)
        # CMP user 13 has no claim: a same-named site account must stay untouched.
        self.namesake = User.objects.create_user(username="same", email="same@example.com")
        LearnerProfile.objects.create(user=self.learner, country="Portugal")

    def test_dry_run_writes_nothing(self):
        result = import_cmp_learner_profiles(self.source, apply=False)

        self.assertFalse(result.applied)
        self.assertEqual(result.profiles_updated, 1)
        profile = LearnerProfile.objects.get(user=self.learner)
        self.assertEqual(profile.company_name, "")
        self.assertIsNone(profile.profile_confirmed_at)

    def test_apply_fills_blanks_from_the_latest_registration_and_is_idempotent(self):
        result = import_cmp_learner_profiles(self.source, apply=True)

        self.assertEqual(result.claimed_users, 2)
        self.assertEqual(result.unclaimed_users, 1)
        self.assertEqual(result.registrations_without_user, 1)
        profile = LearnerProfile.objects.get(user=self.learner)
        self.assertEqual(profile.company_name, "Acme")
        self.assertEqual(profile.certificate_name, "Typed Name")
        self.assertEqual(profile.registration_role, "data_engineer")
        self.assertEqual(profile.linkedin_url, "https://linkedin.com/in/x")
        # A stored value is never overwritten.
        self.assertEqual(profile.country, "Portugal")
        self.assertEqual(profile.profile_confirmed_at, datetime(2026, 9, 2, 10, tzinfo=UTC))
        self.assertFalse(LearnerProfile.objects.filter(user=self.namesake).exists())

        again = import_cmp_learner_profiles(self.source, apply=True)

        self.assertEqual(again.profiles_updated, 0)
        self.assertEqual(again.profiles_created, 0)

    def test_a_profile_confirmed_on_the_site_later_is_left_alone(self):
        LearnerProfile.objects.filter(user=self.learner).update(
            profile_confirmed_at=datetime(2026, 11, 1, tzinfo=UTC)
        )

        result = import_cmp_learner_profiles(self.source, apply=True)

        self.assertEqual(result.profiles_newer_on_site, 1)
        self.assertEqual(LearnerProfile.objects.get(user=self.learner).company_name, "")

    def test_a_missing_source_is_refused(self):
        with self.assertRaises(CmpLearnerProfileImportError):
            import_cmp_learner_profiles(self.source.with_name("nope.db"))
