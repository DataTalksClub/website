"""Real bearer and staff HTML requests share approval and moderation contracts."""

import json
import uuid
from datetime import timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from accounts.studio_test_support import authenticated_studio_client
from core.models import AuditEvent, IdempotencyRecord
from management_api.openapi import generate_document
from management_api.parity import parity_errors
from management_auth.models import APICredential, APIPrincipal
from management_auth.tokens import encode_secret, generate_token
from management_registry import CAPABILITY_REGISTRY
from slack_access import management_services
from slack_access.models import Application, Decision, JoinObservation, Membership


class ManagementParityTests(TestCase):
    def person(self, suffix, *, staff=False):
        user = get_user_model().objects.create_user(
            username=f"parity-{suffix}",
            email=f"dev+parity-{suffix}@dtcdev.click",
            is_staff=staff,
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        return user

    def application(self, suffix):
        return Application.objects.create(
            user=self.person(suffix),
            who="Synthetic questionnaire canary",
            work="Synthetic workplace",
            what="Synthetic role",
            sponsor=self.sponsor,
        )

    def setUp(self):
        self.staff = self.person("staff", staff=True)
        self.permissions = list(
            Permission.objects.filter(
                content_type__app_label="slack_access",
                codename__in=["review_applications", "ban_members"],
            )
        )
        self.staff.user_permissions.add(*self.permissions)
        self.studio = authenticated_studio_client(self.staff)
        self.principal = APIPrincipal.objects.create(
            kind=APIPrincipal.Kind.SERVICE,
            name="Synthetic Slack reviewer",
            identity_snapshot="service:synthetic-slack-reviewer",
        )
        self.principal.permissions.add(*self.permissions)
        token = generate_token()
        self.token = token.raw
        self.credential = APICredential.objects.create(
            principal=self.principal,
            name="Synthetic Slack API token",
            prefix=token.prefix,
            secret_digest=encode_secret(token.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=[
                "slack.applications.list",
                "slack.applications.review",
                "slack.members.preview",
                "slack.members.ban",
                "slack.joins.report",
            ],
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.sponsor = Membership.objects.create(
            user=self.person("sponsor"), slack_user_id="UPARITY1"
        )

    def api(self, method, path, payload=None, *, key="synthetic-command", bearer=True):
        extra: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"} if bearer else {}
        if method == "get":
            return self.client.get(path, data=payload or {}, **extra)
        return self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            **extra,
        )

    def review_api(self, application, **changes):
        return self.api(
            "post",
            f"/api/v1/admin/slack/applications/{application.public_id}/review",
            {"action": "approve", "expected_state": "pending", **changes},
        )

    def review_html(self, application, **changes):
        return self.studio.post(
            reverse("studio:slack-review-action"),
            {
                "application_id": str(application.public_id),
                "action": "approve",
                "expected_state": "pending",
                "idempotency_key": "synthetic-html-command",
                **changes,
            },
        )

    def test_capabilities_bind_shared_services_runtime_routes_and_openapi(self):
        services = {
            "slack.applications.list": management_services.list_applications,
            "slack.applications.review": management_services.review_application,
            "slack.members.preview": management_services.ban_preview,
            "slack.members.ban": management_services.ban_member,
            "slack.joins.report": management_services.join_report,
        }
        document = generate_document()
        for key, service in services.items():
            capability = CAPABILITY_REGISTRY.require(key)
            self.assertIs(capability.service, service)
            callback = resolve(reverse(capability.studio.route)).func
            self.assertEqual(callback.__module__, "slack_access.studio_views")
            self.assertEqual(
                document["paths"][capability.admin_api.route.removeprefix("/api/v1/admin")][
                    capability.admin_api.method.lower()
                ]["operationId"],
                key,
            )
        self.assertFalse(parity_errors())

    def test_api_and_studio_approve_using_the_same_domain_policy_and_audit(self):
        api_application = self.application("api-approved")
        html_application = self.application("html-approved")
        response = self.review_api(api_application)
        self.assertEqual(response.status_code, 200, response.content)
        html = self.review_html(html_application)
        self.assertEqual(html.status_code, 302)
        api_application.refresh_from_db()
        html_application.refresh_from_db()
        self.assertEqual(api_application.state, html_application.state)
        self.assertEqual(api_application.approval_source, html_application.approval_source)
        self.assertEqual(api_application.state, Application.State.APPROVED)
        self.assertEqual(AuditEvent.objects.filter(action="slack.applications.review").count(), 2)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="slack.applications.review",
                api_principal=self.principal,
            ).exists()
        )
        self.assertNotIn("Synthetic questionnaire canary", response.content.decode())
        evidence = str(list(AuditEvent.objects.values("metadata", "changes")))
        evidence += str(list(IdempotencyRecord.objects.values("request_hash", "result")))
        self.assertNotIn("Synthetic questionnaire canary", evidence)
        self.assertNotIn("Synthetic workplace", evidence)

    def test_review_replay_and_stale_state_match_for_html_and_api(self):
        application = self.application("replay")
        first = self.review_api(application)
        self.assertEqual(first.status_code, 200)
        replay = self.review_api(application)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            Decision.objects.filter(application=application, action="approve_staff").count(), 1
        )
        stale = self.api(
            "post",
            f"/api/v1/admin/slack/applications/{application.public_id}/review",
            {"action": "reject", "expected_state": "pending"},
            key="stale-state",
        )
        self.assertEqual(stale.status_code, 409)
        html = self.review_html(application, action="reject")
        self.assertEqual(html.status_code, 409)
        conflict = self.review_api(application, action="reject")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")

    def test_review_without_sponsor_is_denied_by_both_adapters(self):
        application = self.application("no-sponsor")
        application.sponsor = None
        application.save(update_fields=["sponsor"])
        self.assertEqual(self.review_api(application).status_code, 403)
        self.assertEqual(self.review_html(application).status_code, 403)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.PENDING)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="slack.applications.review",
                outcome=AuditEvent.Outcome.DENIED,
            ).count(),
            2,
        )

    def test_list_and_join_report_are_private_bounded_and_have_no_raw_slack_secret(self):
        application = self.application("list")
        url = "/api/v1/admin/slack/applications"
        response = self.api("get", url, {"limit": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["applications"][0]["id"], str(application.public_id))
        self.assertEqual(
            response.json()["applications"][0]["who"], "Synthetic questionnaire canary"
        )
        html = self.studio.get(reverse("studio:slack-review"))
        self.assertContains(html, "Synthetic questionnaire canary")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertEqual(self.api("get", url, {"limit": 101}).status_code, 400)
        self.assertEqual(self.api("get", url + "?limit=1&limit=2").status_code, 400)
        JoinObservation.objects.create(slack_user_id="UOUTPAR1", classification="outside_flow")
        report = self.api("get", "/api/v1/admin/slack/joins")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["joins"][0]["classification"], "outside_flow")
        self.assertNotIn("redirect_digest", report.content.decode())
        self.assertNotIn("vouch_key", report.content.decode())
        self.assertNotIn("join.slack.com", report.content.decode())
        self.assertContains(self.studio.get(reverse("studio:slack-report")), "UOUTPAR1")

    def test_ban_preview_confirmation_scope_and_replay_preserve_api_html_parity(self):
        member = Membership.objects.create(slack_user_id="UBANCH01", sponsor=self.sponsor)
        path = f"/api/v1/admin/slack/members/{member.public_id}/ban"
        preview = self.api("get", path)
        self.assertEqual(preview.status_code, 200)
        count = preview.json()["affected_count"]
        self.assertEqual(count, 2)
        payload = {
            "confirmed": True,
            "expected_count": count,
            "scope_token": preview.json()["scope_token"],
        }
        self.assertEqual(self.api("post", path, {**payload, "confirmed": False}).status_code, 400)
        self.assertEqual(
            self.api("post", path, {**payload, "expected_count": count + 1}).status_code, 409
        )
        self.assertFalse(Membership.objects.filter(banned_at__isnull=False).exists())
        banned = self.api("post", path, payload)
        self.assertEqual(banned.status_code, 200, banned.content)
        self.assertEqual(banned.json()["affected_count"], count)
        self.assertTrue(self.api("post", path, payload).json()["replayed"])
        self.assertEqual(Membership.objects.filter(banned_at__isnull=False).count(), count)

        html_root = Membership.objects.create(slack_user_id="UBANHTML")
        html_child = Membership.objects.create(slack_user_id="UBANHCH1", sponsor=html_root)
        preview_html = self.studio.get(
            reverse("studio:slack-moderation"), {"member_id": str(html_child.public_id)}
        )
        self.assertEqual(preview_html.status_code, 200)
        self.assertContains(preview_html, 'name="scope_token"')
        response = self.studio.post(
            reverse("studio:slack-moderation-action"),
            {
                "member_id": str(html_child.public_id),
                "confirmed": "true",
                "expected_count": 2,
                "scope_token": preview_html.context["preview"]["scope_token"],
                "idempotency_key": "synthetic-html-ban",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        html_root.refresh_from_db()
        self.assertIsNotNone(html_root.banned_at)

    def test_scope_fingerprint_rejects_same_count_reparenting_and_pending_application_changes(self):
        member = Membership.objects.create(slack_user_id="USCOPE01", sponsor=self.sponsor)
        replacement = Membership.objects.create(slack_user_id="USCOPE02")
        path = f"/api/v1/admin/slack/members/{member.public_id}/ban"
        preview = self.api("get", path).json()
        self.assertEqual(preview["affected_count"], 2)
        member.sponsor = replacement
        member.save(update_fields=["sponsor"])
        payload = {
            "confirmed": True,
            "expected_count": preview["affected_count"],
            "scope_token": preview["scope_token"],
        }
        response = self.api("post", path, payload)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Membership.objects.filter(banned_at__isnull=False).exists())
        preview = self.api("get", path).json()
        pending = self.application("pending-scope")
        pending.sponsor = replacement
        pending.save(update_fields=["sponsor"])
        payload["scope_token"] = preview["scope_token"]
        response = self.api("post", path, payload, key="pending-scope-added")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Membership.objects.filter(banned_at__isnull=False).exists())
        preview = self.api("get", path).json()
        payload["scope_token"] = preview["scope_token"]
        pending.state = Application.State.REJECTED
        pending.save(update_fields=["state"])
        self.assertEqual(
            self.api("post", path, payload, key="pending-state-changed").status_code, 409
        )
        self.assertEqual(
            self.api(
                "post", path, {**payload, "scope_token": ""}, key="missing-scope-token"
            ).status_code,
            400,
        )

    def test_missing_scope_revocation_and_explicit_permissions_cannot_be_bypassed(self):
        application = self.application("denied")
        url = "/api/v1/admin/slack/applications"
        self.assertEqual(self.api("get", url, bearer=False).status_code, 401)
        restricted = generate_token()
        APICredential.objects.create(
            principal=self.principal,
            name="Synthetic read-only token",
            prefix=restricted.prefix,
            secret_digest=encode_secret(restricted.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=["slack.applications.list"],
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.token = restricted.raw
        self.assertEqual(self.review_api(application).status_code, 403)
        self.principal.permissions.clear()
        self.assertEqual(self.api("get", url).status_code, 403)
        self.staff.user_permissions.clear()
        self.assertEqual(self.review_html(application).status_code, 403)
        self.credential.revoked_at = timezone.now()
        self.credential.save(update_fields=["revoked_at"])
        APICredential.objects.filter(prefix=restricted.prefix).update(revoked_at=timezone.now())
        self.assertEqual(self.api("get", url).status_code, 401)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.PENDING)

    def test_mass_assignment_and_wrong_json_shapes_have_safe_client_errors(self):
        application = self.application("invalid")
        invalid_changes: list[dict[str, Any]] = [
            {"state": "approved"},
            {"action": []},
            {"expected_state": {}},
            {"application_id": str(application.public_id)},
        ]
        for changes in invalid_changes:
            response = self.review_api(application, **changes)
            self.assertEqual(response.status_code, 400, response.content)
        response = self.api(
            "post",
            f"/api/v1/admin/slack/applications/{application.public_id}/review",
            {"action": "approve", "expected_state": "pending"},
            key="",
        )
        self.assertEqual(response.status_code, 400)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.PENDING)

    def test_human_api_principal_requires_current_staff_status_like_html(self):
        user = self.person("nonstaff-human")
        user.user_permissions.add(*self.permissions)
        principal = APIPrincipal.objects.create(
            kind=APIPrincipal.Kind.HUMAN,
            user=user,
            name="Synthetic human reviewer",
            identity_snapshot=f"user:{user.pk}",
        )
        principal.permissions.add(*self.permissions)
        token = generate_token()
        APICredential.objects.create(
            principal=principal,
            name="Synthetic human token",
            prefix=token.prefix,
            secret_digest=encode_secret(token.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=["slack.applications.list"],
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.token = token.raw
        self.assertEqual(self.api("get", "/api/v1/admin/slack/applications").status_code, 403)
        html = authenticated_studio_client(user)
        self.assertEqual(html.get(reverse("studio:slack-review")).status_code, 403)
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.assertEqual(self.api("get", "/api/v1/admin/slack/applications").status_code, 200)
        user.is_staff = False
        user.save(update_fields=["is_staff"])
        self.assertEqual(self.api("get", "/api/v1/admin/slack/applications").status_code, 403)

    def test_management_targets_and_foreign_references_are_uuid_only(self):
        application = self.application("uuid-target")
        listed = self.api("get", "/api/v1/admin/slack/applications").json()["applications"][0]
        self.assertEqual(listed["id"], str(application.public_id))
        self.assertEqual(listed["sponsor_id"], str(self.sponsor.public_id))
        self.assertEqual(uuid.UUID(listed["id"]), application.public_id)
        self.assertEqual(
            self.api(
                "post",
                f"/api/v1/admin/slack/applications/{application.pk}/review",
                {"action": "approve", "expected_state": "pending"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.api("get", f"/api/v1/admin/slack/members/{self.sponsor.pk}/ban").status_code, 404
        )
        unknown = self.api(
            "post",
            f"/api/v1/admin/slack/applications/{uuid.uuid4()}/review",
            {"action": "approve", "expected_state": "pending"},
        )
        self.assertEqual(unknown.status_code, 404)
        invalid_html = self.review_html(application, application_id=str(application.pk))
        self.assertEqual(invalid_html.status_code, 400)
        JoinObservation.objects.create(
            slack_user_id="UUUIDRPT", classification="outside_flow", application=application
        )
        report = self.api("get", "/api/v1/admin/slack/joins").json()["joins"][0]
        self.assertEqual(report["application_id"], str(application.public_id))
        self.assertEqual(report["sponsor_id"], str(self.sponsor.public_id))
