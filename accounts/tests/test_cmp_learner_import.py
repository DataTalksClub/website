"""Tests for the CMP learner-account importer.

The source is a synthetic SQLite database built here, shaped like the real CMP
export's ``accounts_customuser`` / ``account_emailaddress`` tables (columns
verified against the real read-only export at
``/data/tmp/rds-export/rds-prod-20260902-012536.db`` while building this
importer). No real export is read, and no personal data reaches this suite.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.test import TestCase

from accounts.models import CmpLearnerImportProgress, CustomUser
from accounts.services.cmp_learner_import import (
    FORBIDDEN_TABLES,
    READ_TABLES,
    CmpLearnerImportError,
    dry_run_counts,
    import_cmp_learners,
    progress_status,
)

_SCHEMA = """
CREATE TABLE accounts_customuser (
    id INTEGER, password TEXT, last_login TEXT, is_superuser INTEGER, username TEXT,
    first_name TEXT, last_name TEXT, email TEXT, is_staff INTEGER, is_active INTEGER,
    date_joined TEXT, role TEXT, certificate_name TEXT, dark_mode INTEGER, about_me TEXT,
    github_url TEXT, linkedin_url TEXT, personal_website_url TEXT, country TEXT,
    region TEXT, registration_role TEXT, preferred_timezone TEXT
);
CREATE TABLE account_emailaddress (
    id INTEGER, email TEXT, verified INTEGER, "primary" INTEGER, user_id INTEGER
);
"""

_JOINED = "2024-01-18 22:14:38.592334+00"


def _account_row(
    user_id: int,
    *,
    username: str | None = None,
    email: str,
    is_staff: bool = False,
    is_superuser: bool = False,
    password: str = "pbkdf2_sha256$fake-usable-hash",
) -> tuple:
    return (
        user_id,
        password,
        _JOINED,
        1 if is_superuser else 0,
        username if username is not None else f"user{user_id}",
        f"First{user_id}",
        f"Last{user_id}",
        email,
        1 if is_staff else 0,
        1,
        _JOINED,
        "student",
        None,
        0,
        None,
        None,
        None,
        None,
        "US",
        "",
        "",
        "",
    )


def _build_source(path: Path, accounts: list[tuple], emails: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    connection.executemany(
        "insert into accounts_customuser values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        accounts,
    )
    connection.executemany(
        "insert into account_emailaddress values (?,?,?,?,?)",
        emails,
    )
    connection.commit()
    connection.close()


class CmpLearnerImportBasicsTests(TestCase):
    def _source(self, accounts, emails) -> Path:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        path = Path(tmp.name)
        self.addCleanup(path.unlink, missing_ok=True)
        _build_source(path, accounts, emails)
        return path

    def test_forbidden_tables_are_the_learner_import_security_boundary(self):
        # This importer's own set -- deliberately not review_import's
        # SENSITIVE_TABLES, which excludes the entire learner payload.
        self.assertEqual(
            FORBIDDEN_TABLES,
            frozenset(
                {
                    "django_session",
                    "socialaccount_socialaccount",
                    "socialaccount_socialapp",
                    "socialaccount_socialapp_sites",
                    "socialaccount_socialtoken",
                    "accounts_token",
                }
            ),
        )
        self.assertEqual(READ_TABLES & FORBIDDEN_TABLES, set())

    def test_import_never_touches_a_source_missing_the_forbidden_tables(self):
        """A source lacking the forbidden tables entirely still imports cleanly.

        If this importer ever queried one of them, this would fail with a
        real ``sqlite3.OperationalError`` (no such table), not a mocked
        assertion.
        """

        source = self._source(
            [_account_row(1, email="one@example.com")],
            [(1, "one@example.com", 1, 1, 1)],
        )
        result = import_cmp_learners(source, batch_size=10)
        self.assertEqual(result.accounts.written, 1)

    def test_account_shape(self):
        source = self._source(
            [_account_row(1, email="admin@example.com", is_staff=True, is_superuser=True)],
            [],
        )
        import_cmp_learners(source, batch_size=10)

        user = CustomUser.objects.get(cmp_source_user_id=1)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.identity_state, CustomUser.IdentityState.LEGACY)
        self.assertEqual(user.email, "admin@example.com")
        self.assertEqual(user.normalized_email, "admin@example.com")
        self.assertFalse(SocialAccount.objects.filter(user=user).exists())

    def test_raw_email_address_rows_are_copied_faithfully(self):
        source = self._source(
            [_account_row(1, email="one@example.com")],
            [(1, "one@example.com", 0, 1, 1)],  # unverified, like the export's one row
        )
        import_cmp_learners(source, batch_size=10)

        user = CustomUser.objects.get(cmp_source_user_id=1)
        row = EmailAddress.objects.get(user=user)
        self.assertEqual(row.email, "one@example.com")
        self.assertFalse(row.verified)
        self.assertTrue(row.primary)

    def test_accounts_with_no_email_row_get_a_synthesized_verified_primary_row(self):
        source = self._source(
            [_account_row(1, email="one@example.com")],
            [],  # the export's shape for 4 of 20,009 accounts
        )
        result = import_cmp_learners(source, batch_size=10)

        user = CustomUser.objects.get(cmp_source_user_id=1)
        row = EmailAddress.objects.get(user=user)
        self.assertEqual(row.email, "one@example.com")
        self.assertTrue(row.verified)
        self.assertTrue(row.primary)
        self.assertEqual(result.synthesized_email_addresses.written, 1)
        self.assertEqual(result.synthesis_skipped_collisions, ())

    def test_the_known_same_address_pair_shape_is_reported_not_forced(self):
        """Two accounts share an address; one carries the verified row, one none.

        This is the export's real shape (ids 2 and 15515, both real accounts,
        sharing one address). The importer must not merge them or invent a
        second verified row for the same address -- it imports both and
        reports the account it could not give a synthesized row to, by
        source id, so the existing reconciliation mechanism can find it.
        """

        source = self._source(
            [
                _account_row(2, email="shared@example.com", username="admin"),
                _account_row(15515, email="shared@example.com", username="shared@example.com"),
            ],
            [(1, "shared@example.com", 1, 1, 15515)],
        )
        result = import_cmp_learners(source, batch_size=10)

        self.assertEqual(CustomUser.objects.count(), 2)
        u2 = CustomUser.objects.get(cmp_source_user_id=2)
        u15515 = CustomUser.objects.get(cmp_source_user_id=15515)
        self.assertFalse(EmailAddress.objects.filter(user=u2).exists())
        self.assertTrue(EmailAddress.objects.filter(user=u15515, verified=True).exists())
        self.assertEqual(result.synthesis_skipped_collisions, (2,))
        # Both stay legacy/unmerged -- consolidating them is not this importer's job.
        self.assertEqual(u2.identity_state, CustomUser.IdentityState.LEGACY)
        self.assertEqual(u15515.identity_state, CustomUser.IdentityState.LEGACY)

    def test_a_taken_username_is_suffixed_not_collided(self):
        CustomUser.objects.create_user(username="popular", email="existing@example.com")
        source = self._source(
            [_account_row(1, username="popular", email="one@example.com")],
            [],
        )
        import_cmp_learners(source, batch_size=10)

        imported = CustomUser.objects.get(cmp_source_user_id=1)
        self.assertNotEqual(imported.username, "popular")
        self.assertTrue(imported.username.startswith("popular"))

    def test_blank_source_username_falls_back_to_email_local_part(self):
        source = self._source(
            [_account_row(1, username="", email="jane.doe@example.com")],
            [],
        )
        import_cmp_learners(source, batch_size=10)

        imported = CustomUser.objects.get(cmp_source_user_id=1)
        self.assertTrue(imported.username)
        self.assertTrue(imported.username.startswith("jane.doe"))

    def test_dry_run_reports_counts_and_writes_nothing(self):
        source = self._source(
            [_account_row(1, email="one@example.com"), _account_row(2, email="two@example.com")],
            [(1, "one@example.com", 1, 1, 1)],
        )
        report = dry_run_counts(source)
        self.assertEqual(report["accounts_in_source"], 2)
        self.assertEqual(report["account_emailaddress_in_source"], 1)
        self.assertEqual(report["accounts_already_imported"], 0)
        self.assertFalse(report["applied"])
        self.assertEqual(CustomUser.objects.count(), 0)

    def test_status_reports_progress_without_a_source(self):
        source = self._source([_account_row(1, email="one@example.com")], [])
        import_cmp_learners(source, batch_size=10)

        status = progress_status()
        self.assertTrue(status["progress"]["accounts_customuser"]["completed"])
        self.assertEqual(status["progress"]["accounts_customuser"]["rows_written"], 1)


class CmpLearnerImportResumabilityTests(TestCase):
    """Kill-and-resume, proven without a real process kill.

    A batch's writes and its watermark advance happen in one transaction
    (``django.db.transaction.atomic()``), so simulating "the process died
    mid-batch" is simulating an exception raised inside that block: Django
    rolls the whole block back, exactly as a SIGKILL would leave nothing for
    SQLite to have committed. What matters for this test is the state after
    that failure and after a normal re-run, not the mechanism used to
    interrupt it.
    """

    def _source(self, n: int) -> Path:
        accounts = [_account_row(i, email=f"user{i}@example.com") for i in range(1, n + 1)]
        emails = [(i, f"user{i}@example.com", 1, 1, i) for i in range(1, n + 1)]
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        path = Path(tmp.name)
        self.addCleanup(path.unlink, missing_ok=True)
        _build_source(path, accounts, emails)
        return path

    def test_a_mid_batch_failure_leaves_no_partial_batch_and_a_resume_completes_cleanly(self):
        source = self._source(25)

        import accounts.services.cmp_learner_import as mod

        real_save = mod._save_progress
        calls = {"n": 0}

        def _fail_on_third_batch(progress):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated kill mid-batch")
            real_save(progress)

        mod._save_progress = _fail_on_third_batch
        try:
            with self.assertRaises(RuntimeError):
                import_cmp_learners(source, batch_size=5)
        finally:
            mod._save_progress = real_save

        # Two batches of 5 committed; the third's writes and watermark both
        # rolled back together -- nothing for the DB and the progress row to
        # disagree about.
        progress = CmpLearnerImportProgress.objects.get(table="accounts_customuser")
        self.assertEqual(progress.last_source_id, 10)
        self.assertEqual(progress.rows_written, 10)
        self.assertFalse(progress.completed)
        self.assertEqual(CustomUser.objects.count(), 10)

        # A normal resume: no --edition-style flag, just run it again.
        result = import_cmp_learners(source, batch_size=5)

        self.assertEqual(CustomUser.objects.count(), 25)
        self.assertEqual(
            sorted(CustomUser.objects.values_list("cmp_source_user_id", flat=True)),
            list(range(1, 26)),
        )
        self.assertEqual(result.accounts.written, 25)
        self.assertEqual(result.accounts.skipped, 0)
        self.assertEqual(result.email_addresses.written, 25)

    def test_re_running_a_completed_import_creates_no_duplicates(self):
        source = self._source(12)
        first = import_cmp_learners(source, batch_size=4)
        second = import_cmp_learners(source, batch_size=4)

        self.assertEqual(first.accounts.written, 12)
        self.assertEqual(second.accounts.written, 12)  # cumulative, not "12 more"
        self.assertEqual(CustomUser.objects.count(), 12)
        self.assertEqual(EmailAddress.objects.count(), 12)

    def test_a_row_already_carrying_its_source_id_is_skipped_even_off_the_watermark(self):
        """Belt-and-braces: correctness does not depend on the watermark alone."""

        source = self._source(5)
        import_cmp_learners(source, batch_size=100)

        # Force the watermark backwards, as if progress bookkeeping were lost
        # but the rows themselves were not.
        progress = CmpLearnerImportProgress.objects.get(table="accounts_customuser")
        progress.last_source_id = 0
        progress.completed = False
        progress.save()

        result = import_cmp_learners(source, batch_size=100)
        self.assertEqual(CustomUser.objects.count(), 5)
        self.assertEqual(result.accounts.skipped, 5)


class CmpLearnerImportSourceErrorsTests(TestCase):
    def test_a_missing_source_file_is_a_safe_refusal(self):
        with self.assertRaises(CmpLearnerImportError):
            import_cmp_learners(Path("/nonexistent/no-such-export.db"), batch_size=10)
