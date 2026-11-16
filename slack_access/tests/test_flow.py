import logging
from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from community_base.curriculum.models import Cohort, Course, Enrollment
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.gunicorn_logging import redact_token_path
from core.safe_logging import SafeJsonFormatter
from slack_access import services
from slack_access.forms import VOUCH_WARNING
from slack_access.models import Application, Decision, JoinObservation, Membership
from slack_access.monitoring import poll_joins
from slack_access.slack_api import SlackMember, SlackReadError


@override_settings(SLACK_INVITE_URL="https://join.slack.com/t/synthetic/shared_invite/test")
class FlowTests(TestCase):
    def person(self, name, *, verified=True, staff=False):
        user = get_user_model().objects.create_user(
            username=name,
            email=f"dev+{name}@dtcdev.click",
            is_staff=staff,
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=verified, primary=True)
        return user

    def setUp(self):
        self.applicant = self.person("applicant")
        self.sponsor = self.person("sponsor")
        self.member = Membership.objects.create(user=self.sponsor, slack_user_id="U000001")
        self.staff = self.person("reviewer", staff=True)
        self.staff.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="slack_access",
                codename__in=["review_applications", "ban_members"],
            )
        )
        self.answers = {
            "who": "Synthetic applicant",
            "work": "Synthetic workplace",
            "what": "Study data",
        }

    def apply(self, user=None, sponsor=True):
        return services.apply(
            user or self.applicant, sponsor=self.member if sponsor else None, **self.answers
        )

    def approved(self):
        application = self.apply()
        return services.review(self.staff, application.pk, approve=True)

    def test_application_requires_verified_identity_and_private_answers(self):
        with self.assertRaises(PermissionDenied):
            self.apply(self.person("unverified", verified=False))
        self.client.force_login(self.applicant)
        response = self.client.post(reverse("slack-access:application"), self.answers)
        self.assertEqual(response.status_code, 302)
        application = Application.objects.get(user=self.applicant)
        response = self.client.get(reverse("slack-access:vouch", args=[application.vouch_key]))
        self.assertContains(response, VOUCH_WARNING)
        self.assertNotContains(response, self.answers["work"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertTrue(self.applicant.newsletter_subscribed)

    def test_course_participant_auto_approval_and_ban_override(self):
        course = Course.objects.create(slug="synthetic-course", title="Synthetic course")
        cohort = Cohort.objects.create(
            course=course, slug="synthetic-cohort", title="Synthetic cohort"
        )
        Enrollment.objects.create(user=self.applicant, cohort=cohort)
        application = self.apply(sponsor=False)
        self.assertEqual(application.state, "approved")
        self.assertEqual(application.approval_source, "course")
        Membership.objects.create(
            user=self.applicant, slack_user_id="U000002", banned_at=timezone.now()
        )
        with self.assertRaises(PermissionDenied):
            services.issue_link(self.applicant)

    def test_unenrolled_course_does_not_auto_approve(self):
        course = Course.objects.create(slug="synthetic-course", title="Synthetic course")
        cohort = Cohort.objects.create(
            course=course, slug="synthetic-cohort", title="Synthetic cohort"
        )
        Enrollment.objects.create(user=self.applicant, cohort=cohort, unenrolled_at=timezone.now())
        self.assertEqual(self.apply(sponsor=False).state, "pending")

    def test_staff_permission_and_vouch_required(self):
        application = self.apply(sponsor=False)
        with self.assertRaises(PermissionDenied):
            services.review(self.applicant, application.pk, approve=True)
        with self.assertRaises(PermissionDenied):
            services.review(self.staff, application.pk, approve=True)
        services.vouch(self.sponsor, application.vouch_key)
        self.assertEqual(
            services.review(self.staff, application.pk, approve=True).state, "approved"
        )
        self.assertTrue(Decision.objects.filter(application=application, action="vouch").exists())

    def test_vouch_requires_acknowledgement_and_blocks_self_and_reparenting(self):
        application = self.apply(sponsor=False)
        url = reverse("slack-access:vouch", args=[application.vouch_key])
        self.client.force_login(self.sponsor)
        self.assertContains(self.client.get(url), VOUCH_WARNING)
        self.client.post(url, {})
        application.refresh_from_db()
        self.assertIsNone(application.sponsor_id)
        self.assertContains(self.client.post(url, {"acknowledge": "on"}), "Your vouch is recorded")
        other = self.person("other")
        Membership.objects.create(user=other, slack_user_id="U000003")
        with self.assertRaises(PermissionDenied):
            services.vouch(other, application.vouch_key)
        Membership.objects.create(user=self.applicant, slack_user_id="U000002")
        with self.assertRaises(PermissionDenied):
            services.vouch(self.applicant, application.vouch_key)

    def test_vouch_csrf_and_anonymous_cannot_confirm(self):
        application = self.apply(sponsor=False)
        url = reverse("slack-access:vouch", args=[application.vouch_key])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.sponsor)
        self.assertEqual(client.post(url, {"acknowledge": "on"}).status_code, 403)
        self.assertEqual(self.client.post(url, {"acknowledge": "on"}).status_code, 403)

    def test_single_use_post_get_preview_and_wrong_user(self):
        self.approved()
        token = services.issue_link(self.applicant)
        url = reverse("slack-access:redeem", args=[token])
        self.client.force_login(self.applicant)
        for method in (self.client.get, self.client.head):
            response = method(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
        self.client.force_login(self.sponsor)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.applicant)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Location"], services.destination())
        self.assertEqual(self.client.post(url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.issue_link(self.applicant)
        self.assertNotIn(token, redact_token_path(url))

    def test_invite_destination_failure_does_not_consume(self):
        self.approved()
        token = services.issue_link(self.applicant)
        with override_settings(SLACK_INVITE_URL="https://evil.invalid/path"):
            with self.assertRaises(ValueError):
                services.redeem(self.applicant, token)
        self.assertIsNone(Application.objects.get(user=self.applicant).redirect_consumed_at)
        self.assertEqual(services.redeem(self.applicant, token), services.destination())

    def test_expiry_and_rotation(self):
        application = self.approved()
        old = services.issue_link(self.applicant)
        token = services.issue_link(self.applicant)
        with self.assertRaises(PermissionDenied):
            services.redeem(self.applicant, old)
        Application.objects.filter(pk=application.pk).update(
            redirect_issued_at=timezone.now() - timedelta(days=8)
        )
        with self.assertRaises(PermissionDenied):
            services.redeem(self.applicant, token)

    def test_join_invalidates_link_and_preserves_website_sponsor(self):
        application = self.approved()
        token = services.issue_link(self.applicant)
        observation = services.observe_join(
            "U000002", self.applicant.email, joined_at=timezone.now()
        )
        self.assertEqual(observation.classification, "website_approved")
        self.assertEqual(Membership.objects.get(user=self.applicant).sponsor, self.member)
        application.refresh_from_db()
        self.assertEqual(application.state, "joined")
        with self.assertRaises(PermissionDenied):
            services.redeem(self.applicant, token)

    def test_cascade_ban_includes_voucher_and_siblings_and_pending(self):
        self.approved()
        token = services.issue_link(self.applicant)
        services.observe_join("U000002", self.applicant.email, joined_at=timezone.now())
        child = Membership.objects.get(user=self.applicant)
        sibling = self.person("sibling")
        sibling_member = Membership.objects.create(
            user=sibling, slack_user_id="U000003", sponsor=self.member
        )
        grandchild = Membership.objects.create(slack_user_id="U000004", sponsor=child)
        pending = self.apply(self.person("pending"))
        self.assertEqual(services.ban(self.staff, child.pk), 4)
        for member in (self.member, child, sibling_member, grandchild):
            member.refresh_from_db()
            self.assertIsNotNone(member.banned_at)
        pending.refresh_from_db()
        self.assertEqual(pending.state, "banned")
        with self.assertRaises(PermissionDenied):
            services.active_member(self.sponsor)
        with self.assertRaises(PermissionDenied):
            services.redeem(self.applicant, token)
        with self.assertRaises(PermissionDenied):
            self.apply(self.person("later"))
        self.assertEqual(services.ban(self.staff, child.pk), 4)

    def test_monitor_classification_and_no_moderation_side_effects(self):
        self.approved()
        before = self.member.banned_at
        with patch("slack_access.monitoring.SlackReadClient") as cls:
            cls.return_value.members.return_value = [SlackMember("U000001", self.sponsor.email)]
            self.assertEqual(poll_joins()["historical"], 1)
            cls.return_value.members.return_value += [
                SlackMember("U000002", self.applicant.email),
                SlackMember("U000003", "dev+outside@dtcdev.click"),
            ]
            counts = poll_joins()
            self.assertEqual(counts["unknown"], 1)
            self.assertEqual(counts["outside_flow"], 1)
            self.assertEqual(sum(poll_joins().values()), 0)
        self.member.refresh_from_db()
        self.assertEqual(self.member.banned_at, before)
        self.assertEqual(JoinObservation.objects.count(), 3)

    def test_monitor_does_not_claim_approval_after_actual_join(self):
        application = self.approved()
        observation = services.observe_join(
            "U000002",
            self.applicant.email,
            joined_at=application.approved_at - timedelta(seconds=5),
        )
        self.assertEqual(observation.classification, "outside_flow")

    def test_monitor_failure_does_not_advance_baseline(self):
        with patch("slack_access.monitoring.SlackReadClient") as cls:
            cls.return_value.members.side_effect = SlackReadError("ratelimited")
            with self.assertRaises(SlackReadError):
                poll_joins()
        self.assertFalse(JoinObservation.objects.exists())

    def test_member_invite_warning_and_banned_sponsor(self):
        self.client.force_login(self.sponsor)
        url = reverse("slack-access:invite")
        self.assertContains(self.client.get(url), VOUCH_WARNING)
        response = self.client.post(url, {"acknowledge": "on"})
        token = response.context["invite_url"].split("invite=")[1]
        self.client.force_login(self.applicant)
        response = self.client.post(
            reverse("slack-access:application") + "?invite=" + token, self.answers
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Application.objects.get(user=self.applicant).sponsor, self.member)
        services.ban(self.staff, self.member.pk)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_rejection_prevents_link_and_repeat_approval(self):
        application = self.apply()
        services.review(self.staff, application.pk, approve=False)
        with self.assertRaises(PermissionDenied):
            services.issue_link(self.applicant)
        with self.assertRaises(PermissionDenied):
            services.review(self.staff, application.pk, approve=True)

    def test_token_paths_are_redacted_from_django_error_logs(self):
        token = "synthetic-redaction-canary"
        record = logging.LogRecord(
            "django.request",
            logging.ERROR,
            __file__,
            1,
            "Internal Server Error: %s",
            (f"/community/slack/redeem/{token}/",),
            None,
        )
        record.route = {"path": f"/community/slack/vouch/{token}/"}
        output = SafeJsonFormatter().format(record)
        self.assertNotIn(token, output)
        self.assertIn("[token]", output)

    def test_reviewer_page_requires_staff_session_and_explicit_permission(self):
        self.apply()
        url = reverse("studio:slack-review")
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.staff.user_permissions.add(
            Permission.objects.get(content_type__app_label="core", codename="access_studio")
        )
        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.answers["work"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post(url).status_code, 405)
        invalid = self.client.post(
            reverse("studio:slack-review-action"),
            {
                "application_id": "oops",
                "action": "approve",
                "expected_state": "pending",
                "idempotency_key": "synthetic-invalid-target",
            },
        )
        self.assertEqual(invalid.status_code, 400)

    def test_unknown_join_without_email_is_unresolved(self):
        observation = services.observe_join("U000004")
        self.assertEqual(observation.classification, "unknown")

    def test_later_course_enrollment_reconciles_pending_application(self):
        application = self.apply(sponsor=False)
        course = Course.objects.create(slug="synthetic-course", title="Synthetic course")
        cohort = Cohort.objects.create(
            course=course, slug="synthetic-cohort", title="Synthetic cohort"
        )
        Enrollment.objects.create(user=self.applicant, cohort=cohort)
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse("slack-access:application")).status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.state, "approved")
        self.assertEqual(application.approval_source, "course")

    def test_later_course_enrollment_never_overrides_rejection(self):
        application = self.apply(sponsor=False)
        services.review(self.staff, application.pk, approve=False)
        course = Course.objects.create(slug="synthetic-course", title="Synthetic course")
        cohort = Cohort.objects.create(
            course=course, slug="synthetic-cohort", title="Synthetic cohort"
        )
        Enrollment.objects.create(user=self.applicant, cohort=cohort)
        self.assertEqual(services.refresh_course_approval(self.applicant).state, "rejected")

    def test_join_conflicting_member_identity_does_not_revoke_other_application(self):
        self.approved()
        token = services.issue_link(self.applicant)
        Membership.objects.create(user=self.applicant, slack_user_id="U000005")
        observation = services.observe_join("U000006", self.applicant.email)
        self.assertEqual(observation.classification, "unknown")
        self.assertIsNone(observation.application_id)
        self.assertIsNone(Membership.objects.get(slack_user_id="U000006").user_id)
        self.assertEqual(services.redeem(self.applicant, token), services.destination())

    def test_ban_follows_existing_member_application_edge_before_poll(self):
        existing = self.person("existing-member")
        existing_member = Membership.objects.create(user=existing, slack_user_id="U000007")
        self.apply(existing)
        pending_user = self.person("existing-child")
        pending = services.apply(pending_user, sponsor=existing_member, **self.answers)
        self.assertEqual(services.ban(self.staff, self.member.pk), 2)
        existing_member.refresh_from_db()
        pending.refresh_from_db()
        self.assertIsNotNone(existing_member.banned_at)
        self.assertEqual(pending.state, "banned")
        with self.assertRaises(PermissionDenied):
            services.active_member(existing)

    def test_safe_application_reads_never_reconcile_course_state(self):
        application = self.apply(sponsor=False)
        course = Course.objects.create(slug="safe-course", title="Synthetic course")
        cohort = Cohort.objects.create(course=course, slug="safe-cohort", title="Synthetic cohort")
        with patch("slack_access.signals.refresh_course_approval"):
            Enrollment.objects.create(user=self.applicant, cohort=cohort)
        before = Decision.objects.count()
        self.client.force_login(self.applicant)
        url = reverse("slack-access:application")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.head(url).status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.state, "pending")
        self.assertEqual(Decision.objects.count(), before)
        self.assertEqual(self.client.post(url, {"action": "recheck_course"}).status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.state, "approved")

    @patch("slack_access.rate_limits.LIMIT", 2)
    def test_business_rate_limit_and_harmless_vouch_replay(self):
        application = self.apply(sponsor=False)
        self.client.force_login(self.sponsor)
        url = reverse("slack-access:vouch", args=[application.vouch_key])
        self.assertEqual(self.client.post(url, {"acknowledge": "on"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"acknowledge": "on"}).status_code, 200)
        self.assertEqual(
            Decision.objects.filter(application=application, action="vouch").count(), 1
        )
        rejected = self.client.post(url, {"acknowledge": "on"})
        self.assertEqual(rejected.status_code, 429)
        self.assertGreater(int(rejected["Retry-After"]), 0)
        self.assertIn("no-store", rejected["Cache-Control"])

    def test_rate_window_recovers_without_retaining_action_history(self):
        from slack_access.models import ActionBucket
        from slack_access.rate_limits import AdmissionDenied, admit

        now = timezone.now()
        with patch("slack_access.rate_limits.LIMIT", 1):
            admit(self.applicant.pk, now=now)
            with self.assertRaises(AdmissionDenied):
                admit(self.applicant.pk, now=now)
            admit(self.applicant.pk, now=now + timedelta(seconds=61))
        self.assertEqual(ActionBucket.objects.get().hits, 1)
