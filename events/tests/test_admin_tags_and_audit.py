"""Event admin writes: English-only public text and a success audit trail."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from community_base.events.models import Event
from community_base.kernel.services import ServiceContext
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.studio_roles import set_single_studio_role, synchronize_studio_roles
from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.cyrillic import contains_cyrillic
from core.models import AuditEvent
from events.admin_services import (
    EventAdminError,
    create_event,
    create_speaker,
    message_event_registrants,
    update_event,
)
from management_auth.constants import DEFAULT_CREDENTIAL_LIFETIME, DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token

User = get_user_model()


def _zoom_digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _payload(**overrides: object) -> dict[str, object]:
    start = (timezone.now() + timedelta(days=7)).replace(microsecond=0)
    payload: dict[str, object] = {
        "title": "Audit Webinar",
        "type": "webinar",
        "description": "Talking about audits.",
        "start_datetime": start.isoformat(),
        "end_datetime": (start + timedelta(hours=1)).isoformat(),
        "zoom_join_url": "https://zoom.example/j/123?pwd=hidden",
        "tags": ["Academy"],
        "publish": False,
    }
    payload.update(overrides)
    return payload


class ContainsCyrillicTests(TestCase):
    def test_detects_words_and_homoglyphs_but_not_latin(self) -> None:
        self.assertTrue(contains_cyrillic("Академия"))
        self.assertTrue(contains_cyrillic("topiс"))  # Cyrillic small es
        self.assertTrue(contains_cyrillic("Ԁ"))  # Cyrillic Supplement
        self.assertTrue(contains_cyrillic("Ꙁ"))  # Cyrillic Extended-B
        self.assertFalse(contains_cyrillic("Academy"))
        self.assertFalse(contains_cyrillic("Zoë, naïve, ß, Ω, 学"))


class EventTagValidationTests(TestCase):
    def test_create_rejects_cyrillic_display_tag(self) -> None:
        events_before = Event.objects.count()
        with self.assertRaises(EventAdminError) as caught:
            create_event(_payload(tags=["mlops", "Академия"]))
        self.assertIn("Event tags must be English", str(caught.exception))
        self.assertIn("Cyrillic", str(caught.exception))
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(Event.objects.count(), events_before)
        self.assertFalse(AuditEvent.objects.exists())

    def test_create_rejects_homoglyph_tag(self) -> None:
        with self.assertRaises(EventAdminError):
            create_event(_payload(tags=["Acаdemy"]))  # Cyrillic small a

    def test_update_rejects_cyrillic_tag_and_leaves_event_unchanged(self) -> None:
        created = create_event(_payload())["event"]
        with self.assertRaises(EventAdminError):
            update_event({"tags": ["Академия"]}, event_id=created["id"])
        event = Event.objects.get(content_id=created["id"])
        self.assertIn("Academy", event.tags)
        self.assertFalse(any(contains_cyrillic(tag) for tag in event.tags))
        self.assertFalse(
            AuditEvent.objects.filter(action="events.admin.updated").exists(),
        )

    def test_update_can_replace_a_legacy_cyrillic_tag(self) -> None:
        created = create_event(_payload())["event"]
        Event.objects.filter(content_id=created["id"]).update(tags=["dtc-type:webinar", "Академия"])
        # Any other edit is refused while the bad tag would be re-saved ...
        with self.assertRaises(EventAdminError):
            update_event({"title": "Renamed"}, event_id=created["id"])
        # ... and replacing the tags fixes it.
        update_event({"tags": ["Academy"]}, event_id=created["id"])
        event = Event.objects.get(content_id=created["id"])
        self.assertEqual(event.tags, ["dtc-type:webinar", "Academy"])


class EventTextValidationTests(TestCase):
    """Every author-entered public text field is English-only, not just tags."""

    def test_create_rejects_cyrillic_in_each_public_text_field(self) -> None:
        cases: dict[str, dict[str, object]] = {
            "title": {"title": "Вебинар"},
            "slug": {"slug": "vebinar-ак"},
            "description": {"description": "We cover the topiс of audits."},
            "location": {"location": "Москва"},
            "materials": {"materials": [{"label": "Слайды", "url": "https://example.com/s"}]},
            "sponsor_name": {"sponsor_name": "Акме"},
        }
        events_before = Event.objects.count()
        for field, override in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(EventAdminError) as caught:
                    create_event(_payload(**override))
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "validation_error")
                self.assertIn(f"Event {field} must be English", str(caught.exception))
        self.assertEqual(Event.objects.count(), events_before)
        self.assertFalse(AuditEvent.objects.exists())

    def test_message_names_the_homoglyph_codepoint(self) -> None:
        with self.assertRaises(EventAdminError) as caught:
            create_event(_payload(description="the topiс of deploying"))
        self.assertIn("U+0441", str(caught.exception))

    def test_update_rejects_cyrillic_title_and_description(self) -> None:
        created = create_event(_payload())["event"]
        for field, value in (("title", "Вебинар"), ("description", "topiс")):
            with self.subTest(field=field):
                with self.assertRaises(EventAdminError) as caught:
                    update_event({field: value}, event_id=created["id"])
                self.assertIn(f"Event {field} must be English", str(caught.exception))
        event = Event.objects.get(content_id=created["id"])
        self.assertEqual(event.title, "Audit Webinar")
        self.assertEqual(event.description, "Talking about audits.")

    def test_legacy_cyrillic_description_is_editable_once_fixed_in_the_request(self) -> None:
        created = create_event(_payload())["event"]
        Event.objects.filter(content_id=created["id"]).update(
            description="We will get deeper into the topiс."
        )
        with self.assertRaises(EventAdminError):
            update_event({"title": "Renamed"}, event_id=created["id"])
        update_event(
            {"title": "Renamed", "description": "We will get deeper into the topic."},
            event_id=created["id"],
        )
        event = Event.objects.get(content_id=created["id"])
        self.assertEqual(event.title, "Renamed")
        self.assertEqual(event.description, "We will get deeper into the topic.")

    def test_speaker_and_registrant_message_text_must_be_english(self) -> None:
        with self.assertRaises(EventAdminError) as caught:
            create_speaker({"short": "ivan", "name": "Иван"})
        self.assertIn("Event speaker name must be English", str(caught.exception))
        created = create_event(_payload())["event"]
        with self.assertRaises(EventAdminError) as caught:
            message_event_registrants(
                {"subject": "Привет", "body": "Hello", "dry_run": True},
                event_id=created["id"],
            )
        self.assertIn("Event message subject must be English", str(caught.exception))


class EventWriteAuditTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="auditor", password="pw", is_staff=True)
        self.context = ServiceContext.from_current(actor_ref=f"user:{self.user.pk}")

    def test_create_records_created_audit_with_actor_and_fields(self) -> None:
        created = create_event(_payload(), context=self.context)["event"]

        audit = AuditEvent.objects.get(action="events.admin.created")
        self.assertEqual(audit.outcome, AuditEvent.Outcome.SUCCEEDED)
        self.assertEqual(audit.target_type, "events.event")
        self.assertEqual(str(audit.target_id), created["id"])
        self.assertEqual(audit.actor_ref, f"user:{self.user.pk}")
        self.assertEqual(audit.actor_id, self.user.pk)
        self.assertEqual(
            audit.changes["tags"], {"before": None, "after": ["dtc-type:webinar", "Academy"]}
        )
        self.assertEqual(audit.changes["title"]["after"], "Audit Webinar")
        self.assertEqual(
            audit.changes["zoom_join_url_sha256_12"],
            {"before": None, "after": _zoom_digest("https://zoom.example/j/123?pwd=hidden")},
        )
        serialized = json.dumps(audit.changes) + json.dumps(audit.metadata)
        self.assertNotIn("zoom.example", serialized)
        self.assertNotIn("pwd=hidden", serialized)
        self.assertNotIn("Talking about audits", serialized)

    def test_update_records_only_changed_fields_before_and_after(self) -> None:
        created = create_event(_payload(), context=self.context)["event"]

        update_event(
            {"tags": ["Hireable"], "title": "Renamed"},
            event_id=created["id"],
            context=self.context,
        )

        audit = AuditEvent.objects.get(action="events.admin.updated")
        self.assertEqual(audit.outcome, AuditEvent.Outcome.SUCCEEDED)
        self.assertEqual(str(audit.target_id), created["id"])
        self.assertEqual(audit.actor_id, self.user.pk)
        self.assertEqual(
            audit.changes,
            {
                "title": {"before": "Audit Webinar", "after": "Renamed"},
                "tags": {
                    "before": ["dtc-type:webinar", "Academy"],
                    "after": ["dtc-type:webinar", "Hireable"],
                },
            },
        )

    def test_description_change_is_recorded_as_digest_not_text(self) -> None:
        created = create_event(_payload(), context=self.context)["event"]

        update_event({"description": "Secret agenda"}, event_id=created["id"], context=self.context)

        audit = AuditEvent.objects.get(action="events.admin.updated")
        self.assertEqual(set(audit.changes), {"description"})
        change = audit.changes["description"]
        self.assertEqual(change["after"]["length"], len("Secret agenda"))
        self.assertNotEqual(change["before"]["sha256"], change["after"]["sha256"])
        self.assertNotIn("Secret agenda", json.dumps(audit.changes))

    def test_replacing_the_zoom_link_is_visible_as_a_digest_change(self) -> None:
        created = create_event(_payload(), context=self.context)["event"]
        new_url = "https://zoom.example/j/456?pwd=other"

        update_event({"zoom_join_url": new_url}, event_id=created["id"], context=self.context)

        audit = AuditEvent.objects.get(action="events.admin.updated")
        self.assertEqual(
            audit.changes,
            {
                "zoom_join_url_sha256_12": {
                    "before": _zoom_digest("https://zoom.example/j/123?pwd=hidden"),
                    "after": _zoom_digest(new_url),
                }
            },
        )
        self.assertNotIn("zoom.example", json.dumps(audit.changes))

    def test_staff_actor_ref_resolves_actor_id(self) -> None:
        create_event(
            _payload(), context=ServiceContext.from_current(actor_ref=f"staff:{self.user.pk}")
        )
        audit = AuditEvent.objects.get(action="events.admin.created")
        self.assertEqual(audit.actor_ref, f"staff:{self.user.pk}")
        self.assertEqual(audit.actor_id, self.user.pk)
        self.assertIsNone(audit.api_principal_id)

    def test_actor_ref_for_a_missing_user_keeps_only_the_reference(self) -> None:
        create_event(_payload(), context=ServiceContext.from_current(actor_ref="staff:999999"))
        audit = AuditEvent.objects.get(action="events.admin.created")
        self.assertEqual(audit.actor_ref, "staff:999999")
        self.assertIsNone(audit.actor_id)

    def test_operator_actor_ref_without_context_still_audits(self) -> None:
        created = create_event(_payload())["event"]
        update_event(
            {"tags": ["Academy"]},
            event_id=created["id"],
            context=ServiceContext.from_current(actor_ref="operator:tag-fix"),
        )
        self.assertEqual(
            sorted(AuditEvent.objects.values_list("action", flat=True)),
            ["events.admin.created", "events.admin.updated"],
        )
        updated = AuditEvent.objects.get(action="events.admin.updated")
        self.assertEqual(updated.actor_ref, "operator:tag-fix")
        self.assertIsNone(updated.actor_id)
        self.assertEqual(updated.changes, {})


class EventAdminAPIAuditTests(TestCase):
    def setUp(self) -> None:
        synchronize_studio_roles()
        self.operator = User.objects.create_user(username="event-op", password="pw", is_staff=True)
        set_single_studio_role(self.operator, "event_operator")
        self.principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="Event Operator",
            identity_snapshot="event-op",
            user=self.operator,
            created_by=self.operator,
        )
        group = self.operator.groups.first()
        assert group is not None
        self.principal.permissions.set(group.permissions.all())
        generated = generate_token()
        APICredential.objects.create(
            principal=self.principal,
            name="test-token",
            prefix=generated.prefix,
            digest_algorithm=DIGEST_ALGORITHM,
            digest_version=DIGEST_VERSION,
            secret_digest=encode_secret(generated.secret),
            scopes=["events.manage.create", "events.manage.update"],
            expires_at=timezone.now() + DEFAULT_CREDENTIAL_LIFETIME,
            created_by=self.operator,
        )
        self.headers = {"Authorization": f"Bearer {generated.raw}"}
        self.client = Client()

    def test_api_rejects_cyrillic_tag_and_audits_successful_writes(self) -> None:
        events_before = Event.objects.count()
        rejected = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(_payload(tags=["Академия"])),
            content_type="application/json",
            headers=self.headers,
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("Cyrillic", rejected.content.decode())
        self.assertEqual(Event.objects.count(), events_before)

        created = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(_payload()),
            content_type="application/json",
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.content)
        event_id = created.json()["event"]["id"]

        # The rejected request is audited as denied by the API adapter; the
        # successful one by the service, against the event it created.
        denied = AuditEvent.objects.get(
            action="events.admin.created", outcome=AuditEvent.Outcome.DENIED
        )
        self.assertEqual(denied.metadata["reason"], "validation_error")
        audit = AuditEvent.objects.get(
            action="events.admin.created", outcome=AuditEvent.Outcome.SUCCEEDED
        )
        self.assertEqual(audit.target_type, "events.event")
        self.assertEqual(audit.actor_ref, f"api_principal:{self.principal.id}")
        self.assertEqual(str(audit.target_id), event_id)
        # Both outcomes for one principal carry the same attribution.
        self.assertEqual(audit.api_principal_id, self.principal.id)
        self.assertEqual(audit.actor_id, self.operator.pk)
        self.assertEqual(
            (denied.actor_ref, denied.actor_id, denied.api_principal_id),
            (audit.actor_ref, audit.actor_id, audit.api_principal_id),
        )


class StudioEventAuditTests(TestCase):
    def test_studio_create_is_attributed_to_the_staff_user(self) -> None:
        operator = make_studio_user(username="studio-event-op", roles=("event_operator",))
        client = authenticated_studio_client(operator)

        rejected = client.post(
            "/studio/events/create/",
            data=json.dumps(_payload(title="Вебинар")),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("Event title must be English", rejected.json()["error"]["message"])

        created = client.post(
            "/studio/events/create/",
            data=json.dumps(_payload()),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        audit = AuditEvent.objects.get(
            action="events.admin.created", outcome=AuditEvent.Outcome.SUCCEEDED
        )
        self.assertEqual(audit.actor_ref, f"staff:{operator.pk}")
        self.assertEqual(audit.actor_id, operator.pk)
        self.assertEqual(str(audit.target_id), created.json()["event"]["id"])
