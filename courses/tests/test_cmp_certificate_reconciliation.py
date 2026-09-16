from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase

from accounts.models import CustomUser
from courses.models import Cohort, Course, Enrollment
from courses.models.cmp_import import CmpHistoryClaim, CmpHistoryImportBinding
from courses.services import cmp_certificate_reconciliation as service
from courses.services.cmp_certificate_reconciliation import (
    CmpCertificateReconciliationError,
    reconcile_cmp_enrollment_certificates,
)
from courses.services.cmp_learner_history_import import BINDING_KIND

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ISSUED = "https://certificate.invalid/issued.pdf"
STALE = "https://certificate.invalid/stale.pdf"


class CmpCertificateReconciliationTests(TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / ".tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="cmp-certificate-reconcile-", dir=scratch))
        self.addCleanup(shutil.rmtree, self.root, True)

        family = Course.objects.create(slug="ai-dev-tools-zoomcamp", title="AI Dev Tools")
        self.cohort = Cohort.objects.create(
            course=family,
            slug="ai-dev-tools-zoomcamp-2025",
            identifier="2025",
            year=2025,
            title="AI Dev Tools 2025",
        )
        self.learner = CustomUser.objects.create_user(username="learner")
        self.enrollment = Enrollment.objects.create(
            student=self.learner,
            course=self.cohort,
        )

    def source(self, rows: list[tuple[int, str | None]]) -> Path:
        path = self.root / "source.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE courses_course (id INTEGER PRIMARY KEY, slug TEXT NOT NULL);
                CREATE TABLE courses_enrollment (
                    id INTEGER PRIMARY KEY,
                    course_id INTEGER NOT NULL,
                    certificate_url TEXT
                );
                INSERT INTO courses_course (id, slug) VALUES (13, 'ai-dev-tools-2025');
                """
            )
            connection.executemany(
                "INSERT INTO courses_enrollment (id, course_id, certificate_url) VALUES (?, 13, ?)",
                rows,
            )
            connection.commit()
        finally:
            connection.close()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        CmpHistoryImportBinding.objects.create(
            kind=BINDING_KIND,
            schema_version=2,
            importer_version="fixture",
            source_sha256=digest,
        )
        return path

    def claim(self, source_id: int = 1, enrollment: Enrollment | None = None) -> None:
        CmpHistoryClaim.objects.create(
            table="courses_enrollment",
            source_id=source_id,
            target_id=(enrollment or self.enrollment).pk,
        )

    def reconcile(self, source: Path, *, apply: bool = False):
        return reconcile_cmp_enrollment_certificates(
            source,
            source_course_slug="ai-dev-tools-2025",
            target_cohort_slug=self.cohort.slug,
            apply=apply,
        )

    def test_dry_run_is_read_only_and_reports_aggregate_work(self) -> None:
        source = self.source([(1, ISSUED)])
        self.claim()

        result = self.reconcile(source)

        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.certificate_url)
        self.assertEqual(
            result.summary(),
            {
                "source_certificates": 1,
                "claims_matched": 1,
                "targets_matched": 1,
                "already_current": 0,
                "updates_required": 1,
                "updated": 0,
                "applied": False,
            },
        )

    def test_apply_updates_and_replay_is_idempotent(self) -> None:
        self.enrollment.certificate_url = STALE
        self.enrollment.save(update_fields=("certificate_url",))
        source = self.source([(1, ISSUED)])
        self.claim()

        first = self.reconcile(source, apply=True)
        replay = self.reconcile(source, apply=True)

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.certificate_url, ISSUED)
        self.assertEqual(first.updates_required, 1)
        self.assertEqual(first.updated, 1)
        self.assertEqual(replay.updates_required, 0)
        self.assertEqual(replay.updated, 0)
        self.assertEqual(replay.already_current, 1)

    def test_blank_source_never_erases_an_existing_certificate(self) -> None:
        self.enrollment.certificate_url = STALE
        self.enrollment.save(update_fields=("certificate_url",))
        source = self.source([(1, "  "), (2, None)])

        result = self.reconcile(source, apply=True)

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.certificate_url, STALE)
        self.assertEqual(result.source_certificates, 0)
        self.assertEqual(result.updated, 0)

    def test_missing_claim_refuses_without_writing(self) -> None:
        source = self.source([(1, ISSUED)])

        with self.assertRaisesMessage(CmpCertificateReconciliationError, "enrollment-claim-gap"):
            self.reconcile(source, apply=True)

        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.certificate_url)

    def test_claim_to_another_cohort_refuses_without_writing(self) -> None:
        other = Cohort.objects.create(
            course=self.cohort.course,
            slug="ai-dev-tools-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="AI Dev Tools 2026",
        )
        other_enrollment = Enrollment.objects.create(student=self.learner, course=other)
        source = self.source([(1, ISSUED)])
        self.claim(enrollment=other_enrollment)

        with self.assertRaisesMessage(CmpCertificateReconciliationError, "target-cohort-mismatch"):
            self.reconcile(source, apply=True)

        other_enrollment.refresh_from_db()
        self.assertIsNone(other_enrollment.certificate_url)

    def test_changed_source_digest_is_refused(self) -> None:
        source = self.source([(1, ISSUED)])
        self.claim()
        with source.open("ab") as handle:
            handle.write(b"changed")

        with self.assertRaisesMessage(CmpCertificateReconciliationError, "source-digest-mismatch"):
            self.reconcile(source, apply=True)

    def test_conflicting_urls_for_one_claimed_target_are_refused(self) -> None:
        source = self.source([(1, ISSUED), (2, STALE)])
        self.claim(1)
        self.claim(2)

        with self.assertRaisesMessage(
            CmpCertificateReconciliationError, "conflicting-target-certificates"
        ):
            self.reconcile(source, apply=True)

    def test_wal_backed_source_is_refused_before_unhashed_rows_can_be_read(self) -> None:
        path = self.root / "wal-source.sqlite3"
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE courses_course (id INTEGER PRIMARY KEY, slug TEXT NOT NULL);
            CREATE TABLE courses_enrollment (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                certificate_url TEXT
            );
            INSERT INTO courses_course (id, slug) VALUES (13, 'ai-dev-tools-2025');
            """
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        CmpHistoryImportBinding.objects.create(
            kind=BINDING_KIND,
            schema_version=2,
            importer_version="fixture",
            source_sha256=digest,
        )
        # This committed row exists only in the WAL. A normal mode=ro SQLite
        # connection sees it even though its bytes are absent from the bound
        # main-file digest above.
        connection.execute(
            "INSERT INTO courses_enrollment (id, course_id, certificate_url) VALUES (1, 13, ?)",
            (ISSUED,),
        )
        connection.commit()
        self.claim()

        with self.assertRaisesMessage(CmpCertificateReconciliationError, "source-not-standalone"):
            self.reconcile(path, apply=True)

        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.certificate_url)

    def test_claim_repointed_after_preflight_is_refused_inside_apply_transaction(
        self,
    ) -> None:
        other_learner = CustomUser.objects.create_user(username="other-learner")
        other_enrollment = Enrollment.objects.create(
            student=other_learner,
            course=self.cohort,
        )
        source = self.source([(1, ISSUED)])
        self.claim()
        real_claimed_targets = service._claimed_targets
        calls = 0

        def repoint_after_preflight(issued, *, for_update=False):
            nonlocal calls
            result = real_claimed_targets(issued, for_update=for_update)
            calls += 1
            if calls == 1:
                CmpHistoryClaim.objects.filter(table="courses_enrollment", source_id=1).update(
                    target_id=other_enrollment.pk
                )
            return result

        with mock.patch.object(service, "_claimed_targets", repoint_after_preflight):
            with self.assertRaisesMessage(
                CmpCertificateReconciliationError, "enrollment-claim-drift"
            ):
                self.reconcile(source, apply=True)

        self.enrollment.refresh_from_db()
        other_enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.certificate_url)
        self.assertIsNone(other_enrollment.certificate_url)
