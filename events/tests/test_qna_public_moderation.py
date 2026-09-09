"""Public Q&A privileged adapters authorize through the canonical Studio boundary."""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.test import Client, RequestFactory, TestCase, override_settings

from accounts.studio_sessions import (
    SESSION_REFERENCE_KEY,
    create_staff_session,
    revoke_staff_session,
)
from accounts.studio_test_support import make_studio_user
from core.models import AuditEvent
from events.identity import create_event_identity
from events.models import EventQnaSession
from events.qna import security, services

MODERATION_AUDIT = "events.qna.question_moderated"
DENIAL_AUDIT = "events_qna_moderate.audit"


def _question_payload(**fields: Any) -> str:
    return json.dumps(fields)


def _qna_urls(event: Any) -> dict[str, str]:
    base = services.event_qna_path(event)
    return {
        "page": f"{base}/",
        "questions": f"{base}/api/questions/",
        "host": f"{base}/host/",
        "present": f"{base}/present/",
    }


class PublicModerationTestBase(TestCase):
    def make_event(self):
        event = create_event_identity(
            title="Public moderation test event",
            source_repository="DataTalksClub/events",
            source_revision=uuid.uuid4().hex,
            source_key=f"public-moderation-{uuid.uuid4().hex[:8]}",
        )
        services.transition_session(event.id, EventQnaSession.State.OPEN)
        return event

    def make_staff_client(self, *, username: str = "qna-staff"):
        user = make_studio_user(username=username, roles=("site_admin",))
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        store = client.session
        request = RequestFactory().get("/")
        request.session = store
        session = create_staff_session(user=user, request=request)
        store.save()
        return client, user, session

    def csrf_token(self, client: Client, path: str) -> str:
        client.get(path)
        return client.cookies["csrftoken"].value

    def patch_question(
        self,
        client: Client,
        url: str,
        payload: dict[str, Any],
        *,
        token: str | None = None,
    ):
        if token is not None:
            return client.patch(
                url,
                data=_question_payload(**payload),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            )
        return client.patch(
            url,
            data=_question_payload(**payload),
            content_type="application/json",
        )

    def submit_question(self, event: Any, text: str = "First question"):
        participant, _token = security.new_participant()
        return services.submit_question(event.id, text=text, participant=participant)


class RevokedStaffSessionTests(PublicModerationTestBase):
    def test_revoked_staff_session_cannot_moderate_or_see_host_pages(self):
        event = self.make_event()
        question = self.submit_question(event)
        urls = _qna_urls(event)
        client, user, session = self.make_staff_client()
        revoke_staff_session(session.id, user=user)
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )
        host_response = client.get(urls["host"])
        present_response = client.get(urls["present"])

        self.assertEqual(response.status_code, 403)
        self.assertEqual(host_response.status_code, 403)
        self.assertEqual(present_response.status_code, 403)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.VISIBLE)
        self.assertFalse(AuditEvent.objects.filter(action=MODERATION_AUDIT).exists())

    def test_denied_staff_moderation_writes_one_safe_denial_record(self):
        event = self.make_event()
        question = self.submit_question(event, "Denial audit question")
        urls = _qna_urls(event)
        client, user, session = self.make_staff_client()
        revoke_staff_session(session.id, user=user)
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )

        self.assertEqual(response.status_code, 403)
        denials = AuditEvent.objects.filter(action=DENIAL_AUDIT, outcome="denied")
        self.assertEqual(denials.count(), 1)
        denial = denials.get()
        self.assertEqual(denial.actor_id, user.pk)
        self.assertNotIn("question", denial.metadata)
        self.assertNotIn("Denial audit question", str(denial.metadata))

    def test_missing_staff_session_reference_denies_moderation(self):
        event = self.make_event()
        question = self.submit_question(event)
        urls = _qna_urls(event)
        user = make_studio_user(username="no-staff-session", roles=("site_admin",))
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        # login binds a staff session by signal; strip it to model an
        # ordinary authenticated Django session with no staff elevation.
        store = client.session
        del store[SESSION_REFERENCE_KEY]
        store.save()
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )

        self.assertEqual(response.status_code, 403)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.VISIBLE)
        self.assertFalse(AuditEvent.objects.filter(action=MODERATION_AUDIT).exists())

    @override_settings(STUDIO_SESSION_ABSOLUTE_SECONDS=0)
    def test_absolute_expired_staff_session_denies_moderation(self):
        event = self.make_event()
        question = self.submit_question(event)
        urls = _qna_urls(event)
        client, _user, _session = self.make_staff_client()
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )

        self.assertEqual(response.status_code, 403)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.VISIBLE)

    def test_removed_permission_is_effective_on_next_request(self):
        event = self.make_event()
        question = self.submit_question(event)
        urls = _qna_urls(event)
        client, user, _session = self.make_staff_client()
        user.groups.clear()
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )

        self.assertEqual(response.status_code, 403)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.VISIBLE)

    def test_superuser_without_explicit_permission_is_denied(self):
        event = self.make_event()
        question = self.submit_question(event)
        urls = _qna_urls(event)
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="qna-superuser",
            email="",
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        store = client.session
        request = RequestFactory().get("/")
        request.session = store
        create_staff_session(user=user, request=request)
        store.save()
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )

        self.assertEqual(response.status_code, 403)


class AuthorizedModerationTests(PublicModerationTestBase):
    def test_valid_staff_session_moderates_with_single_safe_audit(self):
        event = self.make_event()
        question = self.submit_question(event, "Staff moderation question")
        urls = _qna_urls(event)
        client, user, _session = self.make_staff_client()
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
            token=token,
        )

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.ANSWERED)
        audits = AuditEvent.objects.filter(action=MODERATION_AUDIT)
        self.assertEqual(audits.count(), 1)
        audit = audits.get()
        self.assertEqual(audit.actor_id, user.pk)
        self.assertEqual(audit.actor_ref, f"user:{user.pk}")
        self.assertNotIn("Staff moderation question", str(audit.metadata))

    def test_missing_csrf_token_still_fails_staff_moderation(self):
        event = self.make_event()
        question = self.submit_question(event)
        urls = _qna_urls(event)
        client, _user, _session = self.make_staff_client()

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "answered"},
        )

        self.assertEqual(response.status_code, 403)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.VISIBLE)

    def test_moderator_page_access_requires_current_authorization(self):
        event = self.make_event()
        urls = _qna_urls(event)
        client, _user, _session = self.make_staff_client()

        host_response = client.get(urls["host"])

        self.assertEqual(host_response.status_code, 200)


class CohostModerationTests(PublicModerationTestBase):
    def make_cohost_client(self, event: Any, *, name: str = "moderator"):
        services.create_cohost(
            event.id,
            name=name,
            passcode="open-sesame-42",
            actor_ref="user:1",
        )
        invite, error = services.redeem_cohost(event.id, name, "open-sesame-42")
        self.assertIsNotNone(invite, error)
        assert invite is not None
        client = Client(enforce_csrf_checks=True)
        client.cookies[security.COHOST_COOKIE] = security.new_cohost_token(
            str(invite.session_id), invite.invite_id
        )
        return client, invite

    def test_valid_cohost_moderates_with_opaque_audit_reference(self):
        event = self.make_event()
        question = self.submit_question(event, "Cohort moderation question")
        urls = _qna_urls(event)
        client, invite = self.make_cohost_client(event)
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"pinned": True},
            token=token,
        )

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertTrue(question.pinned)
        audits = AuditEvent.objects.filter(action=MODERATION_AUDIT)
        self.assertEqual(audits.count(), 1)
        audit = audits.get()
        self.assertIsNone(audit.actor_id)
        self.assertEqual(audit.actor_ref, f"cohost:{invite.invite_id}")

    def test_cohost_grant_is_scoped_to_its_session(self):
        other_event = self.make_event()
        other_question = self.submit_question(other_event, "Other room question")
        event = self.make_event()
        urls = _qna_urls(other_event)
        client, _invite = self.make_cohost_client(event)
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{other_question.question_id}/",
            {"pinned": True},
            token=token,
        )

        self.assertEqual(response.status_code, 403)
        other_question.refresh_from_db()
        self.assertFalse(other_question.pinned)


class ParticipantContinuityTests(PublicModerationTestBase):
    def test_anonymous_participant_can_still_withdraw_own_question(self):
        event = self.make_event()
        participant, participant_token = security.new_participant()
        question = services.submit_question(event.id, text="Withdraw me", participant=participant)
        urls = _qna_urls(event)
        client = Client(enforce_csrf_checks=True)
        client.cookies[security.PARTICIPANT_COOKIE] = participant_token
        token = self.csrf_token(client, urls["page"])

        response = self.patch_question(
            client,
            f"{urls['questions']}{question.question_id}/",
            {"status": "deleted"},
            token=token,
        )

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.status, question.Status.DELETED)
        self.assertFalse(AuditEvent.objects.filter(action=MODERATION_AUDIT).exists())
