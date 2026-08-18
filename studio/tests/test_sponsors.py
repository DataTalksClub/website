from __future__ import annotations

import uuid

from django.test import TestCase
from django.urls import reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import AuditEvent, Sponsor, SponsorRevision


class StudioSponsorTests(TestCase):
    def setUp(self) -> None:
        self.admin = make_studio_user(username="sponsor-admin", roles=("site_admin",))
        self.client = authenticated_studio_client(self.admin)
        self.url = reverse("studio:sponsor-list")

    def assert_private(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def create_payload(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "idempotency_key": str(uuid.uuid4()),
            "key": "acme",
            "name": "Acme Analytics",
            "url": "https://acme.example",
            "tagline": "Data for everyone",
            "lifecycle": "draft",
            "placement": "events_hub",
            "position": "1",
            "assignment_enabled": "true",
        }
        data.update(overrides)
        return data

    def test_empty_state_create_detail_and_navigation(self) -> None:
        response = self.client.get(self.url)
        self.assert_private(response, 200)
        self.assertContains(response, "There are no sponsors yet.")
        self.assertContains(response, "Create the first sponsor")
        self.assertContains(self.client.get(reverse("studio:home")), self.url)
        created = self.client.post(self.url, self.create_payload())
        self.assert_private(created, 302)
        sponsor = Sponsor.objects.get()
        self.assertEqual(created.headers["Location"], f"/studio/sponsors/{sponsor.id}/?saved=1")
        detail = self.client.get(created.headers["Location"])
        self.assert_private(detail, 200)
        self.assertContains(detail, "acme")
        self.assertContains(detail, "draft")
        self.assertContains(detail, "studio")
        self.assertContains(detail, "Revision 1")
        self.assertEqual(SponsorRevision.objects.count(), 1)

    def test_auditor_is_read_only_and_unrelated_role_is_denied(self) -> None:
        auditor = make_studio_user(username="sponsor-auditor", roles=("auditor",))
        auditor_client = authenticated_studio_client(auditor)
        empty = auditor_client.get(self.url)
        self.assert_private(empty, 200)
        self.assertNotContains(empty, "Create sponsor")
        self.assertNotContains(empty, "Export CSV")
        denied = auditor_client.post(self.url, self.create_payload())
        self.assert_private(denied, 403)
        self.assertFalse(Sponsor.objects.exists())
        outsider = make_studio_user(username="sponsor-outsider", roles=("course_operator",))
        outsider_client = authenticated_studio_client(outsider)
        self.assert_private(outsider_client.get(self.url), 403)
        home = outsider_client.get(reverse("studio:home"))
        self.assertNotContains(home, self.url)

    def test_validation_and_stale_edit_preserve_input(self) -> None:
        self.client.post(self.url, self.create_payload())
        sponsor = Sponsor.objects.get()
        detail_url = reverse("studio:sponsor-detail", args=[sponsor.id])
        invalid = self.client.post(
            detail_url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "1",
                "name": "Unsafe <markup>",
                "url": "http://acme.example",
                "tagline": "Kept",
                "lifecycle": "draft",
                "placement": "events_hub",
                "position": "1",
                "assignment_enabled": "true",
            },
        )
        self.assert_private(invalid, 400)
        self.assertContains(invalid, "Correct the highlighted fields", status_code=400)
        self.assertContains(invalid, "Kept", status_code=400)
        self.assertContains(invalid, "Unsafe &lt;markup&gt;", status_code=400)
        self.assertNotContains(invalid, "Unsafe <markup>", status_code=400)
        self.assertEqual(Sponsor.objects.get().name, "Acme Analytics")

        first = self.client.post(
            detail_url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "1",
                "name": "First save",
                "url": "https://acme.example",
                "tagline": "Data for everyone",
                "lifecycle": "active",
                "placement": "events_hub",
                "position": "1",
                "assignment_enabled": "true",
            },
        )
        self.assert_private(first, 302)
        stale = self.client.post(
            detail_url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "1",
                "name": "Stale proposed",
                "url": "https://acme.example",
                "tagline": "Data for everyone",
                "lifecycle": "active",
                "placement": "events_hub",
                "position": "1",
                "assignment_enabled": "true",
            },
        )
        self.assert_private(stale, 409)
        self.assertContains(stale, "Stale proposed", status_code=409)
        self.assertContains(stale, "Current revision 2", status_code=409)
        self.assertEqual(Sponsor.objects.get().name, "First save")

    def test_archive_reactivate_and_export(self) -> None:
        self.client.post(self.url, self.create_payload(lifecycle="active"))
        sponsor = Sponsor.objects.get()
        archive = self.client.post(
            reverse("studio:sponsor-archive", args=[sponsor.id]),
            {
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "1",
                "confirmed": "true",
            },
        )
        self.assert_private(archive, 302)
        self.assertEqual(Sponsor.objects.get().lifecycle, "archived")
        reactivate = self.client.post(
            reverse("studio:sponsor-reactivate", args=[sponsor.id]),
            {
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "2",
                "confirmed": "true",
            },
        )
        self.assert_private(reactivate, 302)
        self.assertEqual(Sponsor.objects.get().lifecycle, "active")
        export = self.client.post(
            reverse("studio:sponsor-export"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "confirmed": "true",
                "reason": "operator review",
            },
        )
        self.assert_private(export, 200)
        self.assertTrue(export.headers["Content-Type"].startswith("text/csv"))
        self.assertIn("acme", export.content.decode())
        self.assertEqual(
            AuditEvent.objects.filter(action="core.sponsor_directory.exported").count(),
            1,
        )
