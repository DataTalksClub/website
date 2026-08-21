from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditEvent
from core.services import ServiceContext
from courses.models import (
    Cohort,
    CourseRegistration,
    CourseRegistrationCountRevision,
    CourseRegistrationCountSlot,
    CourseRegistrationCountSourceRun,
    RegistrationCampaign,
)
from courses.registration_count_importer import (
    ADAPTER_VERSION,
    CourseCountSourceError,
    schema_contract_checksum,
)
from courses.services.registration_counts import (
    CourseRegistrationCountConflict,
    activate_source,
    cancel_source,
    public_course_registration_count,
    replace_baseline_with_rows,
    restore_baseline_from_rows,
    rollback_source,
    stage_registered_source,
    validate_source,
)

SOURCE_MINIMUM = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
SOURCE_MAXIMUM = datetime(2026, 1, 3, 11, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
CAPTURED = datetime(2026, 1, 11, 0, 0, tzinfo=UTC)
NATIVE_START = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)


class CourseRegistrationCountBaselineTests(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.source = Path(self.temporary.name) / "synthetic-course-count.sqlite3"
        self.course = Cohort.objects.create(
            slug="synthetic-cohort-2026",
            title="Synthetic cohort",
            description="Deterministic aggregate fixture.",
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug="synthetic-campaign",
            title="Synthetic campaign",
            edition_label="2026 cohort",
            current_course=self.course,
        )
        self._write_source(
            registrations=(
                (1, 1, 1, SOURCE_MINIMUM.isoformat(), "synthetic-one"),
                (2, 1, 1, SOURCE_MAXIMUM.isoformat(), "synthetic-two"),
            )
        )
        self.registry = {"synthetic-course-count": self._registry_entry()}
        self.context = ServiceContext.from_current(
            actor_ref="operator:synthetic",
            idempotency_key="synthetic-service-command",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_source(self, *, registrations: tuple[tuple[object, ...], ...]) -> None:
        connection = sqlite3.connect(self.source)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE courses_course (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE
            );
            CREATE TABLE courses_registrationcampaign (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                current_course_id INTEGER REFERENCES courses_course(id)
            );
            CREATE TABLE courses_courseregistration (
                id INTEGER PRIMARY KEY,
                campaign_id INTEGER NOT NULL REFERENCES courses_registrationcampaign(id),
                course_id INTEGER REFERENCES courses_course(id),
                created_at TEXT NOT NULL,
                email_normalized TEXT NOT NULL,
                UNIQUE(campaign_id, email_normalized)
            );
            """
        )
        connection.execute(
            "INSERT INTO courses_course(id, slug) VALUES (?, ?)",
            (1, self.course.slug),
        )
        connection.execute(
            "INSERT INTO courses_registrationcampaign(id, slug, current_course_id) "
            "VALUES (?, ?, ?)",
            (1, self.campaign.slug, 1),
        )
        connection.executemany(
            "INSERT INTO courses_courseregistration"
            "(id, campaign_id, course_id, created_at, email_normalized) "
            "VALUES (?, ?, ?, ?, ?)",
            registrations,
        )
        connection.commit()
        connection.close()

    def _registry_entry(self) -> dict[str, object]:
        payload = self.source.read_bytes()
        return {
            "adapter": ADAPTER_VERSION,
            "path": str(self.source),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
            "schema_version": "synthetic-cmp-v1",
            "schema_contract_checksum": schema_contract_checksum(self.source),
            "captured_at": CAPTURED.isoformat(),
            "source_frozen_at": CUTOFF.isoformat(),
            "coverage_cutoff_at": CUTOFF.isoformat(),
            "native_start_at": NATIVE_START.isoformat(),
        }

    def _replace_source(self, registrations: tuple[tuple[object, ...], ...]) -> None:
        self.source.unlink()
        self._write_source(registrations=registrations)
        self.registry = {"synthetic-course-count": self._registry_entry()}

    def _stage_validate_activate(self) -> CourseRegistrationCountSourceRun:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
            self.assertFalse(replayed)
            run = validate_source(
                run.id,
                expected_revision=run.revision,
                reason_code="synthetic_validate",
                actor=None,
                context=self.context,
            )
            return activate_source(
                run.id,
                expected_revision=run.revision,
                reason_code="synthetic_activate",
                actor=None,
                context=self.context,
            )

    def test_stage_is_atomic_and_exact_replay_writes_nothing(self) -> None:
        before = self.source.read_bytes()
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
            self.assertFalse(replayed)
            counts = (
                CourseRegistrationCountSourceRun.objects.count(),
                CourseRegistrationCountRevision.objects.count(),
                AuditEvent.objects.count(),
            )
            repeated, replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_replay",
                actor=None,
                context=self.context,
            )
        self.assertTrue(replayed)
        self.assertEqual(repeated.id, run.id)
        self.assertEqual(
            counts,
            (
                CourseRegistrationCountSourceRun.objects.count(),
                CourseRegistrationCountRevision.objects.count(),
                AuditEvent.objects.count(),
            ),
        )
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(run.row_total, 2)
        self.assertEqual(run.count_revisions.get().baseline_count, 2)
        audit = AuditEvent.objects.get(action="courses.registration_count_baseline.staged")
        self.assertEqual(
            audit.changes,
            {
                "state": {"before": None, "after": "staged"},
                "revision": {"before": None, "after": 1},
            },
        )
        self.assertTrue(audit.idempotency_key_hash)

    def test_same_reference_with_different_manifest_fails_closed(self) -> None:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, _replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
        counts = (
            CourseRegistrationCountSourceRun.objects.count(),
            CourseRegistrationCountRevision.objects.count(),
            AuditEvent.objects.count(),
        )
        self._replace_source(
            (
                (1, 1, 1, SOURCE_MINIMUM.isoformat(), "synthetic-one"),
                (2, 1, 1, SOURCE_MAXIMUM.isoformat(), "synthetic-two"),
                (3, 1, 1, "2026-01-04T12:00:00+00:00", "synthetic-three"),
            )
        )
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            with self.assertRaisesMessage(
                CourseRegistrationCountConflict, "source_identity_conflict"
            ):
                stage_registered_source(
                    source_reference="synthetic-course-count",
                    reason_code="synthetic_changed",
                    actor=None,
                    context=self.context,
                )
        self.assertEqual(
            counts,
            (
                CourseRegistrationCountSourceRun.objects.count(),
                CourseRegistrationCountRevision.objects.count(),
                AuditEvent.objects.count(),
            ),
        )
        run.refresh_from_db()
        self.assertEqual(run.row_total, 2)

    def test_source_and_count_provenance_are_immutable(self) -> None:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, _replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
        revision = run.count_revisions.get()
        run.row_total = 99
        with self.assertRaisesMessage(ValueError, "source provenance is immutable"):
            run.save()
        revision.baseline_count = 99
        with self.assertRaisesMessage(ValueError, "provenance and count are immutable"):
            revision.save()

    def test_database_rejects_states_outside_the_exact_lifecycle(self) -> None:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, _replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
        revision = run.count_revisions.get()
        run.state = "unknown"
        run.revision += 1
        with self.assertRaises(IntegrityError), transaction.atomic():
            run.save(update_fields=("state", "revision", "updated_at"))
        revision.state = "unknown"
        revision.revision += 1
        with self.assertRaises(IntegrityError), transaction.atomic():
            revision.save(update_fields=("state", "revision", "updated_at"))

    def test_cancel_quarantines_candidate_revisions_without_public_pointer(self) -> None:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, _replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
            run = cancel_source(
                run.id,
                expected_revision=run.revision,
                reason_code="synthetic_cancel",
                actor=None,
                context=self.context,
            )
        self.assertEqual(run.state, CourseRegistrationCountSourceRun.State.CANCELLED)
        self.assertEqual(
            set(run.count_revisions.values_list("state", flat=True)),
            {CourseRegistrationCountRevision.State.QUARANTINED},
        )
        self.assertFalse(CourseRegistrationCountSlot.objects.exists())
        audit = AuditEvent.objects.get(action="courses.registration_count_baseline.cancelled")
        self.assertEqual(audit.changes["state"], {"before": "staged", "after": "cancelled"})
        self.assertEqual(audit.changes["revision"], {"before": 1, "after": 2})
        self.assertTrue(audit.idempotency_key_hash)

    def test_baseline_plus_native_counts_exact_rows_once_and_rollback_omits(self) -> None:
        run = self._stage_validate_activate()
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            total = public_course_registration_count(self.campaign)
            self.assertIsNotNone(total)
            assert total is not None
            self.assertEqual(total.count, 2)
            CourseRegistration.objects.create(
                campaign=self.campaign,
                course=self.course,
                email="native-one",
            )
            self.assertEqual(public_course_registration_count(self.campaign).count, 3)  # type: ignore[union-attr]
            other_course = Cohort.objects.create(
                slug="synthetic-other-cohort",
                title="Other cohort",
                description="Other deterministic cohort.",
            )
            other_campaign = RegistrationCampaign.objects.create(
                slug="synthetic-other-campaign",
                title="Other campaign",
                current_course=other_course,
            )
            CourseRegistration.objects.create(
                campaign=other_campaign,
                course=other_course,
                email="other-native",
            )
            self.assertEqual(public_course_registration_count(self.campaign).count, 3)  # type: ignore[union-attr]
            run = rollback_source(
                run.id,
                expected_revision=run.revision,
                reason_code="synthetic_rollback",
                actor=None,
                context=self.context,
            )
            self.assertEqual(run.state, CourseRegistrationCountSourceRun.State.ROLLED_BACK)
            self.assertIsNone(public_course_registration_count(self.campaign))
        audit = AuditEvent.objects.get(action="courses.registration_count_baseline.rolled_back")
        self.assertEqual(audit.changes["state"], {"before": "active", "after": "rolled_back"})
        self.assertEqual(audit.changes["revision"], {"before": 3, "after": 4})
        self.assertTrue(audit.idempotency_key_hash)

    def test_target_repoint_and_registered_checksum_drift_omit_instead_of_zero(self) -> None:
        self._stage_validate_activate()
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            self.assertEqual(public_course_registration_count(self.campaign).count, 2)  # type: ignore[union-attr]
        drifted = {
            "synthetic-course-count": {
                **self.registry["synthetic-course-count"],
                "sha256": "f" * 64,
            }
        }
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=drifted):
            self.assertIsNone(public_course_registration_count(self.campaign))
        boundary_drifted = {
            "synthetic-course-count": {
                **self.registry["synthetic-course-count"],
                "native_start_at": "2026-02-02T00:00:00+00:00",
            }
        }
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=boundary_drifted):
            self.assertIsNone(public_course_registration_count(self.campaign))
        changed = Cohort.objects.create(
            slug="synthetic-changed-cohort",
            title="Changed cohort",
            description="Changed deterministic cohort.",
        )
        self.campaign.current_course = changed
        self.campaign.save(update_fields=("current_course", "updated_at"))
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            self.assertIsNone(public_course_registration_count(self.campaign))

    def test_failed_refresh_preserves_a_still_registered_active_total(self) -> None:
        self._stage_validate_activate()
        registry = {
            **self.registry,
            "synthetic-refresh": self.registry["synthetic-course-count"],
        }
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=registry):
            with self.assertRaisesMessage(
                CourseRegistrationCountConflict, "source_checksum_already_staged"
            ):
                stage_registered_source(
                    source_reference="synthetic-refresh",
                    reason_code="synthetic_refresh",
                    actor=None,
                    context=self.context,
                )
            total = public_course_registration_count(self.campaign)
        self.assertIsNotNone(total)
        assert total is not None
        self.assertEqual(total.count, 2)

    def test_quarantine_omits_and_database_failure_is_not_converted_to_zero(self) -> None:
        run = self._stage_validate_activate()
        run.state = CourseRegistrationCountSourceRun.State.QUARANTINED
        run.revision += 1
        run.save(update_fields=("state", "revision", "updated_at"))
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            self.assertIsNone(public_course_registration_count(self.campaign))
            with mock.patch(
                "courses.services.registration_counts."
                "CourseRegistrationCountSlot.objects.select_related",
                side_effect=RuntimeError("synthetic database failure"),
            ):
                with self.assertRaisesMessage(RuntimeError, "synthetic database failure"):
                    public_course_registration_count(self.campaign)

    def test_row_replacement_and_rollback_preserve_total(self) -> None:
        self._stage_validate_activate()
        native = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="native-after-cutover",
        )
        first = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="historical-one",
        )
        second = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="historical-two",
        )
        CourseRegistration.objects.filter(pk=first.pk).update(created_at=SOURCE_MINIMUM)
        CourseRegistration.objects.filter(pk=second.pk).update(created_at=SOURCE_MAXIMUM)
        slot = CourseRegistrationCountSlot.objects.get(campaign=self.campaign)
        replacement_id = uuid.uuid4()
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            slot = replace_baseline_with_rows(
                campaign_id=self.campaign.pk,
                row_replacement_revision_id=replacement_id,
                expected_slot_revision=slot.revision,
                reason_code="synthetic_rows",
                actor=None,
                context=self.context,
            )
            self.assertEqual(slot.mode, CourseRegistrationCountSlot.Mode.ROWS_ONLY)
            self.assertEqual(public_course_registration_count(self.campaign).count, 3)  # type: ignore[union-attr]
            slot = restore_baseline_from_rows(
                campaign_id=self.campaign.pk,
                expected_slot_revision=slot.revision,
                reason_code="synthetic_rows_rollback",
                actor=None,
                context=self.context,
            )
            self.assertEqual(slot.mode, CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE)
            self.assertEqual(public_course_registration_count(self.campaign).count, 3)  # type: ignore[union-attr]
        self.assertTrue(CourseRegistration.objects.filter(pk=native.pk).exists())
        replacement = AuditEvent.objects.get(action="courses.registration_count_baseline.replaced")
        restored = AuditEvent.objects.get(
            action="courses.registration_count_baseline.replacement_rolled_back"
        )
        self.assertEqual(replacement.changes["revision"], {"before": 1, "after": 2})
        self.assertEqual(restored.changes["revision"], {"before": 2, "after": 3})
        self.assertTrue(replacement.idempotency_key_hash)
        self.assertTrue(restored.idempotency_key_hash)

    def test_row_replacement_mismatch_changes_no_pointer_and_omits_total(self) -> None:
        self._stage_validate_activate()
        row = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="incomplete-historical-replacement",
        )
        CourseRegistration.objects.filter(pk=row.pk).update(created_at=SOURCE_MINIMUM)
        slot = CourseRegistrationCountSlot.objects.get(campaign=self.campaign)
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            with self.assertRaisesMessage(
                CourseRegistrationCountConflict, "replacement_reconciliation_mismatch"
            ):
                replace_baseline_with_rows(
                    campaign_id=self.campaign.pk,
                    row_replacement_revision_id=uuid.uuid4(),
                    expected_slot_revision=slot.revision,
                    reason_code="synthetic_mismatch",
                    actor=None,
                    context=self.context,
                )
            self.assertIsNone(public_course_registration_count(self.campaign))
        slot.refresh_from_db()
        self.assertEqual(slot.mode, CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE)
        self.assertIsNotNone(slot.active_baseline_revision_id)

    def test_null_recorded_cohort_is_rejected_without_target_write(self) -> None:
        self.source.unlink()
        self._write_source(
            registrations=((1, 1, None, SOURCE_MINIMUM.isoformat(), "synthetic-null"),)
        )
        registry = {"synthetic-course-count": self._registry_entry()}
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=registry):
            with self.assertRaisesMessage(CourseCountSourceError, "registration_cohort_missing"):
                stage_registered_source(
                    source_reference="synthetic-course-count",
                    reason_code="synthetic_reject",
                    actor=None,
                    context=self.context,
                )
        self.assertFalse(CourseRegistrationCountSourceRun.objects.exists())

    def test_registered_schema_drift_is_rejected_without_target_write(self) -> None:
        original_entry = self.registry["synthetic-course-count"]
        connection = sqlite3.connect(self.source)
        connection.execute(
            "ALTER TABLE courses_courseregistration ADD COLUMN unexpected_source_field TEXT"
        )
        connection.commit()
        connection.close()
        payload = self.source.read_bytes()
        drifted_entry = {
            **original_entry,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        }
        with override_settings(
            COURSE_REGISTRATION_COUNT_SOURCES={
                "synthetic-course-count": drifted_entry,
            }
        ):
            with self.assertRaisesMessage(CourseCountSourceError, "schema_contract_changed"):
                stage_registered_source(
                    source_reference="synthetic-course-count",
                    reason_code="synthetic_reject",
                    actor=None,
                    context=self.context,
                )
        self.assertFalse(CourseRegistrationCountSourceRun.objects.exists())

    def test_timestamp_span_and_nonregular_source_fail_before_target_write(self) -> None:
        for registrations, code in (
            (
                ((1, 1, 1, "2026-01-02T10:00:00", "synthetic-invalid"),),
                "registration_timestamp_naive",
            ),
            (
                ((1, 1, 1, "2026-01-12T10:00:00+00:00", "synthetic-invalid"),),
                "registration_after_cutoff",
            ),
            (
                (
                    (1, 1, 1, "2026-01-01T10:00:00+00:00", "synthetic-aware-one"),
                    (2, 1, 1, "2026-01-02T10:00:00", "synthetic-naive-middle"),
                    (3, 1, 1, "2026-01-03T10:00:00+00:00", "synthetic-aware-two"),
                ),
                "registration_timestamp_naive",
            ),
        ):
            with self.subTest(code=code):
                self._replace_source(registrations)
                with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
                    with self.assertRaisesMessage(CourseCountSourceError, code):
                        stage_registered_source(
                            source_reference="synthetic-course-count",
                            reason_code="synthetic_reject",
                            actor=None,
                            context=self.context,
                        )
                self.assertFalse(CourseRegistrationCountSourceRun.objects.exists())

        self._replace_source(((1, 1, 1, SOURCE_MINIMUM.isoformat(), "synthetic-first-cohort"),))
        connection = sqlite3.connect(self.source)
        connection.execute(
            "INSERT INTO courses_course(id, slug) VALUES (?, ?)",
            (2, "synthetic-source-other-cohort"),
        )
        connection.execute(
            "INSERT INTO courses_courseregistration"
            "(id, campaign_id, course_id, created_at, email_normalized) "
            "VALUES (?, ?, ?, ?, ?)",
            (2, 1, 2, SOURCE_MAXIMUM.isoformat(), "synthetic-second-cohort"),
        )
        connection.commit()
        connection.close()
        self.registry = {"synthetic-course-count": self._registry_entry()}
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            with self.assertRaisesMessage(CourseCountSourceError, "campaign_spans_cohorts"):
                stage_registered_source(
                    source_reference="synthetic-course-count",
                    reason_code="synthetic_reject",
                    actor=None,
                    context=self.context,
                )

        link = self.source.with_name("synthetic-source-link.sqlite3")
        link.symlink_to(self.source)
        linked_entry = {**self.registry["synthetic-course-count"], "path": str(link)}
        with override_settings(
            COURSE_REGISTRATION_COUNT_SOURCES={"synthetic-course-count": linked_entry}
        ):
            with self.assertRaisesMessage(CourseCountSourceError, "source_not_regular"):
                stage_registered_source(
                    source_reference="synthetic-course-count",
                    reason_code="synthetic_reject",
                    actor=None,
                    context=self.context,
                )
        self.assertFalse(CourseRegistrationCountSourceRun.objects.exists())

    def test_activation_rejects_target_pre_boundary_row_atomically(self) -> None:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, _replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
            run = validate_source(
                run.id,
                expected_revision=run.revision,
                reason_code="synthetic_validate",
                actor=None,
                context=self.context,
            )
            row = CourseRegistration.objects.create(
                campaign=self.campaign,
                course=self.course,
                email="pre-boundary",
            )
            CourseRegistration.objects.filter(pk=row.pk).update(created_at=SOURCE_MINIMUM)
            with self.assertRaisesMessage(
                CourseRegistrationCountConflict, "target_registration_before_cutover"
            ):
                activate_source(
                    run.id,
                    expected_revision=run.revision,
                    reason_code="synthetic_activate",
                    actor=None,
                    context=self.context,
                )
        run.refresh_from_db()
        self.assertEqual(run.state, CourseRegistrationCountSourceRun.State.VALIDATED)
        self.assertFalse(CourseRegistrationCountSlot.objects.exists())

    def test_failed_validation_quarantines_only_the_staged_candidate(self) -> None:
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            run, _replayed = stage_registered_source(
                source_reference="synthetic-course-count",
                reason_code="synthetic_stage",
                actor=None,
                context=self.context,
            )
            changed = Cohort.objects.create(
                slug="synthetic-validation-change",
                title="Validation change",
                description="Deterministic changed target.",
            )
            self.campaign.current_course = changed
            self.campaign.save(update_fields=("current_course", "updated_at"))
            with self.assertRaisesMessage(CourseRegistrationCountConflict, "target_cohort_changed"):
                validate_source(
                    run.id,
                    expected_revision=run.revision,
                    reason_code="synthetic_validation_failed",
                    actor=None,
                    context=self.context,
                )
        run.refresh_from_db()
        self.assertEqual(run.state, CourseRegistrationCountSourceRun.State.QUARANTINED)
        self.assertEqual(
            set(run.count_revisions.values_list("state", flat=True)),
            {CourseRegistrationCountRevision.State.QUARANTINED},
        )
        audit = AuditEvent.objects.get(
            action="courses.registration_count_baseline.validation_failed"
        )
        self.assertEqual(audit.outcome, AuditEvent.Outcome.FAILED)
        self.assertEqual(audit.metadata["failure_code"], "target_cohort_changed")
        self.assertEqual(
            audit.changes,
            {
                "state": {"before": "staged", "after": "quarantined"},
                "revision": {"before": 1, "after": 2},
            },
        )
        self.assertTrue(audit.idempotency_key_hash)

    def test_copied_page_preserves_zero_one_many_and_edition_suffix(self) -> None:
        scenarios = (
            ((), None),
            (((1, 1, 1, SOURCE_MINIMUM.isoformat(), "synthetic-one"),), 1),
            (
                (
                    (1, 1, 1, SOURCE_MINIMUM.isoformat(), "synthetic-one"),
                    (2, 1, 1, SOURCE_MAXIMUM.isoformat(), "synthetic-two"),
                ),
                2,
            ),
        )
        registration_url = reverse(
            "registration_campaign",
            kwargs={"campaign_slug": self.campaign.slug},
        )
        for registrations, expected in scenarios:
            with self.subTest(expected=expected):
                CourseRegistrationCountSlot.objects.all().delete()
                CourseRegistrationCountRevision.objects.all().delete()
                CourseRegistrationCountSourceRun.objects.all().delete()
                self._replace_source(registrations)
                self._stage_validate_activate()
                with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
                    response = self.client.get(registration_url)
                self.assertEqual(response.status_code, 200)
                if expected is None:
                    self.assertNotContains(response, "already registered")
                else:
                    self.assertContains(response, f"{expected} already registered")
                    self.assertContains(response, "for 2026 cohort")

    @override_settings(
        DATAMAILER_URL="",
        DATAMAILER_API_KEY="",
        DATAMAILER_CLIENT="",
        DATAMAILER_AUDIENCE="",
    )
    def test_successful_form_increments_once_and_duplicate_does_not(self) -> None:
        self._replace_source(((1, 1, 1, SOURCE_MINIMUM.isoformat(), "synthetic-baseline"),))
        self._stage_validate_activate()
        payload = {
            "email": "synthetic-native@invalid.example",
            "name": "Synthetic learner",
            "country": "Germany",
            "role": CourseRegistration.Role.DATA_ENGINEER,
            "accepted_newsletter": "on",
        }
        registration_url = reverse(
            "registration_campaign",
            kwargs={"campaign_slug": self.campaign.slug},
        )
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(registration_url, payload)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "2 already registered")
            self.assertContains(response, "for 2026 cohort")
            duplicate = self.client.post(registration_url, payload)
            self.assertEqual(duplicate.status_code, 200)
            self.assertContains(duplicate, "2 already registered")
            self.assertContains(duplicate, "for 2026 cohort")
        self.assertEqual(CourseRegistration.objects.count(), 1)
