"""Tests for the relocated one-time account-merge reconciliation logic.

``scripts.prod.account_reconciliation`` (dry-run, apply, rollback-check) and
``scripts.prod.import_account_reconciliation`` (its CLI, which replaced the
retired ``accounts.management.commands.reconcile_accounts``) used to live in
``accounts/`` -- see both modules' docstrings for why the merge logic moved
out while ``AccountReconciliationRun``, ``AccountIdentityAlias`` and
``AccountIdentityQuarantine`` stayed registered under ``accounts`` as Django
models. These tests moved with the logic (from ``test_single_identity.py``
and the retired ``test_identity_commands.py``); everything else that was in
``test_single_identity.py`` (identity model basics, authentication, social
linking, session lifecycle, and so on) stayed there unchanged.
"""

from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.db import IntegrityError
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from accounts.identity_resolution import resolve_durable_user_id
from accounts.models import (
    AccountIdentityAlias,
    AccountIdentityQuarantine,
    AccountReconciliationRun,
    CustomUser,
    Token,
)
from accounts.tests.test_single_identity import create_verified_user
from core.models import AuditEvent, StaffSession
from courses.models import (
    Cohort,
    Enrollment,
    Homework,
    PeerReview,
    Project,
    ProjectSubmission,
    Submission,
)
from management_auth.models import APIPrincipal
from scripts.prod.account_reconciliation import (
    ReconciliationBlocked,
    ReconciliationError,
    apply_reviewed_mapping,
    dry_run_reconciliation,
    parse_mapping_document,
    validate_rollback_window,
)

SNAPSHOT_ID = "a" * 64


def mapping_document(
    *,
    source: CustomUser,
    survivor: CustomUser,
    field_decisions: dict[str, str] | None = None,
    authority_decision: str = "",
    evidence: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "snapshot_id": SNAPSHOT_ID,
        "review_reference": "synthetic-review-100",
        "mappings": [
            {
                "source_user_id": source.pk,
                "survivor_user_id": survivor.pk,
                "ownership_evidence": evidence or ["verified_normalized_email"],
                "field_decisions": field_decisions or {},
                "authority_decision": authority_decision,
            }
        ],
    }


class ReconciliationDryRunTests(TestCase):
    def test_dry_run_is_deterministic_redacted_and_no_write(self) -> None:
        first = create_verified_user(
            username="learner@example.invalid",
            email="Learner@Example.Invalid",
            verified_email="Learner@Example.Invalid",
        )
        second = create_verified_user(
            username="legacy-learner",
            email="learner@example.invalid",
            verified_email="learner@example.invalid",
            is_staff=True,
        )
        course = Cohort.objects.create(
            slug="identity-course",
            title="Identity course",
            description="Synthetic course",
        )
        Enrollment.objects.create(student=first, course=course)
        Enrollment.objects.create(student=second, course=course)
        SocialAccount.objects.create(
            user=first,
            provider="github",
            uid="synthetic-provider-a",
            extra_data={"access_token": "must-not-appear"},
        )
        SocialAccount.objects.create(
            user=second,
            provider="github",
            uid="synthetic-provider-b",
            extra_data={"access_token": "also-must-not-appear"},
        )
        before = {
            "aliases": AccountIdentityAlias.objects.count(),
            "quarantines": AccountIdentityQuarantine.objects.count(),
            "runs": AccountReconciliationRun.objects.count(),
        }

        first_report = dry_run_reconciliation(snapshot_id=SNAPSHOT_ID)
        second_report = dry_run_reconciliation(snapshot_id=SNAPSHOT_ID)

        self.assertEqual(first_report, second_report)
        self.assertFalse(first_report["write_performed"])
        self.assertFalse(first_report["outbound_side_effects"])
        self.assertFalse(first_report["newest_last_login_authoritative"])
        group = first_report["candidate_groups"][0]
        self.assertEqual(group["source_user_ids"], [first.pk, second.pk])
        self.assertIsNone(group["automatic_survivor"])
        self.assertIn("authority_collision", group["risk_codes"])
        self.assertIn("provider_uid_conflict", group["risk_codes"])
        self.assertIn("courses.enrollment_collision", group["risk_codes"])
        rendered = json.dumps(first_report, sort_keys=True)
        for forbidden in (
            "learner@example.invalid",
            "synthetic-provider-a",
            "synthetic-provider-b",
            "must-not-appear",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(
            before,
            {
                "aliases": AccountIdentityAlias.objects.count(),
                "quarantines": AccountIdentityQuarantine.objects.count(),
                "runs": AccountReconciliationRun.objects.count(),
            },
        )

    def test_username_email_overlap_is_reviewed_not_auto_merged(self) -> None:
        first = CustomUser.objects.create_user(
            username="overlap@example.invalid",
            email="first@example.invalid",
        )
        second = CustomUser.objects.create_user(
            username="second",
            email="overlap@example.invalid",
        )

        report = dry_run_reconciliation(snapshot_id=SNAPSHOT_ID)

        self.assertEqual(
            report["candidate_groups"][0]["source_user_ids"],
            [first.pk, second.pk],
        )
        self.assertIn(
            "identifier",
            report["candidate_groups"][0]["evidence"],
        )


class ReviewedReconciliationTests(TestCase):
    def setUp(self) -> None:
        self.source = create_verified_user(
            username="legacy-learner",
            email="Learner@Example.Invalid",
            verified_email="Learner@Example.Invalid",
            certificate_name="Synthetic Learner",
        )
        self.survivor = create_verified_user(
            username="durable-learner",
            email="learner@example.invalid",
            verified_email="learner@example.invalid",
        )
        self.course = Cohort.objects.create(
            slug="durable-identity",
            title="Durable identity",
            description="Synthetic reconciliation fixture",
        )

    def plan(self, **kwargs):
        document = mapping_document(
            source=self.source,
            survivor=self.survivor,
            field_decisions={"certificate_name": "source"},
            **kwargs,
        )
        return parse_mapping_document(document)

    def test_apply_reparents_course_and_social_relations_once(self) -> None:
        enrollment = Enrollment.objects.create(
            student=self.source,
            course=self.course,
            display_on_leaderboard=False,
            display_public_profile=False,
            disable_learning_in_public=True,
            total_score=29,
            certificate_name="Synthetic certificate",
            certificate_url="https://example.invalid/certificate",
        )
        homework = Homework.objects.create(
            slug="identity-homework",
            course=self.course,
            title="Identity homework",
            due_date=timezone.now(),
        )
        submission = Submission.objects.create(
            homework=homework,
            student=self.source,
            enrollment=enrollment,
            total_score=11,
        )
        project = Project.objects.create(
            course=self.course,
            slug="identity-project",
            title="Identity project",
            submission_due_date=timezone.now(),
            peer_review_due_date=timezone.now(),
        )
        project_submission = ProjectSubmission.objects.create(
            project=project,
            student=self.source,
            enrollment=enrollment,
            github_link="https://example.invalid/source-project",
            commit_id="a" * 40,
            project_score=9,
            peer_review_score=3,
            total_score=12,
            passed=True,
        )
        reviewer_user = create_verified_user(
            username="synthetic-reviewer",
            email="synthetic-reviewer@example.invalid",
        )
        reviewer_enrollment = Enrollment.objects.create(
            student=reviewer_user,
            course=self.course,
        )
        reviewer_submission = ProjectSubmission.objects.create(
            project=project,
            student=reviewer_user,
            enrollment=reviewer_enrollment,
            github_link="https://example.invalid/reviewer-project",
            commit_id="b" * 40,
        )
        peer_review = PeerReview.objects.create(
            submission_under_evaluation=project_submission,
            reviewer=reviewer_submission,
            note_to_peer="Synthetic review",
        )
        social = SocialAccount.objects.create(
            user=self.source,
            provider="github",
            uid="synthetic-github-owner",
            extra_data={"login": "synthetic-only"},
        )
        unverified_address = EmailAddress.objects.create(
            user=self.source,
            email="unverified-alias@example.invalid",
            verified=False,
            primary=False,
        )
        token = Token.objects.create(key="synthetic-token", user=self.source)
        session_client = Client()
        session_client.force_login(self.source)
        clean_user = create_verified_user(
            username="unrelated",
            email="unrelated@example.invalid",
        )
        unrelated_client = Client()
        unrelated_client.force_login(clean_user)
        unrelated_session_key = unrelated_client.session.session_key

        report = apply_reviewed_mapping(self.plan())

        self.source.refresh_from_db()
        self.survivor.refresh_from_db()
        enrollment.refresh_from_db()
        submission.refresh_from_db()
        project_submission.refresh_from_db()
        peer_review.refresh_from_db()
        social.refresh_from_db()
        unverified_address.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.source.pk, report["applied_source_user_ids"][0])
        self.assertEqual(
            self.source.identity_state,
            CustomUser.IdentityState.ABSORBED,
        )
        self.assertTrue(self.source.is_active)
        self.assertEqual(
            self.survivor.identity_state,
            CustomUser.IdentityState.ACTIVE,
        )
        self.assertEqual(self.survivor.certificate_name, "Synthetic Learner")
        self.assertEqual(enrollment.student_id, self.survivor.pk)
        self.assertFalse(enrollment.display_on_leaderboard)
        self.assertFalse(enrollment.display_public_profile)
        self.assertTrue(enrollment.disable_learning_in_public)
        self.assertEqual(enrollment.total_score, 29)
        self.assertEqual(enrollment.certificate_name, "Synthetic certificate")
        self.assertEqual(
            enrollment.certificate_url,
            "https://example.invalid/certificate",
        )
        self.assertEqual(submission.student_id, self.survivor.pk)
        self.assertEqual(submission.total_score, 11)
        self.assertEqual(project_submission.student_id, self.survivor.pk)
        self.assertEqual(project_submission.total_score, 12)
        self.assertEqual(project_submission.peer_review_score, 3)
        self.assertTrue(project_submission.passed)
        self.assertEqual(
            peer_review.submission_under_evaluation_id,
            project_submission.pk,
        )
        self.assertEqual(social.user_id, self.survivor.pk)
        self.assertEqual(unverified_address.user_id, self.source.pk)
        self.assertEqual(token.user_id, self.source.pk)
        self.assertEqual(
            AccountIdentityAlias.objects.get(source_user_id=self.source.pk).survivor_id,
            self.survivor.pk,
        )
        self.assertFalse(report["privilege_union"])
        self.assertFalse(report["consent_union"])

        session_response = session_client.get("/api/v1/account/identity/")
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(session_response.json()["account_id"], self.survivor.pk)
        self.assertEqual(
            session_client.session["_auth_user_id"],
            str(self.survivor.pk),
        )
        from django.contrib.sessions.models import Session

        self.assertTrue(Session.objects.filter(session_key=unrelated_session_key).exists())
        compatibility = self.client.get(
            "/api/account/identity/",
            HTTP_AUTHORIZATION="Token synthetic-token",
        )
        self.assertEqual(compatibility.status_code, 200)
        self.assertEqual(compatibility.json()["account_id"], self.survivor.pk)

        # Proof 1 -- idempotency: replaying the exact same
        # (snapshot_id, mapping_checksum, mode=apply) returns the first run's
        # cached result rather than re-merging.
        replay = apply_reviewed_mapping(self.plan())
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["run_id"], report["run_id"])
        self.assertEqual(AccountIdentityAlias.objects.count(), 1)
        self.assertEqual(AccountReconciliationRun.objects.count(), 1)
        self.assertTrue(
            AuditEvent.objects.filter(action="accounts.identity.merge_succeeded").exists()
        )

    def test_rollback_check_keeps_post_cutover_writes_and_alias(self) -> None:
        apply_reviewed_mapping(self.plan())
        post_cutover = Enrollment.objects.create(
            student=self.survivor,
            course=self.course,
        )

        report = validate_rollback_window(self.plan())

        self.assertTrue(report["post_cutover_writes_retained"])
        self.assertFalse(report["relationships_reversed"])
        self.assertFalse(report["sessions_globally_flushed"])
        self.assertTrue(Enrollment.objects.filter(pk=post_cutover.pk).exists())
        self.assertEqual(resolve_durable_user_id(self.source.pk), self.survivor.pk)

    def test_idempotent_replay_fails_if_absorbed_state_was_tampered(self) -> None:
        plan = self.plan()
        apply_reviewed_mapping(plan)
        self.source.identity_state = CustomUser.IdentityState.LEGACY
        self.source.save(update_fields=("identity_state",))

        with self.assertRaises(ReconciliationError):
            apply_reviewed_mapping(plan)

    def test_same_course_history_fails_closed_into_quarantine(self) -> None:
        Enrollment.objects.create(student=self.source, course=self.course)
        Enrollment.objects.create(student=self.survivor, course=self.course)

        with self.assertRaises(ReconciliationBlocked) as raised:
            apply_reviewed_mapping(self.plan())

        self.assertIn(
            "courses.enrollment_collision",
            raised.exception.conflicts[0]["reason_codes"],
        )
        self.assertEqual(AccountIdentityAlias.objects.count(), 0)
        quarantine = AccountIdentityQuarantine.objects.get()
        self.assertEqual(
            quarantine.source_user_ids,
            [self.source.pk, self.survivor.pk],
        )

    def test_profile_difference_requires_reviewed_field_decision(self) -> None:
        document = mapping_document(
            source=self.source,
            survivor=self.survivor,
        )

        with self.assertRaises(ReconciliationBlocked) as raised:
            apply_reviewed_mapping(parse_mapping_document(document))

        self.assertIn(
            "field_decision_required:certificate_name",
            raised.exception.conflicts[0]["reason_codes"],
        )

    def test_conflicting_provider_uids_fail_closed(self) -> None:
        SocialAccount.objects.create(
            user=self.source,
            provider="github",
            uid="source-provider-uid",
        )
        SocialAccount.objects.create(
            user=self.survivor,
            provider="github",
            uid="survivor-provider-uid",
        )

        with self.assertRaises(ReconciliationBlocked) as raised:
            apply_reviewed_mapping(self.plan())

        self.assertIn(
            "provider_uid_conflict",
            raised.exception.conflicts[0]["reason_codes"],
        )

    def test_authority_collision_uses_survivor_only_without_union(self) -> None:
        self.source.is_staff = True
        self.source.is_superuser = True
        self.source.save(update_fields=("is_staff", "is_superuser"))
        staff_session = StaffSession.objects.create(
            user=self.source,
            authenticated_at=timezone.now(),
        )
        source_principal = APIPrincipal.objects.create(
            kind=APIPrincipal.Kind.HUMAN,
            name="Synthetic source principal",
            identity_snapshot="synthetic-source-principal",
            user=self.source,
        )
        plan = self.plan(authority_decision="survivor_only")

        apply_reviewed_mapping(plan)

        self.survivor.refresh_from_db()
        staff_session.refresh_from_db()
        source_principal.refresh_from_db()
        self.assertFalse(self.survivor.is_staff)
        self.assertFalse(self.survivor.is_superuser)
        self.assertEqual(staff_session.user_id, self.survivor.pk)
        self.assertEqual(self.survivor.groups.count(), 0)
        self.assertFalse(source_principal.is_active)
        self.assertEqual(source_principal.user_id, self.source.pk)
        self.assertFalse(APIPrincipal.objects.filter(user_id=self.survivor.pk).exists())


class ReconciliationConcurrencyTests(TestCase):
    """Proof 2 -- concurrency safety, with a real race, not a read of the code.

    A genuinely simultaneous OS-thread race was tried here first and
    rejected: production requires PostgreSQL (``core.bootstrap`` fails
    closed on anything else for a deployed environment), where row-level
    locking resolves two concurrent applies in milliseconds. The test
    database is SQLite, which locks the *whole file* for a writer's
    transaction; two real threads racing SQLite's coarse lock produced
    ``OperationalError: database is locked`` under ordinary test-suite CPU
    load -- a SQLite test-harness artifact, not a property of the
    concurrency guarantee this is supposed to prove.

    So this proves the same property a different, deterministic way: it
    forces the exact race window a real second caller would be in --
    *its own* idempotency read (the ``AccountReconciliationRun`` lookup at
    the very top of ``apply_reviewed_mapping``) happens before the winner's
    row is visible, but by the time it reaches its own
    ``AccountReconciliationRun.objects.create(...)``, the winner's row is
    already committed. Only that one read is patched, to return empty on
    its first call, simulating "this caller's read happened one instant
    before the winner's write" -- every other step (conflict checks, the
    merge writes, and critically the final ``.create()`` call) runs for
    real, against the real database, so the real ``UniqueConstraint`` is
    what actually raises ``IntegrityError``, and the real ``except
    IntegrityError`` handler in ``apply_reviewed_mapping`` is what actually
    recovers it -- engine-independent, and not a mock of the property being
    proven.
    """

    def test_a_racing_second_caller_gets_the_winners_result_not_a_second_merge(self) -> None:
        source = create_verified_user(
            username="race-source",
            email="race-source@example.invalid",
            verified_email="race-source@example.invalid",
        )
        survivor = create_verified_user(
            username="race-survivor",
            email="race-survivor@example.invalid",
            verified_email="race-survivor@example.invalid",
        )
        # Distinct addresses, like ReconciliationTransactionalFailureTests --
        # manual_verified_ownership evidence, not the automatic
        # verified_normalized_email match, is the reviewed disposition for
        # that shape.
        plan = parse_mapping_document(
            mapping_document(
                source=source,
                survivor=survivor,
                evidence=["manual_verified_ownership"],
            )
        )

        import scripts.prod.account_reconciliation as recon

        # The real, winning apply -- nothing patched.
        first_report = apply_reviewed_mapping(plan)
        self.assertFalse(first_report["idempotent_replay"])

        real_filter = recon.AccountReconciliationRun.objects.filter
        calls = {"n": 0}

        def racing_first_read(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # The second caller's own idempotency check, as if its read
                # happened before the first caller's transaction committed.
                return recon.AccountReconciliationRun.objects.none()
            # Every later call (the except-IntegrityError re-query included)
            # sees the database exactly as it really is.
            return real_filter(*args, **kwargs)

        with patch.object(
            recon.AccountReconciliationRun.objects,
            "filter",
            side_effect=racing_first_read,
        ):
            second_report = apply_reviewed_mapping(plan)

        # The racing caller never re-merged -- it received the winner's
        # cached result, via the real UniqueConstraint's IntegrityError and
        # the real except-handler, not its own early idempotency check.
        self.assertTrue(second_report["idempotent_replay"])
        self.assertEqual(second_report["run_id"], first_report["run_id"])
        self.assertEqual(
            AccountReconciliationRun.objects.filter(
                source_snapshot_id=plan.snapshot_id,
                mapping_checksum=plan.checksum,
                mode=AccountReconciliationRun.Mode.APPLY,
            ).count(),
            1,
        )
        self.assertEqual(
            AccountIdentityAlias.objects.filter(source_user_id=source.pk).count(),
            1,
        )
        source.refresh_from_db()
        survivor.refresh_from_db()
        self.assertEqual(source.identity_state, CustomUser.IdentityState.ABSORBED)
        self.assertEqual(survivor.identity_state, CustomUser.IdentityState.ACTIVE)


class ReconciliationTransactionalFailureTests(TransactionTestCase):
    def setUp(self) -> None:
        self.source = create_verified_user(
            username="transaction-source",
            email="transaction-source@example.invalid",
        )
        self.survivor = create_verified_user(
            username="transaction-survivor",
            email="transaction-survivor@example.invalid",
        )

    def plan(
        self,
        *,
        source_user_id: int | None = None,
        survivor_user_id: int | None = None,
    ):
        document = mapping_document(
            source=self.source,
            survivor=self.survivor,
        )
        mapping = document["mappings"][0]
        mapping["source_user_id"] = source_user_id or self.source.pk
        mapping["survivor_user_id"] = survivor_user_id or self.survivor.pk
        mapping["ownership_evidence"] = ["manual_verified_ownership"]
        return parse_mapping_document(document)

    def assert_no_merge_writes(self) -> None:
        self.source.refresh_from_db()
        self.assertNotEqual(
            self.source.identity_state,
            CustomUser.IdentityState.ABSORBED,
        )
        self.assertFalse(AccountIdentityAlias.objects.exists())
        self.assertFalse(AccountReconciliationRun.objects.exists())

    def test_missing_survivor_uses_source_audit_authority_atomically(self) -> None:
        missing_survivor_id = 999999

        with self.assertRaises(ReconciliationBlocked) as raised:
            apply_reviewed_mapping(self.plan(survivor_user_id=missing_survivor_id))

        self.assertEqual(
            raised.exception.conflicts[0]["reason_codes"],
            ["account_missing"],
        )
        self.assert_no_merge_writes()
        quarantine = AccountIdentityQuarantine.objects.get()
        self.assertEqual(
            quarantine.source_user_ids,
            [self.source.pk, missing_survivor_id],
        )
        audit = AuditEvent.objects.get(action="accounts.identity.merge_denied")
        self.assertEqual(audit.actor_id, self.source.pk)
        self.assertEqual(audit.actor_ref, f"user:{self.source.pk}")

    def test_absorbed_survivor_never_becomes_the_denied_audit_actor(self) -> None:
        self.survivor.identity_state = CustomUser.IdentityState.ABSORBED
        self.survivor.save(update_fields=("identity_state",))

        with self.assertRaises(ReconciliationBlocked):
            apply_reviewed_mapping(self.plan())

        self.assert_no_merge_writes()
        audit = AuditEvent.objects.get(action="accounts.identity.merge_denied")
        self.assertEqual(audit.actor_id, self.source.pk)
        self.assertNotEqual(audit.actor_id, self.survivor.pk)

    def test_invalid_source_and_survivor_use_system_audit_authority(self) -> None:
        self.source.identity_state = CustomUser.IdentityState.QUARANTINED
        self.source.save(update_fields=("identity_state",))
        self.survivor.identity_state = CustomUser.IdentityState.ABSORBED
        self.survivor.save(update_fields=("identity_state",))

        with self.assertRaises(ReconciliationBlocked):
            apply_reviewed_mapping(self.plan())

        self.assert_no_merge_writes()
        audit = AuditEvent.objects.get(action="accounts.identity.merge_denied")
        self.assertIsNone(audit.actor_id)
        self.assertEqual(
            audit.actor_ref,
            "system:account-reconciliation",
        )

    def test_missing_source_uses_valid_survivor_audit_authority(self) -> None:
        missing_source_id = 999998

        with self.assertRaises(ReconciliationBlocked):
            apply_reviewed_mapping(self.plan(source_user_id=missing_source_id))

        self.assert_no_merge_writes()
        audit = AuditEvent.objects.get(action="accounts.identity.merge_denied")
        self.assertEqual(audit.actor_id, self.survivor.pk)

    def test_audit_integrity_failure_rolls_back_partial_quarantine(self) -> None:
        with (
            patch(
                "core.audit.record_audit_event",
                side_effect=IntegrityError("raw database detail"),
            ),
            self.assertRaises(ReconciliationBlocked) as raised,
        ):
            apply_reviewed_mapping(self.plan(survivor_user_id=999999))

        self.assertEqual(
            str(raised.exception),
            "account reconciliation requires quarantine review",
        )
        self.assert_no_merge_writes()
        self.assertFalse(AccountIdentityQuarantine.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_apply_integrity_failure_is_redacted_and_fails_closed(self) -> None:
        with (
            patch(
                "scripts.prod.account_reconciliation._apply_one_mapping",
                side_effect=IntegrityError("raw database detail"),
            ),
            self.assertRaises(ReconciliationBlocked) as raised,
        ):
            apply_reviewed_mapping(self.plan())

        rendered = json.dumps(raised.exception.conflicts)
        self.assertNotIn("raw database detail", rendered)
        self.assertEqual(
            raised.exception.conflicts[0]["reason_codes"],
            ["reconciliation_integrity_conflict"],
        )
        self.assert_no_merge_writes()
        self.assertEqual(AccountIdentityQuarantine.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="accounts.identity.merge_denied").count(),
            1,
        )

    def test_stale_survivor_state_fails_closed_before_reparenting(self) -> None:
        from scripts.prod.account_reconciliation import _profile_changes

        def change_survivor_after_snapshot(*, source, survivor, mapping):
            changes = _profile_changes(
                source=source,
                survivor=survivor,
                mapping=mapping,
            )
            CustomUser.objects.filter(pk=survivor.pk).update(
                last_name="Concurrent change",
            )
            return changes

        with (
            patch(
                "scripts.prod.account_reconciliation._profile_changes",
                side_effect=change_survivor_after_snapshot,
            ),
            self.assertRaises(ReconciliationBlocked) as raised,
        ):
            apply_reviewed_mapping(self.plan())

        self.assertEqual(
            raised.exception.conflicts[0]["reason_codes"],
            ["reconciliation_integrity_conflict"],
        )
        self.assert_no_merge_writes()
        self.survivor.refresh_from_db()
        self.assertEqual(self.survivor.last_name, "")
        self.assertEqual(AccountIdentityQuarantine.objects.count(), 1)

    def test_script_writes_a_safe_report_for_a_missing_survivor(self) -> None:
        """The CLI end to end: a real invocation of ``main()``, not just the
        underlying function -- proving the script itself runs and produces
        the same redacted, quarantined report the retired management command
        used to.
        """

        from django.db import connection

        from scripts.prod.import_account_reconciliation import main

        artifact_directory = Path(".tmp/issue-100-tests")
        artifact_directory.mkdir(parents=True, exist_ok=True)
        mapping_path = artifact_directory / "missing-survivor-mapping.json"
        report_path = artifact_directory / "missing-survivor-report.json"
        self.addCleanup(mapping_path.unlink, missing_ok=True)
        self.addCleanup(report_path.unlink, missing_ok=True)
        document = self.plan(survivor_user_id=999999).canonical_document
        mapping_path.write_text(json.dumps(document), encoding="utf-8")
        mapping_path.chmod(0o600)

        exit_code = main(
            [
                "--database",
                str(connection.settings_dict["NAME"]),
                "--snapshot-id",
                SNAPSHOT_ID,
                "--mapping",
                str(mapping_path),
                "--apply",
                "--output",
                str(report_path),
            ]
        )

        self.assertEqual(exit_code, 1)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "quarantined")
        self.assertEqual(
            report["conflicts"][0]["reason_codes"],
            ["account_missing"],
        )
        self.assertNotIn("IntegrityError", json.dumps(report))


class ImportAccountReconciliationCliArtifactTests(TestCase):
    """Moved from the retired ``accounts/tests/test_identity_commands.py``,
    which called ``call_command("reconcile_accounts", ...)``. The command is
    gone; ``scripts.prod.import_account_reconciliation.main`` is what a real
    operator invocation now runs, so these test that directly.
    """

    def _database_path(self) -> str:
        from django.db import connection

        return str(connection.settings_dict["NAME"])

    def test_dry_run_report_is_restricted_redacted_and_project_local(self) -> None:
        from scripts.prod.import_account_reconciliation import main

        artifact_root = Path(settings.BASE_DIR) / ".tmp"
        artifact_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="identity-command-",
            dir=artifact_root,
        ) as directory:
            output = Path(directory) / "dry-run.json"

            exit_code = main(
                [
                    "--database",
                    self._database_path(),
                    "--snapshot-id",
                    SNAPSHOT_ID,
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["write_performed"])
            self.assertFalse(report["outbound_side_effects"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)

    def test_mapping_must_be_restricted_and_artifacts_cannot_escape_tmp(self) -> None:
        from scripts.prod.import_account_reconciliation import main

        artifact_root = Path(settings.BASE_DIR) / ".tmp"
        artifact_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="identity-mapping-",
            dir=artifact_root,
        ) as directory:
            mapping = Path(directory) / "mapping.json"
            mapping.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "snapshot_id": SNAPSHOT_ID,
                        "review_reference": "synthetic-command-review",
                        "mappings": [],
                    }
                ),
                encoding="utf-8",
            )
            mapping.chmod(0o644)

            database = self._database_path()
            restricted_mapping_output = io.StringIO()
            with contextlib.redirect_stdout(restricted_mapping_output):
                exit_code = main(
                    [
                        "--database",
                        database,
                        "--snapshot-id",
                        SNAPSHOT_ID,
                        "--mapping",
                        str(mapping),
                        "--apply",
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("permissions", restricted_mapping_output.getvalue())

            escaping_output_path = Path(settings.BASE_DIR) / "identity-report.json"
            escaping_output = io.StringIO()
            with contextlib.redirect_stdout(escaping_output):
                exit_code = main(
                    [
                        "--database",
                        database,
                        "--snapshot-id",
                        SNAPSHOT_ID,
                        "--output",
                        str(escaping_output_path),
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("project-local .tmp", escaping_output.getvalue())
            self.assertFalse(escaping_output_path.exists())
