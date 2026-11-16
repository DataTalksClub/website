"""Monitoring proves approvals conservatively and never takes provider actions."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from slack_access.models import Application, JoinObservation, Membership, MonitorState
from slack_access.monitoring import poll_joins
from slack_access.services import active_member
from slack_access.slack_api import SlackMember, SlackReadClient, SlackReadError


def response(body, status=200, headers=None):
    return SimpleNamespace(status_code=status, json=lambda: body, headers=headers or {})


class SlackReadClientTests(SimpleTestCase):
    @patch("slack_access.slack_api.requests.post")
    def test_pagination_filters_nonmembers_and_uses_documented_guest_provenance(self, post):
        post.side_effect = [
            response({"ok": True, "team_id": "TTEST01"}),
            response(
                {
                    "ok": True,
                    "members": [
                        {
                            "id": "UTEST01",
                            "profile": {"email": "dev+one@dtcdev.click"},
                            "updated": 123,
                            "inviter": "UCHANNEL",
                        },
                        {"id": "UBOT001", "is_bot": True},
                        {"id": "UDEAD01", "deleted": True},
                        {"id": "UPEND01", "is_invited_user": True},
                        {"id": "UEXT001", "team_id": "TOTHER1"},
                    ],
                    "response_metadata": {"next_cursor": "next-page"},
                }
            ),
            response(
                {
                    "ok": True,
                    "members": [
                        {"id": "UGUEST1", "profile": {"guest_invited_by": "USPON01"}},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            ),
        ]
        members = SlackReadClient("synthetic-token", "TTEST01").members()
        self.assertEqual([row.slack_user_id for row in members], ["UTEST01", "UGUEST1"])
        self.assertIsNone(members[0].joined_at)
        self.assertEqual(members[0].inviter_slack_id, "")
        self.assertEqual(members[1].inviter_slack_id, "USPON01")
        self.assertEqual(post.call_args_list[-1].kwargs["data"]["cursor"], "next-page")
        self.assertTrue(
            all(call.kwargs["allow_redirects"] is False for call in post.call_args_list)
        )

    @patch("slack_access.slack_api.requests.post")
    def test_unexpected_workspace_is_refused_before_roster_read(self, post):
        post.return_value = response({"ok": True, "team_id": "TOTHER1"})
        with self.assertRaisesMessage(SlackReadError, "unexpected_workspace"):
            SlackReadClient("synthetic-token", "TTEST01").members()
        self.assertEqual(post.call_count, 1)

    @patch("slack_access.slack_api.requests.post")
    def test_cursor_loop_fails_instead_of_returning_partial_snapshot(self, post):
        post.side_effect = [
            response({"ok": True, "team_id": "TTEST01"}),
            response({"ok": True, "members": [], "response_metadata": {"next_cursor": "same"}}),
            response({"ok": True, "members": [], "response_metadata": {"next_cursor": "same"}}),
        ]
        with self.assertRaisesMessage(SlackReadError, "pagination_loop"):
            SlackReadClient("synthetic-token", "TTEST01").members()

    @patch("slack_access.slack_api.requests.post")
    def test_arbitrary_provider_error_and_transport_details_are_not_exposed(self, post):
        post.return_value = response({"ok": False, "error": "synthetic-secret-canary"})
        with self.assertRaisesMessage(SlackReadError, "provider_rejected"):
            SlackReadClient("synthetic-token", "TTEST01").members()
        post.side_effect = RuntimeError("synthetic-secret-canary")
        with self.assertRaisesMessage(SlackReadError, "transport_failed"):
            SlackReadClient("synthetic-token", "TTEST01").members()

    @patch("slack_access.slack_api.time.sleep")
    @patch("slack_access.slack_api.requests.post")
    def test_rate_limit_retry_is_bounded_and_does_not_follow_redirects(self, post, sleep):
        post.side_effect = [
            response({}, status=429, headers={"Retry-After": "1"}),
            response({"ok": True, "team_id": "TTEST01"}),
            response({"ok": True, "members": [], "response_metadata": {}}),
        ]
        self.assertEqual(SlackReadClient("synthetic-token", "TTEST01").members(), [])
        sleep.assert_called_once_with(1)
        post.side_effect = None
        post.return_value = response({}, status=429, headers={"Retry-After": "3600"})
        with self.assertRaisesMessage(SlackReadError, "ratelimited"):
            SlackReadClient("synthetic-token", "TTEST01").members()


class MonitoringTests(TestCase):
    def user(self, suffix, email=None):
        email = email or f"dev+{suffix}@dtcdev.click"
        user = get_user_model().objects.create_user(username=f"monitor-{suffix}", email=email)
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        return user

    def poll(self, *members):
        return poll_joins(SimpleNamespace(members=lambda: list(members)))

    def test_bulk_baseline_binds_unique_verified_identities_without_per_member_queries(self):
        user = self.user("baseline")
        members = [SlackMember(f"UBASE{i:05}") for i in range(1200)]
        members[0] = SlackMember(members[0].slack_user_id, user.email.upper())
        with CaptureQueriesContext(connection) as queries:
            counts = self.poll(*members)
        self.assertEqual(counts["historical"], 1200)
        self.assertLess(len(queries), 50)
        self.assertEqual(Membership.objects.get(slack_user_id=members[0].slack_user_id).user, user)
        self.assertEqual(JoinObservation.objects.filter(classification="historical").count(), 1200)
        with patch("slack_access.monitoring.observe_join") as observe:
            self.assertEqual(sum(self.poll(*members).values()), 0)
        observe.assert_not_called()

    def test_ambiguous_case_insensitive_email_does_not_bind_membership(self):
        user = self.user("ambiguous")
        self.user("other", email=user.email.upper())
        self.poll(SlackMember("UAMBIG1", user.email))
        self.assertIsNone(Membership.objects.get(slack_user_id="UAMBIG1").user_id)

    def test_two_slack_accounts_matching_verified_aliases_do_not_pick_an_arbitrary_account(self):
        user = self.user("aliases")
        alias = "dev+aliases-second@dtcdev.click"
        EmailAddress.objects.create(user=user, email=alias, verified=True)
        members = [SlackMember("UALIAS1", user.email), SlackMember("UALIAS2", alias)]
        self.poll(*members)
        self.poll(*members)
        self.assertFalse(Membership.objects.filter(user=user).exists())

    def test_two_new_members_matching_verified_aliases_remain_unknown_and_unbound(self):
        self.poll(SlackMember("UBASE01"))
        user = self.user("newaliases")
        alias = "dev+newaliases-second@dtcdev.click"
        EmailAddress.objects.create(user=user, email=alias, verified=True)
        members = [SlackMember("UNEWAL1", user.email), SlackMember("UNEWAL2", alias)]
        counts = self.poll(*members)
        self.assertEqual(counts["unknown"], 2)
        self.assertFalse(Membership.objects.filter(user=user).exists())
        self.assertEqual(JoinObservation.objects.filter(classification="unknown").count(), 2)
        self.poll(*members)
        self.assertFalse(Membership.objects.filter(user=user).exists())

    def test_failed_snapshot_never_establishes_baseline_or_advances_watermark(self):
        client = Mock()
        client.members.side_effect = SlackReadError("incomplete_pagination")
        with self.assertRaisesMessage(SlackReadError, "incomplete_pagination"):
            poll_joins(client)
        self.assertFalse(MonitorState.objects.filter(key="slack-joins").exists())
        self.poll(SlackMember("UBASE01"))
        before = MonitorState.objects.get(key="slack-joins").last_success_at
        with self.assertRaises(SlackReadError):
            poll_joins(client)
        self.assertEqual(MonitorState.objects.get(key="slack-joins").last_success_at, before)
        self.assertEqual(JoinObservation.objects.count(), 1)

    def test_late_account_binding_preserves_historical_classification(self):
        self.poll(SlackMember("ULATER1", "dev+later@dtcdev.click"))
        user = self.user("later")
        self.assertEqual(sum(self.poll(SlackMember("ULATER1", user.email)).values()), 0)
        self.assertEqual(Membership.objects.get(slack_user_id="ULATER1").user, user)
        self.assertEqual(
            JoinObservation.objects.get(slack_user_id="ULATER1").classification, "historical"
        )

    def test_new_outside_join_is_reported_without_automatic_moderation(self):
        self.poll(SlackMember("UBASE01"))
        counts = self.poll(
            SlackMember("UBASE01"),
            SlackMember("UOUT001", "dev+outside@dtcdev.click", inviter_slack_id="USPON01"),
        )
        self.assertEqual(counts["outside_flow"], 1)
        observation = JoinObservation.objects.get(slack_user_id="UOUT001")
        self.assertEqual(observation.inviter_slack_id, "USPON01")
        self.assertFalse(Membership.objects.filter(banned_at__isnull=False).exists())
        self.assertEqual(sum(self.poll(SlackMember("UOUT001")).values()), 0)

    def test_member_absence_denies_sponsorship_without_provider_ban(self):
        user = self.user("present")
        self.poll(SlackMember("UPRES01", user.email))
        self.assertEqual(active_member(user).slack_user_id, "UPRES01")
        failing = Mock()
        failing.members.side_effect = SlackReadError("transport_failed")
        with self.assertRaises(SlackReadError):
            poll_joins(failing)
        self.assertTrue(Membership.objects.get(user=user).present)
        self.poll(SlackMember("UOTHER1"))
        member = Membership.objects.get(user=user)
        self.assertFalse(member.present)
        self.assertIsNone(member.banned_at)
        with self.assertRaisesMessage(PermissionDenied, "Verified Slack membership"):
            active_member(user)
        self.poll(SlackMember("UPRES01", user.email))
        self.assertEqual(active_member(user).pk, member.pk)

    def test_approved_account_without_reliable_join_time_is_unknown_and_redirect_revoked(self):
        user = self.user("approved")
        self.poll(SlackMember("UBASE01"))
        application = Application.objects.create(
            user=user,
            who="Synthetic",
            work="Synthetic",
            what="Synthetic testing",
            state=Application.State.APPROVED,
            approved_at=timezone.now(),
            redirect_digest="a" * 64,
        )
        counts = self.poll(SlackMember("UAPPR01", user.email))
        self.assertEqual(counts["unknown"], 1)
        application.refresh_from_db()
        self.assertEqual(application.redirect_digest, "")
        self.assertEqual(application.state, Application.State.JOINED)

    def test_timestamped_join_proves_approval_preceded_join(self):
        user = self.user("timestamp")
        self.poll(SlackMember("UBASE01"))
        approved_at = timezone.now() - timedelta(minutes=2)
        Application.objects.create(
            user=user,
            who="Synthetic",
            work="Synthetic",
            what="Synthetic testing",
            state=Application.State.APPROVED,
            approved_at=approved_at,
        )
        counts = self.poll(SlackMember("UTIMED1", user.email, joined_at=timezone.now()))
        self.assertEqual(counts["website_approved"], 1)
