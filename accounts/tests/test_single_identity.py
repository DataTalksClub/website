from __future__ import annotations

import json
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
)
from django.utils import timezone

from accounts.auth import ConsolidatingSocialAccountAdapter
from accounts.identity_inventory import account_inventory
from accounts.identity_resolution import resolve_durable_user_id
from accounts.models import (
    AccountIdentityAlias,
    AccountIdentityQuarantine,
    AccountReconciliationRun,
    CustomUser,
    Token,
)
from accounts.navigation import SAFE_ACCOUNT_DESTINATION, safe_next_path
from accounts.reconciliation import (
    ReconciliationBlocked,
    ReconciliationError,
    apply_reviewed_mapping,
    dry_run_reconciliation,
    parse_mapping_document,
    validate_rollback_window,
)
from accounts.studio_roles import synchronize_studio_roles
from content.review_projection import review_projection
from core.models import AuditEvent, StaffSession
from courses.models import (
    Course,
    Enrollment,
    Homework,
    PeerReview,
    Project,
    ProjectSubmission,
    Submission,
)
from management_api.authentication import authenticate as authenticate_management
from management_auth.constants import DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.tokens import encode_secret, generate_token
from review_import.manifest import is_sensitive_table

SNAPSHOT_ID = "a" * 64
TRANSITION_BYPASS_NEXT_VALUES = (
    "/courses/../accounts/continue/",
    "/courses/%2e%2e/accounts/login/",
    "/courses/%2E%2e/accounts/logout/",
    "/courses/.%2E/accounts/github/login/callback/",
    "/courses/%2e./accounts/github/login/",
    "/courses/%252e%252e/accounts/continue/",
    "%2Fcourses%2F%252E%252e%2Faccounts%2Flogin%2F",
    "/courses\\..\\accounts\\logout\\",
    "/courses/%5c..%5caccounts%5ccontinue/",
    "/courses/%255C..%255caccounts%255clogin/",
    "/courses//../accounts//logout/",
    "../../accounts/login/",
    "//testserver/accounts/login/",
    "\\\\testserver\\accounts\\login\\",
    "/%2ftestserver/accounts/logout/",
    "https://attacker.invalid/accounts/login/",
    "https%3A%2F%2Fattacker.invalid/accounts/login/",
    "/courses/%0d%0a/accounts/login/",
    "/courses/%ZZ/accounts/login/",
)


def create_verified_user(
    *,
    username: str,
    email: str,
    verified_email: str | None = None,
    password: str = "synthetic-password",
    **fields,
) -> CustomUser:
    user = CustomUser.objects.create_user(
        username=username,
        email=email,
        password=password,
        **fields,
    )
    EmailAddress.objects.create(
        user=user,
        email=verified_email or email,
        verified=True,
        primary=True,
    )
    return user


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


class SingleIdentityModelTests(TestCase):
    def test_adopted_user_and_table_identity_remain_authoritative(self) -> None:
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.CustomUser")
        self.assertEqual(CustomUser._meta.db_table, "accounts_customuser")
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS, ["accounts.backends.DurableAccountBackend"]
        )

    def test_email_normalization_is_expand_only_and_active_identity_is_unique(self) -> None:
        first = CustomUser.objects.create_user(
            username="first",
            email="  Learner@Example.Invalid ",
        )
        second = CustomUser.objects.create_user(
            username="second",
            email="learner@example.invalid",
        )
        self.assertEqual(first.normalized_email, "learner@example.invalid")
        self.assertEqual(second.normalized_email, "learner@example.invalid")
        self.assertEqual(first.identity_state, CustomUser.IdentityState.LEGACY)

        first.identity_state = CustomUser.IdentityState.ACTIVE
        first.save(update_fields=("identity_state",))
        second.identity_state = CustomUser.IdentityState.ACTIVE
        with self.assertRaises(IntegrityError), transaction.atomic():
            second.save(update_fields=("identity_state",))

    def test_alias_resolves_old_id_without_replacing_source_row(self) -> None:
        source = CustomUser.objects.create_user(username="source")
        survivor = CustomUser.objects.create_user(username="survivor")
        AccountIdentityAlias.objects.create(
            source_user_id=source.pk,
            survivor=survivor,
            source_snapshot_id=SNAPSHOT_ID,
            mapping_checksum="b" * 64,
            review_reference="synthetic-review-100",
        )

        self.assertEqual(resolve_durable_user_id(source.pk), survivor.pk)
        self.assertTrue(CustomUser.objects.filter(pk=source.pk).exists())


class SafeNextCanonicalizationTests(SimpleTestCase):
    factory = RequestFactory()

    def safe_next(
        self,
        candidate: str,
        *,
        path: str = "/accounts/continue/",
    ) -> str:
        query = urlencode({"next": candidate})
        request = self.factory.get(f"{path}?{query}")
        return safe_next_path(request)

    def test_browser_equivalent_transition_bypasses_fall_back(self) -> None:
        for candidate in TRANSITION_BYPASS_NEXT_VALUES:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    self.safe_next(candidate),
                    SAFE_ACCOUNT_DESTINATION,
                )

    def test_legitimate_local_paths_keep_query_and_fragment(self) -> None:
        cases = (
            (
                "/courses/ai-dev-tools/?tab=overview&view=full#module-1",
                "/courses/ai-dev-tools/?tab=overview&view=full#module-1",
            ),
            (
                "/courses/guides/../ai-dev-tools//?tab=overview#module-1",
                "/courses/ai-dev-tools/?tab=overview#module-1",
            ),
            (
                "../../courses/?q=one%20two#catalog",
                "/courses/?q=one%20two#catalog",
            ),
            (
                "/blog/post.html?utm_source=account#faq",
                "/blog/post.html?utm_source=account#faq",
            ),
        )
        for candidate, expected in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.safe_next(candidate), expected)

    def test_query_only_reference_is_allowed_off_transition_pages(self) -> None:
        self.assertEqual(
            self.safe_next("?tab=overview#module", path="/courses/"),
            "/courses/?tab=overview#module",
        )

    def test_absent_or_self_reference_on_transition_page_falls_back(self) -> None:
        request = self.factory.get("/accounts/continue/")
        self.assertEqual(
            safe_next_path(request),
            SAFE_ACCOUNT_DESTINATION,
        )
        self.assertEqual(
            self.safe_next("?tab=still-self"),
            SAFE_ACCOUNT_DESTINATION,
        )


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
        course = Course.objects.create(
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
        self.course = Course.objects.create(
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
        self.assertTrue(Session.objects.filter(session_key=unrelated_session_key).exists())
        compatibility = self.client.get(
            "/api/account/identity/",
            HTTP_AUTHORIZATION="Token synthetic-token",
        )
        self.assertEqual(compatibility.status_code, 200)
        self.assertEqual(compatibility.json()["account_id"], self.survivor.pk)

        replay = apply_reviewed_mapping(self.plan())
        self.assertTrue(replay["idempotent_replay"])
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
                "accounts.reconciliation._apply_one_mapping",
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
        from accounts.reconciliation import _profile_changes

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
                "accounts.reconciliation._profile_changes",
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

    def test_command_writes_a_safe_report_for_a_missing_survivor(self) -> None:
        artifact_directory = Path(".tmp/issue-100-tests")
        artifact_directory.mkdir(parents=True, exist_ok=True)
        mapping_path = artifact_directory / "missing-survivor-mapping.json"
        report_path = artifact_directory / "missing-survivor-report.json"
        self.addCleanup(mapping_path.unlink, missing_ok=True)
        self.addCleanup(report_path.unlink, missing_ok=True)
        document = self.plan(survivor_user_id=999999).canonical_document
        mapping_path.write_text(
            json.dumps(document),
            encoding="utf-8",
        )
        mapping_path.chmod(0o600)

        with self.assertRaises(CommandError) as raised:
            call_command(
                "reconcile_accounts",
                snapshot_id=SNAPSHOT_ID,
                mapping=str(mapping_path),
                output=str(report_path),
                stdout=StringIO(),
                **{"apply": True},
            )

        self.assertEqual(
            str(raised.exception),
            "account mapping was quarantined",
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "quarantined")
        self.assertEqual(
            report["conflicts"][0]["reason_codes"],
            ["account_missing"],
        )
        self.assertNotIn("IntegrityError", json.dumps(report))


class DurableAuthenticationTests(TestCase):
    def test_legacy_username_login_remains_compatible(self) -> None:
        user = CustomUser.objects.create_user(
            username="legacy-login",
            email="legacy@example.invalid",
            password="synthetic-password",
        )

        authenticated = authenticate(
            username="legacy-login",
            password="synthetic-password",
        )

        self.assertEqual(authenticated.pk, user.pk)

    def test_duplicate_normalized_email_fails_closed_even_when_one_password_matches(self) -> None:
        CustomUser.objects.create_user(
            username="first",
            email="Duplicate@Example.Invalid",
            password="matching-password",
        )
        CustomUser.objects.create_user(
            username="second",
            email="duplicate@example.invalid",
            password="different-password",
        )

        authenticated = authenticate(
            email="duplicate@example.invalid",
            password="matching-password",
        )

        self.assertIsNone(authenticated)

    def test_email_collision_cannot_fall_through_to_matching_username(self) -> None:
        CustomUser.objects.create_user(
            username="collision@example.invalid",
            email="first@example.invalid",
            password="known-pass",
        )
        CustomUser.objects.create_user(
            username="other",
            email="collision@example.invalid",
            password="other-pass",
        )

        self.assertIsNone(
            authenticate(
                username="collision@example.invalid",
                password="known-pass",
            )
        )

    def test_quarantined_account_cannot_authenticate(self) -> None:
        user = CustomUser.objects.create_user(
            username="quarantined",
            email="quarantined@example.invalid",
            password="synthetic-password",
        )
        user.identity_state = CustomUser.IdentityState.QUARANTINED
        user.save(update_fields=("identity_state",))

        self.assertIsNone(
            authenticate(
                username="quarantined",
                password="synthetic-password",
            )
        )


class SocialLinkingTests(TestCase):
    @staticmethod
    def anonymous_request(path: str):
        request = RequestFactory().get(path)
        request.user = AnonymousUser()
        return request

    def social_login(
        self,
        *,
        email: str,
        verified: bool,
        provider: str = "github",
        uid: str = "synthetic-social-uid",
        extra_data: dict | None = None,
    ):
        account = SimpleNamespace(
            provider=provider,
            uid=uid,
            extra_data=extra_data or {},
        )
        return SimpleNamespace(
            account=account,
            user=None,
            is_existing=False,
            email_addresses=[SimpleNamespace(email=email, verified=verified)],
            connect=Mock(),
        )

    def test_verified_social_claim_connects_existing_account_only(self) -> None:
        user = create_verified_user(
            username="returning",
            email="returning@example.invalid",
        )
        sociallogin = self.social_login(
            email="returning@example.invalid",
            verified=True,
            extra_data={"access_token": "not-logged"},
        )
        before_users = CustomUser.objects.count()

        ConsolidatingSocialAccountAdapter().pre_social_login(
            self.anonymous_request("/accounts/github/login/callback/"),
            sociallogin,
        )

        sociallogin.connect.assert_called_once()
        connected_user = sociallogin.connect.call_args.args[1]
        self.assertEqual(connected_user.pk, user.pk)
        self.assertEqual(CustomUser.objects.count(), before_users)
        self.assertFalse(AccountIdentityQuarantine.objects.exists())
        audit = AuditEvent.objects.get(action="accounts.identity.link_succeeded")
        rendered = json.dumps(audit.metadata, sort_keys=True)
        self.assertNotIn("returning@example.invalid", rendered)
        self.assertNotIn("not-logged", rendered)

    def test_stale_identity_state_fails_closed_before_social_linking(self) -> None:
        user = create_verified_user(
            username="stale-returning",
            email="stale-returning@example.invalid",
        )
        sociallogin = self.social_login(
            email="stale-returning@example.invalid",
            verified=True,
        )

        def change_identity_after_snapshot(*, email, user_id):
            del email
            CustomUser.objects.filter(pk=user_id).update(
                identity_state=CustomUser.IdentityState.QUARANTINED,
            )
            return False

        with (
            patch(
                "accounts.auth._has_unresolved_email_collision",
                side_effect=change_identity_after_snapshot,
            ),
            self.assertRaises(ImmediateHttpResponse) as raised,
        ):
            ConsolidatingSocialAccountAdapter().pre_social_login(
                self.anonymous_request("/accounts/github/login/callback/"),
                sociallogin,
            )

        self.assertEqual(raised.exception.response.status_code, 409)
        sociallogin.connect.assert_not_called()
        user.refresh_from_db()
        self.assertEqual(user.identity_state, CustomUser.IdentityState.LEGACY)
        quarantine = AccountIdentityQuarantine.objects.get()
        self.assertEqual(quarantine.reason_codes, ["normalized_email_conflict"])

    def test_each_supported_provider_connects_the_existing_durable_account(self) -> None:
        for provider in ("github", "google", "slack"):
            with self.subTest(provider=provider):
                email = f"{provider}@example.invalid"
                user = create_verified_user(
                    username=f"{provider}-returning",
                    email=email,
                )
                sociallogin = self.social_login(
                    email=email,
                    verified=True,
                    provider=provider,
                    uid=f"synthetic-{provider}-uid",
                )

                ConsolidatingSocialAccountAdapter().pre_social_login(
                    self.anonymous_request(f"/accounts/{provider}/login/callback/"),
                    sociallogin,
                )

                self.assertEqual(sociallogin.connect.call_args.args[1].pk, user.pk)

    def test_unverified_claim_is_denied_without_account_creation(self) -> None:
        sociallogin = self.social_login(
            email="unverified@example.invalid",
            verified=False,
            extra_data={"token": "must-not-leak"},
        )

        with self.assertRaises(ImmediateHttpResponse) as raised:
            ConsolidatingSocialAccountAdapter().pre_social_login(
                self.anonymous_request("/accounts/github/login/callback/"),
                sociallogin,
            )

        self.assertEqual(raised.exception.response.status_code, 409)
        self.assertEqual(CustomUser.objects.count(), 0)
        sociallogin.connect.assert_not_called()
        quarantine = AccountIdentityQuarantine.objects.get()
        rendered = json.dumps(
            {
                "reason_codes": quarantine.reason_codes,
                "source_user_ids": quarantine.source_user_ids,
                "fingerprint": quarantine.fingerprint,
            },
            sort_keys=True,
        )
        self.assertNotIn("unverified@example.invalid", rendered)
        self.assertNotIn("must-not-leak", rendered)

    def test_ambiguous_verified_case_variant_never_uses_last_login(self) -> None:
        first = create_verified_user(
            username="first",
            email="Case@Example.Invalid",
            verified_email="Case@Example.Invalid",
        )
        second = create_verified_user(
            username="second",
            email="case@example.invalid",
            verified_email="case@example.invalid",
        )
        sociallogin = self.social_login(
            email="case@example.invalid",
            verified=True,
        )

        with self.assertRaises(ImmediateHttpResponse):
            ConsolidatingSocialAccountAdapter().pre_social_login(
                self.anonymous_request("/accounts/github/login/callback/"),
                sociallogin,
            )

        sociallogin.connect.assert_not_called()
        self.assertEqual(
            AccountIdentityQuarantine.objects.get().source_user_ids,
            [first.pk, second.pk],
        )


class SharedAccountSurfaceTests(TestCase):
    def test_login_heading_matches_deployed_smoke_contract(self) -> None:
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.content.decode("utf-8"),
            r'<h1 class="text-2xl font-semibold app-heading">\s*'
            r'<i class="fas fa-sign-in-alt" aria-hidden="true"></i>\s*'
            r"Sign In\s*</h1>",
        )

    def test_signed_out_shell_uses_one_same_host_login_and_no_account_rows(self) -> None:
        identity_counts = {
            "users": CustomUser.objects.count(),
            "emails": EmailAddress.objects.count(),
            "social": SocialAccount.objects.count(),
            "sessions": Session.objects.count(),
            "aliases": AccountIdentityAlias.objects.count(),
        }

        response = self.client.get("/")

        self.assertContains(response, 'href="/accounts/login/?next=%2F"')
        self.assertNotContains(response, "courses.datatalks.club/accounts")
        self.assertEqual(
            identity_counts,
            {
                "users": CustomUser.objects.count(),
                "emails": EmailAddress.objects.count(),
                "social": SocialAccount.objects.count(),
                "sessions": Session.objects.count(),
                "aliases": AccountIdentityAlias.objects.count(),
            },
        )
        self.assertIsNotNone(review_projection())

    def test_signed_in_public_course_settings_and_api_share_account_id(self) -> None:
        user = create_verified_user(
            username="shared-account",
            email="shared@example.invalid",
        )
        self.client.force_login(user)

        for path in ("/", "/courses", "/accounts/settings/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-account-id="{user.pk}"')
                self.assertContains(response, "Account settings")
                self.assertContains(response, "Login connections")
                self.assertIn("private", response["Cache-Control"])
                self.assertIn("no-store", response["Cache-Control"])
        identity = self.client.get("/api/v1/account/identity/")
        self.assertEqual(identity.json()["account_id"], user.pk)
        self.assertEqual(identity.json()["auth_user_model"], "accounts.CustomUser")

    def test_identity_apis_deny_generically_without_cross_account_data(self) -> None:
        session_response = self.client.get("/api/v1/account/identity/")
        token_response = self.client.get("/api/account/identity/")

        self.assertEqual(session_response.status_code, 401)
        self.assertEqual(
            session_response.json(),
            {"error": "Authentication required"},
        )
        self.assertEqual(token_response.status_code, 401)
        self.assertEqual(token_response.json(), {"error": "Authentication token required"})
        rendered = json.dumps(
            [session_response.json(), token_response.json()],
            sort_keys=True,
        )
        self.assertNotIn("account_id", rendered)

    def test_studio_navigation_uses_capability_policy_not_is_staff_alone(self) -> None:
        staff = create_verified_user(
            username="synthetic-staff",
            email="staff@example.invalid",
            is_staff=True,
        )
        self.client.force_login(staff)
        without_capability = self.client.get("/")
        self.assertNotContains(without_capability, 'href="/studio/"')

        roles = {group.name: group for group in synchronize_studio_roles()}
        staff.groups.add(roles["site_admin"])
        with_capability = self.client.get("/")
        self.assertContains(with_capability, 'href="/studio/"')

    def test_explicit_reauthentication_has_safe_next_and_no_credential(self) -> None:
        first = self.client.get("/accounts/continue/?next=/courses/durable-identity/")
        replay = self.client.get("/accounts/continue/?next=/courses/durable-identity/")

        expected = "http://testserver/accounts/login/?next=%2Fcourses%2Fdurable-identity%2F"
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first["Location"], expected)
        self.assertEqual(replay["Location"], expected)
        self.assertEqual(first["Referrer-Policy"], "same-origin")
        for forbidden in ("token=", "code=", "credential=", "session="):
            self.assertNotIn(forbidden, first["Location"].casefold())

        for path in (
            "/accounts/continue/",
            "/accounts/continue/?next=/accounts/continue/",
            "/accounts/continue/?next=%2Faccounts%2Fcontinue%2F",
            "/accounts/continue/?next=%252Faccounts%252Fcontinue%252F",
            "/accounts/continue/?next=/accounts/login/",
            "/accounts/continue/?next=/accounts/logout/",
            "/accounts/continue/?next=/accounts/github/login/",
            "/accounts/continue/?next=/accounts/github/login/callback/",
            "/accounts/continue/?next=https://attacker.invalid/collect",
        ):
            with self.subTest(path=path):
                unsafe = self.client.get(path)
                self.assertEqual(
                    unsafe["Location"],
                    "http://testserver/accounts/login/?next=%2F",
                )

        for candidate in TRANSITION_BYPASS_NEXT_VALUES:
            with self.subTest(candidate=candidate):
                unsafe = self.client.get(
                    "/accounts/continue/",
                    {"next": candidate},
                )
                self.assertEqual(
                    unsafe["Location"],
                    "http://testserver/accounts/login/?next=%2F",
                )

        legitimate = self.client.get(
            "/accounts/continue/",
            {"next": ("/courses/guides/../durable-identity//?tab=overview#module-1")},
        )
        login_query = parse_qs(urlsplit(legitimate["Location"]).query)
        self.assertEqual(
            login_query["next"],
            ["/courses/durable-identity/?tab=overview#module-1"],
        )

    def test_authenticated_continuity_returns_directly_with_same_account(self) -> None:
        user = create_verified_user(
            username="continuity-account",
            email="continuity-account@example.invalid",
        )
        self.client.force_login(user)

        intended = self.client.get("/accounts/continue/?next=/courses/durable-identity/")
        fallback = self.client.get("/accounts/continue/?next=/accounts/login/")

        self.assertEqual(
            intended["Location"],
            "/courses/durable-identity/",
        )
        self.assertEqual(fallback["Location"], "/")
        for location in (intended["Location"], fallback["Location"]):
            self.assertNotIn("/accounts/login/", location)
            self.assertNotIn("/accounts/continue/", location)

        for candidate in TRANSITION_BYPASS_NEXT_VALUES:
            with self.subTest(candidate=candidate):
                bypass = self.client.get(
                    "/accounts/continue/",
                    {"next": candidate},
                )
                self.assertEqual(bypass["Location"], "/")

        legitimate = self.client.get(
            "/accounts/continue/",
            {"next": ("/courses/guides/../durable-identity//?tab=overview#module-1")},
        )
        self.assertEqual(
            legitimate["Location"],
            "/courses/durable-identity/?tab=overview#module-1",
        )
        identity = self.client.get("/api/v1/account/identity/")
        self.assertEqual(identity.json()["account_id"], user.pk)

    def test_content_only_review_tables_classify_new_identity_rows_as_sensitive(self) -> None:
        for table in (
            "accounts_accountidentityalias",
            "accounts_accountidentityquarantine",
            "accounts_accountreconciliationrun",
        ):
            with self.subTest(table=table):
                self.assertTrue(is_sensitive_table(table))

    def test_inventory_covers_fields_relations_routes_and_session_boundary(self) -> None:
        inventory = account_inventory()

        self.assertEqual(inventory["auth_user_model"], "accounts.CustomUser")
        self.assertEqual(inventory["user_table"], "accounts_customuser")
        self.assertEqual(len(inventory["dependent_relations"]), 21)
        self.assertEqual(len(inventory["many_to_many_relations"]), 3)
        relation_keys = {
            f"{item['model_label']}.{item['field_name']}"
            for item in inventory["dependent_relations"]
        }
        self.assertIn("courses.Enrollment.student", relation_keys)
        self.assertIn("core.AuditEvent.actor", relation_keys)
        self.assertIn("management_auth.APIPrincipal.user", relation_keys)
        self.assertIn("socialaccount.SocialAccount.user", relation_keys)
        self.assertIsNone(inventory["session"]["cookie_domain"])
        self.assertEqual(
            inventory["session"]["cross_host_policy"],
            "explicit_reauthentication",
        )
        self.assertFalse(inventory["session"]["save_every_request"])
        authentication_paths = {item["path"] for item in inventory["authentication_routes"]}
        self.assertIn("/accounts/github/login/callback/", authentication_paths)
        self.assertIn("/accounts/google/login/callback/", authentication_paths)
        self.assertIn("/accounts/slack/login/callback/", authentication_paths)
        self.assertFalse(inventory["content_projection_account_creation"])
        self.assertEqual(len(inventory["inventory_checksum"]), 64)


class SessionLifecycleTests(TestCase):
    def test_ordinary_identity_release_preserves_the_existing_session(self) -> None:
        user = create_verified_user(
            username="session-continuity",
            email="session-continuity@example.invalid",
        )
        self.client.force_login(user)
        original_session_key = self.client.session.session_key
        user.preferred_timezone = "Europe/Berlin"
        user.save(update_fields=("preferred_timezone",))

        response = self.client.get("/api/v1/account/identity/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], user.pk)
        self.assertEqual(self.client.session.session_key, original_session_key)

    def test_login_cycles_anonymous_session_key_and_logout_is_scoped(self) -> None:
        user = CustomUser.objects.create_user(
            username="fixation-check",
            email="fixation-check@example.invalid",
            password="synthetic-password",
        )
        anonymous_session = self.client.session
        anonymous_session["synthetic_pre_login_value"] = True
        anonymous_session.save()
        anonymous_key = anonymous_session.session_key
        other_client = Client()
        other_client.force_login(user)
        other_session_key = other_client.session.session_key

        self.assertTrue(
            self.client.login(
                username="fixation-check",
                password="synthetic-password",
            )
        )
        authenticated_key = self.client.session.session_key
        self.assertNotEqual(authenticated_key, anonymous_key)

        self.client.post("/accounts/logout/")

        self.assertFalse(Session.objects.filter(session_key=authenticated_key).exists())
        self.assertTrue(Session.objects.filter(session_key=other_session_key).exists())

    def test_password_change_disablement_and_expiry_fail_closed(self) -> None:
        password_user = CustomUser.objects.create_user(
            username="password-session",
            email="password-session@example.invalid",
            password="before-change",
        )
        password_client = Client()
        password_client.force_login(password_user)
        password_user.set_password("after-change")
        password_user.save(update_fields=("password",))
        self.assertEqual(
            password_client.get("/api/v1/account/identity/").status_code,
            401,
        )

        disabled_user = create_verified_user(
            username="disabled-session",
            email="disabled-session@example.invalid",
            is_staff=True,
        )
        disabled_client = Client()
        disabled_client.force_login(disabled_user)
        disabled_user.is_active = False
        disabled_user.save(update_fields=("is_active",))
        self.assertEqual(
            disabled_client.get("/api/v1/account/identity/").status_code,
            401,
        )

        expiring_user = create_verified_user(
            username="expired-session",
            email="expired-session@example.invalid",
        )
        expiring_client = Client()
        expiring_client.force_login(expiring_user)
        expiring_session = expiring_client.session
        expiring_session.set_expiry(-1)
        expiring_session.save()
        self.assertEqual(
            expiring_client.get("/api/v1/account/identity/").status_code,
            401,
        )


class ManagementIdentityParityTests(TestCase):
    def test_human_management_principal_uses_the_same_durable_account(self) -> None:
        user = create_verified_user(
            username="management-human",
            email="management-human@example.invalid",
            is_staff=True,
        )
        principal = APIPrincipal.objects.create(
            kind=APIPrincipal.Kind.HUMAN,
            name="Synthetic management human",
            identity_snapshot="synthetic-human-identity",
            user=user,
        )
        generated = generate_token()
        APICredential.objects.create(
            principal=principal,
            name="Synthetic management credential",
            prefix=generated.prefix,
            secret_digest=encode_secret(generated.secret),
            digest_algorithm=DIGEST_ALGORITHM,
            digest_version=DIGEST_VERSION,
            scopes=["studio.home.read"],
            expires_at=timezone.now() + timedelta(hours=1),
        )
        request = RequestFactory().get(
            "/api/v1/admin/health",
            HTTP_AUTHORIZATION=f"Bearer {generated.raw}",
        )

        identity = authenticate_management(request)

        self.assertEqual(identity.principal.user_id, user.pk)
        self.client.force_login(user)
        session_identity = self.client.get("/api/v1/account/identity/").json()
        self.assertEqual(session_identity["account_id"], user.pk)


class IdentityTemplateReadabilityTests(SimpleTestCase):
    structural_tags = (
        r"(?:article|aside|div|footer|form|h[1-6]|header|li|main|nav|ol|p|"
        r"section|table|tbody|td|th|thead|tr|ul)"
    )
    compressed_patterns = (
        re.compile(rf"</{structural_tags}>\s*<{structural_tags}\b"),
        re.compile(r"{%\s*(?:for|if|elif|else|empty|endif|endfor)\b[^%]*%}\s*<"),
        re.compile(
            rf"</{structural_tags}>\s*"
            r"{%\s*(?:endfor|endif|else|elif|empty)\b"
        ),
    )

    def test_identity_templates_are_line_broken_not_minified(self) -> None:
        root = Path(settings.BASE_DIR)
        template_paths = (
            root / "course_platform_templates/base.html",
            root / "templates/base.html",
            root / "accounts/templates/accounts/login.html",
            root / "course_platform_templates/socialaccount/identity_conflict.html",
            root / "course_platform_templates/accounts/account_settings.html",
        )
        failures = []
        for path in template_paths:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if any(pattern.search(line) for pattern in self.compressed_patterns):
                    failures.append(f"{path.relative_to(root)}:{line_number}")
        self.assertEqual(
            failures,
            [],
            "Keep structural HTML and Django controls on separate source lines",
        )
