"""REL-03/REL-04 acceptance for the learner-account importer.

The run binding must refuse a different export before any write, and a
killed batch must leave the database exactly as it was -- claims, watermark
and account rows commit or roll back as one -- so an ordinary unmodified
re-run reaches the same end state as an uninterrupted one, with no manual
progress repair.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest.mock
from pathlib import Path

from allauth.account.models import EmailAddress
from django.db import IntegrityError
from django.test import TestCase

from accounts.models import (
    CmpLearnerClaim,
    CmpLearnerImportBinding,
    CmpLearnerImportProgress,
    CustomUser,
)
from accounts.services import cmp_learner_import as service
from accounts.services.cmp_learner_import import (
    CmpLearnerImportError,
    import_cmp_learners,
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


def _account_row(user_id: int, email: str) -> tuple:
    return (
        user_id,
        "pbkdf2_sha256$fake-usable-hash",
        _JOINED,
        0,
        f"user{user_id}",
        f"First{user_id}",
        f"Last{user_id}",
        email,
        0,
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


def _email_row(row_id: int, user_id: int, email: str) -> tuple:
    return (row_id, email, 1, 1, user_id)


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


class _SourceFixtureMixin(TestCase):
    def source(self, count: int, *, first_id: int = 1) -> Path:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        accounts = [
            _account_row(first_id + index, f"user{first_id + index}@example.invalid")
            for index in range(count)
        ]
        emails = [
            _email_row(
                first_id + index,
                first_id + index,
                f"user{first_id + index}@example.invalid",
            )
            for index in range(count)
        ]
        _build_source(path, accounts, emails)
        return path


class RunBindingTests(_SourceFixtureMixin, TestCase):
    def test_the_first_run_records_the_binding(self) -> None:
        source = self.source(1)

        import_cmp_learners(source)

        binding = CmpLearnerImportBinding.objects.get(kind=service.BINDING_KIND)
        self.assertEqual(binding.source_sha256, service._source_digest(source))
        self.assertEqual(binding.schema_version, service.SCHEMA_VERSION)
        self.assertEqual(binding.importer_version, service.IMPORTER_VERSION)

    def test_a_rerun_of_the_same_export_resumes(self) -> None:
        source = self.source(3)
        import_cmp_learners(source, batch_size=2)

        # The watermark says the import completed, so a rerun is a no-op --
        # binding check passes, nothing is re-read or rewritten.
        result = import_cmp_learners(source)

        self.assertEqual(CustomUser.objects.count(), 3)
        self.assertEqual(result.accounts.written, 3)
        self.assertEqual(CmpLearnerClaim.objects.count(), 3)

    def test_a_changed_export_is_refused_before_any_write(self) -> None:
        first = self.source(2)
        import_cmp_learners(first)
        accounts_before = CustomUser.objects.count()
        claims_before = CmpLearnerClaim.objects.count()

        # Same logical shape, different bytes: a different snapshot.
        second = self.source(3)

        with self.assertRaises(CmpLearnerImportError) as error:
            import_cmp_learners(second)

        self.assertEqual(str(error.exception), "run-bound-to-different-source")
        self.assertEqual(CustomUser.objects.count(), accounts_before)
        self.assertEqual(CmpLearnerClaim.objects.count(), claims_before)

    def test_a_changed_importer_version_is_refused(self) -> None:
        source = self.source(1)
        import_cmp_learners(source)
        CmpLearnerImportBinding.objects.update(importer_version="something-older")

        with self.assertRaises(CmpLearnerImportError) as error:
            import_cmp_learners(source)

        self.assertEqual(str(error.exception), "run-bound-to-different-importer-version")

    def test_two_databases_never_share_a_binding(self) -> None:
        # A rebuilt target database has no binding row: it starts a fresh
        # import of the same export instead of resuming someone else's.
        source = self.source(1)
        import_cmp_learners(source)
        first_uuid = CmpLearnerImportBinding.objects.get(kind=service.BINDING_KIND).target_uuid

        CmpLearnerImportBinding.objects.all().delete()
        CmpLearnerClaim.objects.all().delete()
        CmpLearnerImportProgress.objects.all().delete()
        CustomUser.objects.all().delete()

        import_cmp_learners(source)
        second_binding = CmpLearnerImportBinding.objects.get(kind=service.BINDING_KIND)

        self.assertNotEqual(second_binding.target_uuid, first_uuid)
        self.assertEqual(CustomUser.objects.count(), 1)


class KilledBatchAtomicityTests(_SourceFixtureMixin, TestCase):
    """Fault injection immediately inside a batch: the account row, its
    claim, and the watermark must roll back together (audit REL-04)."""

    def _source_with_a_trap(self, count: int, *, trap_email: str) -> Path:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        accounts = [
            _account_row(index + 1, f"user{index + 1}@example.invalid") for index in range(count)
        ]
        # The trapped source row sits mid-batch.
        accounts = [
            _account_row(
                index + 1,
                trap_email if index == 1 else f"user{index + 1}@example.invalid",
            )
            for index in range(count)
        ]
        emails = [
            _email_row(
                index + 1,
                index + 1,
                trap_email if index == 1 else f"user{index + 1}@example.invalid",
            )
            for index in range(count)
        ]
        _build_source(path, accounts, emails)
        return path

    def test_a_killed_batch_leaves_no_partial_state_and_a_rerun_completes(self) -> None:
        source = self._source_with_a_trap(4, trap_email="trap@example.invalid")
        real_build = service._build_account
        calls = {"count": 0}

        def trapping_build(row):
            calls["count"] += 1
            if (row["email"] or "") == "trap@example.invalid":
                raise IntegrityError("simulated kill mid-batch")
            return real_build(row)

        with unittest.mock.patch.object(service, "_build_account", trapping_build):
            with self.assertRaises(IntegrityError):
                import_cmp_learners(source, batch_size=10)

        # Nothing from the killed batch survived -- not the rows before the
        # trap, not their claims, not the watermark.
        self.assertEqual(CustomUser.objects.count(), 0)
        self.assertEqual(CmpLearnerClaim.objects.count(), 0)
        progress = CmpLearnerImportProgress.objects.get(table="accounts_customuser")
        self.assertEqual(progress.last_source_id, 0)

        # An ordinary, unmodified re-run (no manual progress repair) completes.
        result = import_cmp_learners(source, batch_size=10)

        self.assertEqual(CustomUser.objects.count(), 4)
        self.assertEqual(CmpLearnerClaim.objects.count(), 4)
        self.assertEqual(EmailAddress.objects.count(), 4)
        progress.refresh_from_db()
        self.assertTrue(progress.completed)
        self.assertEqual(
            sorted(CmpLearnerClaim.objects.values_list("source_id", flat=True)),
            [1, 2, 3, 4],
        )
        self.assertEqual(result.accounts.skipped, 0)
