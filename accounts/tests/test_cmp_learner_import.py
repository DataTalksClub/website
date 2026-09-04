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
    CmpClaimsStore,
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


class _ClaimsFixtureMixin:
    """Every test gets its own claims file -- the default path is shared,
    real resumability state, so a test using it would leak into siblings and
    into a real ``.tmp/cmp_learner_import_claims.json`` left by an actual run.
    """

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.NamedTemporaryFile(suffix="-claims.json", delete=False)
        tmp.close()
        self.claims_path = Path(tmp.name)
        self.claims_path.unlink()  # CmpClaimsStore.load tolerates "missing"
        self.addCleanup(self.claims_path.unlink, missing_ok=True)

    def _user_for_source(self, source_id: int) -> CustomUser:
        store = CmpClaimsStore.load(self.claims_path)
        user_id = store.user_id_for_source(source_id)
        self.assertIsNotNone(user_id, f"source id {source_id} was never claimed")
        return CustomUser.objects.get(pk=user_id)

    def _import(self, source: Path, *, batch_size: int = 10):
        return import_cmp_learners(source, batch_size=batch_size, claims_path=self.claims_path)


class CmpLearnerImportBasicsTests(_ClaimsFixtureMixin, TestCase):
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
        result = self._import(source)
        self.assertEqual(result.accounts.written, 1)

    def test_account_shape(self):
        source = self._source(
            [_account_row(1, email="admin@example.com", is_staff=True, is_superuser=True)],
            [],
        )
        self._import(source)

        user = self._user_for_source(1)
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
        self._import(source)

        user = self._user_for_source(1)
        row = EmailAddress.objects.get(user=user)
        self.assertEqual(row.email, "one@example.com")
        self.assertFalse(row.verified)
        self.assertTrue(row.primary)

    def test_accounts_with_no_email_row_get_a_synthesized_verified_primary_row(self):
        source = self._source(
            [_account_row(1, email="one@example.com")],
            [],  # the export's shape for 4 of 20,009 accounts
        )
        result = self._import(source)

        user = self._user_for_source(1)
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
        result = self._import(source)

        self.assertEqual(CustomUser.objects.count(), 2)
        u2 = self._user_for_source(2)
        u15515 = self._user_for_source(15515)
        self.assertFalse(EmailAddress.objects.filter(user=u2).exists())
        self.assertTrue(EmailAddress.objects.filter(user=u15515, verified=True).exists())
        self.assertEqual(result.synthesis_skipped_collisions, (2,))
        # Both stay legacy/unmerged -- consolidating them is not this importer's job.
        self.assertEqual(u2.identity_state, CustomUser.IdentityState.LEGACY)
        self.assertEqual(u15515.identity_state, CustomUser.IdentityState.LEGACY)

    def test_an_account_a_different_importer_already_created_is_attached_not_duplicated(self):
        """The exact shape ``import_legacy_zoomcamp.py`` leaves behind: an
        account with just a username and a real email, unclaimed by this
        importer, created before this importer ever runs (legacy history
        imports first, per the migration runbook's step order). This importer
        must attach its row onto that account, not create a second one for
        the same address -- that second row is exactly what left 879 real
        members ``verified_owner_ambiguous`` and locked out.
        """

        legacy_user = CustomUser(username="zc-hist-deadbeef", email="shared-learner@example.invalid")
        legacy_user.set_unusable_password()
        legacy_user.save()
        legacy_pk = legacy_user.pk

        source = self._source(
            [
                _account_row(
                    1,
                    username="shared-learner",
                    email="shared-learner@example.invalid",
                )
            ],
            [(1, "shared-learner@example.invalid", 1, 1, 1)],
        )
        result = self._import(source)

        # Still one account, at the same primary key -- so every existing
        # reference to it (enrollments, submissions, certificates) is
        # untouched.
        self.assertEqual(
            CustomUser.objects.filter(email="shared-learner@example.invalid").count(), 1
        )
        merged = CustomUser.objects.get(email="shared-learner@example.invalid")
        self.assertEqual(merged.pk, legacy_pk)
        # The importer's own username choice never overwrites the identity
        # the first importer already established.
        self.assertEqual(merged.username, "zc-hist-deadbeef")
        self.assertEqual(self._user_for_source(1).pk, legacy_pk)
        self.assertFalse(merged.has_usable_password())
        self.assertEqual(merged.first_name, "First1")
        self.assertEqual(merged.country, "US")
        # The CMP-sourced email row lands on the same, merged account.
        email_row = EmailAddress.objects.get(user=merged)
        self.assertEqual(email_row.email, "shared-learner@example.invalid")
        self.assertTrue(email_row.verified)
        self.assertEqual(result.cross_source_matches, (1,))

    def test_a_second_cmp_row_never_attaches_onto_a_cmp_created_row(self):
        """The one already-documented within-CMP collision (ids 2/15515 in the
        real export) must keep surfacing for manual reconciliation -- it is
        not the same shape as a cross-source duplicate, and must not be
        silently auto-merged by the new cross-source matching."""

        source = self._source(
            [
                _account_row(2, email="shared@example.com", username="admin"),
                _account_row(15515, email="shared@example.com", username="shared@example.com"),
            ],
            [(1, "shared@example.com", 1, 1, 15515)],
        )
        result = self._import(source)

        self.assertEqual(CustomUser.objects.count(), 2)
        self.assertEqual(result.cross_source_matches, ())
        self.assertEqual(result.synthesis_skipped_collisions, (2,))

    def test_a_taken_username_is_suffixed_not_collided(self):
        CustomUser.objects.create_user(username="popular", email="existing@example.com")
        source = self._source(
            [_account_row(1, username="popular", email="one@example.com")],
            [],
        )
        self._import(source)

        imported = self._user_for_source(1)
        self.assertNotEqual(imported.username, "popular")
        self.assertTrue(imported.username.startswith("popular"))

    def test_blank_source_username_falls_back_to_email_local_part(self):
        source = self._source(
            [_account_row(1, username="", email="jane.doe@example.com")],
            [],
        )
        self._import(source)

        imported = self._user_for_source(1)
        self.assertTrue(imported.username)
        self.assertTrue(imported.username.startswith("jane.doe"))

    def test_dry_run_reports_counts_and_writes_nothing(self):
        source = self._source(
            [_account_row(1, email="one@example.com"), _account_row(2, email="two@example.com")],
            [(1, "one@example.com", 1, 1, 1)],
        )
        report = dry_run_counts(source, claims_path=self.claims_path)
        self.assertEqual(report["accounts_in_source"], 2)
        self.assertEqual(report["account_emailaddress_in_source"], 1)
        self.assertEqual(report["accounts_already_imported"], 0)
        self.assertFalse(report["applied"])
        self.assertEqual(CustomUser.objects.count(), 0)

    def test_status_reports_progress_without_a_source(self):
        source = self._source([_account_row(1, email="one@example.com")], [])
        self._import(source)

        status = progress_status(claims_path=self.claims_path)
        self.assertTrue(status["progress"]["accounts_customuser"]["completed"])
        self.assertEqual(status["progress"]["accounts_customuser"]["rows_written"], 1)
        self.assertEqual(status["claims_recorded"], 1)


class CmpLearnerImportResumabilityTests(_ClaimsFixtureMixin, TestCase):
    """Kill-and-resume, proven without a real process kill.

    A batch's writes and its watermark advance happen in one transaction
    (``django.db.transaction.atomic()``), so simulating "the process died
    mid-batch" is simulating an exception raised inside that block: Django
    rolls the whole block back, exactly as a SIGKILL would leave nothing for
    SQLite to have committed. What matters for this test is the state after
    that failure and after a normal re-run, not the mechanism used to
    interrupt it. The claims file, unlike the database, cannot roll back --
    but a killed batch never reaches the claims-file write at all (it happens
    only after the batch's transaction commits, see
    ``accounts.services.cmp_learner_import``'s module docstring), so a
    simulated kill leaves the claims file exactly as consistent as the
    database.
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
                self._import(source, batch_size=5)
        finally:
            mod._save_progress = real_save

        # Two batches of 5 committed; the third's writes and watermark both
        # rolled back together -- nothing for the DB, the progress row, and
        # the claims file to disagree about (the claims file was never
        # touched for the third batch: it writes only after _save_progress
        # returns, which is exactly where this simulated kill happens).
        progress = CmpLearnerImportProgress.objects.get(table="accounts_customuser")
        self.assertEqual(progress.last_source_id, 10)
        self.assertEqual(progress.rows_written, 10)
        self.assertFalse(progress.completed)
        self.assertEqual(CustomUser.objects.count(), 10)
        self.assertEqual(len(CmpClaimsStore.load(self.claims_path)), 10)

        # A normal resume: no --edition-style flag, just run it again.
        result = self._import(source, batch_size=5)

        self.assertEqual(CustomUser.objects.count(), 25)
        claims = CmpClaimsStore.load(self.claims_path)
        self.assertEqual(len(claims), 25)
        self.assertEqual(
            sorted(source_id for source_id, _user_id in claims.sorted_claims()),
            list(range(1, 26)),
        )
        self.assertEqual(result.accounts.written, 25)
        self.assertEqual(result.accounts.skipped, 0)
        self.assertEqual(result.email_addresses.written, 25)

    def test_re_running_a_completed_import_creates_no_duplicates(self):
        source = self._source(12)
        first = self._import(source, batch_size=4)
        second = self._import(source, batch_size=4)

        self.assertEqual(first.accounts.written, 12)
        self.assertEqual(second.accounts.written, 12)  # cumulative, not "12 more"
        self.assertEqual(CustomUser.objects.count(), 12)
        self.assertEqual(EmailAddress.objects.count(), 12)

    def test_a_row_already_claimed_is_skipped_even_off_the_watermark(self):
        """Belt-and-braces: correctness does not depend on the watermark alone."""

        source = self._source(5)
        self._import(source, batch_size=100)

        # Force the watermark backwards, as if progress bookkeeping were lost
        # but the rows themselves (and the claims file) were not.
        progress = CmpLearnerImportProgress.objects.get(table="accounts_customuser")
        progress.last_source_id = 0
        progress.completed = False
        progress.save()

        result = self._import(source, batch_size=100)
        self.assertEqual(CustomUser.objects.count(), 5)
        self.assertEqual(result.accounts.skipped, 5)

    def test_a_lost_claims_file_recovers_by_idempotent_reattachment_not_duplication(self):
        """The one residual risk the module docstring calls out: a claims
        file that forgot an already-imported row (the narrow crash window
        between a batch's commit and its claims-file write, or -- as here --
        a claims file lost outright) never creates a second CustomUser row
        for the same source id. ``_find_cross_source_match`` finds the
        importer's own earlier row (by ``normalized_email``, unclaimed from
        the store's point of view) and safely re-attaches onto it instead.
        """

        source = self._source(3)
        self._import(source, batch_size=100)
        self.assertEqual(CustomUser.objects.count(), 3)

        # Simulate the claims file being lost while the database (and its
        # watermark) is intact -- a stronger fault than the narrow crash
        # window the module docstring accepts as a residual risk.
        self.claims_path.unlink()
        progress = CmpLearnerImportProgress.objects.get(table="accounts_customuser")
        progress.last_source_id = 0
        progress.completed = False
        progress.save()

        result = self._import(source, batch_size=100)

        # No duplicate accounts -- every row re-attached onto the account
        # this importer already created for it. `written` is cumulative
        # across resumed runs against the same progress row (see
        # CmpLearnerImportProgress), so this is 3 (first run) + 3 (this
        # run's re-attachments), not "3 more" restated as a bare 3.
        self.assertEqual(CustomUser.objects.count(), 3)
        self.assertEqual(result.accounts.written, 6)
        self.assertEqual(result.cross_source_matches, (1, 2, 3))
        claims = CmpClaimsStore.load(self.claims_path)
        self.assertEqual(len(claims), 3)


class CmpLearnerImportSourceErrorsTests(_ClaimsFixtureMixin, TestCase):
    def test_a_missing_source_file_is_a_safe_refusal(self):
        with self.assertRaises(CmpLearnerImportError):
            self._import(Path("/nonexistent/no-such-export.db"))
