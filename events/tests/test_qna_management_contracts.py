"""Studio and management mutations carry the same guarantees (audit EVT-02).

The Studio adapters once called the low-level Q&A services directly: no
revision check, the rendered idempotency key ignored, unrendered settings
written back as defaults, and no server-side confirmation on retry/revoke.
These tests pin the paired adapter contract -- the same guarantee holds no
matter which authorized surface a command arrives from:

* a stale or missing session revision writes nothing (409/400, never
  reload-and-overwrite);
* repeating the same actor/key/payload replays without executing again -- even
  when the retried form's revision has since gone stale;
* settings set through the API survive an unrelated Studio save, because the
  Studio form writes only the settings it renders;
* management question moderation enforces its declared If-Match contract;
* retry and revoke refuse to run without the explicit confirmation field.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from events.identity import create_event_identity
from events.models import EventQnaSession
from events.qna import security, services
from management_auth.models import APIPrincipal
from management_auth.services import create_principal, issue_credential_once


class ManagementContractTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Adapter contract test event",
            source_repository="DataTalksClub/events",
            source_revision="0" * 40,
            source_key="adapter-contract-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        participant, _token = security.new_participant()
        self.question = services.submit_question(
            self.event.id, text="Contract test question", participant=participant
        )
        self.studio_client = authenticated_studio_client(
            make_studio_user(username="qna-operator", roles=("event_operator",))
        )
        permission = Permission.objects.get(
            content_type__app_label="events",
            codename="manage_event_qna",
        )
        principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Q&A adapter contract test",
            identity_snapshot="service:adapter-contract",
            permissions=(permission,),
        )
        issued = issue_credential_once(
            actor_principal=principal,
            target_principal_id=principal.id,
            name="Q&A adapter contract credential",
            scopes=(
                "events.qna.read",
                "events.qna.manage",
                "events.qna.moderate",
            ),
            idempotency_key="adapter-contract-credential",
            actor_permission="events.manage_event_qna",
        )
        self.bearer = str(issued.response["token"])

    # -- helpers ---------------------------------------------------------

    def _session(self) -> EventQnaSession:
        return EventQnaSession.objects.get(event=self.event)

    def _studio_update(
        self,
        *,
        revision: str | None = "current",
        key: str = "studio-retry-key-1",
        extra: dict[str, str] | None = None,
    ):
        if revision == "current":
            revision = str(self._session().revision)
        payload: dict[str, Any] = {
            "state": "open",
            "allow_names": "on",
            "default_sort": "recent",
        }
        if revision is not None:
            payload["revision"] = revision
        payload["idempotency_key"] = key
        payload.update(extra or {})
        return self.studio_client.post(
            reverse("studio:event-qna-update", kwargs={"event_id": self.event.id}), payload
        )

    def _api_moderate(self, payload: dict[str, Any], *, if_match: str | None = "current"):
        if if_match == "current":
            if_match = f'"rev-{self._session().revision}"'
        headers: dict[str, Any] = {
            "HTTP_AUTHORIZATION": f"Bearer {self.bearer}",
            "HTTP_IDEMPOTENCY_KEY": "api-moderate-key-1",
        }
        if if_match is not None:
            headers["HTTP_IF_MATCH"] = if_match
        return self.client.patch(
            reverse(
                "api:admin-event-qna-moderate",
                kwargs={"event_id": self.event.id, "question_id": self.question.question_id},
            ),
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    # -- session revision and idempotency --------------------------------

    def test_a_stale_studio_session_form_writes_nothing(self) -> None:
        before = self._session()

        response = self._studio_update(revision="999")

        self.assertEqual(response.status_code, 409, response.content)
        after = self._session()
        self.assertEqual(
            (after.revision, after.state, after.default_sort),
            (before.revision, before.state, before.default_sort),
        )

    def test_a_studio_session_form_without_a_revision_writes_nothing(self) -> None:
        before = self._session()

        response = self._studio_update(revision=None)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self._session().revision, before.revision)

    def test_a_studio_session_replay_does_not_execute_again(self) -> None:
        first = self._studio_update(key="studio-retry-key-1")
        self.assertEqual(first.status_code, 302, first.content)
        after_first = self._session().revision

        # A browser retry resubmits the same rendered form: same key and the
        # same -- now stale -- hidden revision.  The replay must return the
        # stored result instead of refusing or re-executing.
        retry = self._studio_update(revision=str(after_first - 1), key="studio-retry-key-1")

        self.assertEqual(retry.status_code, 302, retry.content)
        self.assertEqual(self._session().revision, after_first)

    def test_api_settings_survive_an_unrelated_studio_save(self) -> None:
        services.update_session(
            self.event.id,
            {"settings": {"listed": False, "answered_placement": "bottom"}},
        )
        session = self._session()
        self.assertFalse(session.listed)
        self.assertEqual(session.answered_placement, "bottom")

        response = self._studio_update()

        self.assertEqual(response.status_code, 302, response.content)
        session = self._session()
        # The Studio form does not render listed or answered placement, so its
        # save must not reset them to the form's own defaults.
        self.assertFalse(session.listed)
        self.assertEqual(session.answered_placement, "bottom")
        self.assertEqual(session.default_sort, "recent")

    # -- question moderation concurrency ---------------------------------

    def test_api_moderation_requires_and_honors_if_match(self) -> None:
        missing = self._api_moderate({"status": "answered"}, if_match=None)
        self.assertEqual(missing.status_code, 428, missing.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "visible")

        stale = self._api_moderate({"status": "answered"}, if_match='"rev-1"')
        self.assertEqual(stale.status_code, 409, stale.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "visible")

        current = self._api_moderate({"status": "answered"})
        self.assertEqual(current.status_code, 200, current.content)
        self.assertEqual(current.json()["status"], "answered")
        new_revision = self._session().revision
        self.assertEqual(current.json()["session_revision"], new_revision)
        self.assertEqual(current.headers["ETag"], f'"rev-{new_revision}"')

    def test_studio_moderation_honors_the_same_revision_contract(self) -> None:
        url = reverse(
            "studio:event-qna-moderate",
            kwargs={"event_id": self.event.id, "question_id": self.question.question_id},
        )
        stale = self.studio_client.post(
            url,
            {"action": "answer", "revision": "999", "idempotency_key": "mod-key-1"},
        )
        self.assertEqual(stale.status_code, 409, stale.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "visible")

        current = self.studio_client.post(
            url,
            {
                "action": "answer",
                "revision": str(self._session().revision),
                "idempotency_key": "mod-key-2",
            },
        )
        self.assertEqual(current.status_code, 302, current.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "answered")

    def test_studio_pin_and_unpin_follow_the_rendered_state(self) -> None:
        # UX-12: the Studio template once offered Pin unconditionally, so a
        # pinned question had no way to be unpinned from this surface even
        # though the view supported it.  The action/label pair must follow
        # the rendered server state, and a moderator can undo a pin here.
        detail_url = reverse("studio:event-qna-detail", kwargs={"event_id": self.event.id})
        moderate_url = reverse(
            "studio:event-qna-moderate",
            kwargs={"event_id": self.event.id, "question_id": self.question.question_id},
        )

        def _moderate(action: str, key: str):
            return self.studio_client.post(
                moderate_url,
                {
                    "action": action,
                    "revision": str(self._session().revision),
                    "idempotency_key": key,
                },
            )

        pinned = _moderate("pin", "pin-key-1")
        self.assertEqual(pinned.status_code, 302, pinned.content)
        self.question.refresh_from_db()
        self.assertTrue(self.question.pinned)

        rendered = self.studio_client.get(detail_url)
        self.assertEqual(rendered.status_code, 200, rendered.content)
        self.assertIn(b'value="unpin"', rendered.content)
        self.assertNotIn(b'value="pin"', rendered.content)
        # The saved redirect has a user-visible status.
        saved_page = self.studio_client.get(detail_url, {"saved": "1"})
        self.assertIn(b"Saved.", saved_page.content)

        unpinned = _moderate("unpin", "unpin-key-1")
        self.assertEqual(unpinned.status_code, 302, unpinned.content)
        self.question.refresh_from_db()
        self.assertFalse(self.question.pinned)

        rendered = self.studio_client.get(detail_url)
        self.assertIn(b'value="pin"', rendered.content)
        self.assertNotIn(b'value="unpin"', rendered.content)

    # -- confirmation contracts ------------------------------------------

    def test_studio_retry_requires_explicit_confirmation(self) -> None:
        url = reverse("studio:event-qna-retry", kwargs={"event_id": self.event.id})
        unconfirmed = self.studio_client.post(url, {"idempotency_key": "retry-key-1"})
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.content)

        confirmed = self.studio_client.post(
            url, {"confirmed": "true", "idempotency_key": "retry-key-2"}
        )
        self.assertEqual(confirmed.status_code, 302, confirmed.content)

    def test_studio_revoke_requires_explicit_confirmation(self) -> None:
        invite = services.create_cohost(
            self.event.id,
            name="operator",
            passcode="open-sesame-42",
            actor_ref="user:0",
        )
        url = reverse(
            "studio:event-qna-cohost-revoke",
            kwargs={"event_id": self.event.id, "invite_id": str(invite["invite_id"])},
        )
        unconfirmed = self.studio_client.post(url, {"idempotency_key": "revoke-key-1"})
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.content)

        confirmed = self.studio_client.post(
            url,
            {"confirmed": "true", "idempotency_key": "revoke-key-2"},
        )
        self.assertEqual(confirmed.status_code, 302, confirmed.content)

    def test_studio_cohost_create_replay_creates_one_invite_and_no_passcode(
        self,
    ) -> None:
        url = reverse("studio:event-qna-cohost", kwargs={"event_id": self.event.id})
        first = self.studio_client.post(
            url,
            {"name": "operator", "passcode": "open-sesame-42", "idempotency_key": "grant-key-1"},
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertIn("open-sesame-42", first.content.decode())

        retry = self.studio_client.post(
            url,
            {"name": "operator", "passcode": "open-sesame-42", "idempotency_key": "grant-key-1"},
        )

        # The replay neither creates a second invite nor shows the passcode
        # again: the operator is redirected back to the session page.
        self.assertEqual(retry.status_code, 302, retry.content)
        from events.models import EventQnaCohostInvite

        invites = EventQnaCohostInvite.objects.filter(session__event=self.event)
        self.assertEqual(invites.count(), 1)
        self.assertNotIn(b"open-sesame-42", retry.content)
