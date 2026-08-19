from __future__ import annotations

import uuid

from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.idempotency import JsonObject
from core.models import AuditEvent, SiteNavigationEntry, SiteNavigationMenu, SiteNavigationRevision
from core.navigation import default_navigation_entries


class StudioSiteNavigationTests(TestCase):
    def setUp(self) -> None:
        self.admin = make_studio_user(username="navigation-admin", roles=("site_admin",))
        self.client = authenticated_studio_client(self.admin)
        self.url = reverse("studio:navigation")

    def assert_private(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def post_menu(
        self,
        client: Client,
        entries: list[JsonObject],
        *,
        revision: int,
        idempotency_key: str | None = None,
    ):
        data: dict[str, str] = {
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            "expected_revision": str(revision),
        }
        for index, entry in enumerate(entries):
            data[f"entry-{index}-key"] = str(entry["key"])
            data[f"entry-{index}-label"] = str(entry["label"])
            data[f"entry-{index}-target"] = str(entry["target"])
            data[f"entry-{index}-position"] = str(entry["position"])
            if entry.get("visible") is True:
                data[f"entry-{index}-visible"] = "true"
        return client.post(self.url, data)

    def test_canonical_get_head_navigation_and_read_only_contract(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)
        self.assert_private(response, 200)
        navigation_queries = [
            query
            for query in captured.captured_queries
            if "core_sitenavigation" in query["sql"].casefold()
        ]
        self.assertLessEqual(len(navigation_queries), 2)
        self.assertEqual(self.url, "/studio/navigation")
        self.assertContains(response, "Site navigation")
        self.assertContains(response, "Code default")
        self.assertContains(response, "Revision 0")
        self.assertContains(response, "Save site navigation")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="expected_revision"')
        self.assertContains(response, 'name="idempotency_key"')
        self.assertContains(response, 'name="entry-0-label"')

        head = self.client.head(self.url)
        self.assert_private(head, 200)
        self.assertEqual(head.content, b"")
        with self.assertRaises(Resolver404):
            resolve(f"{self.url}/")

        auditor = make_studio_user(username="navigation-auditor", roles=("auditor",))
        auditor_client = authenticated_studio_client(auditor)
        read_only = auditor_client.get(self.url)
        self.assert_private(read_only, 200)
        self.assertContains(read_only, "Site navigation")
        self.assertContains(read_only, "disabled")
        self.assertContains(read_only, "readonly")
        self.assertNotContains(read_only, "Save site navigation")
        denied_post = self.post_menu(
            auditor_client,
            [entry.as_dict() for entry in default_navigation_entries()],
            revision=0,
        )
        self.assert_private(denied_post, 403)
        self.assertFalse(SiteNavigationMenu.objects.exists())

        outsider = make_studio_user(username="navigation-outsider", roles=("course_operator",))
        outsider_client = authenticated_studio_client(outsider)
        self.assert_private(outsider_client.get(self.url), 403)
        home = outsider_client.get(reverse("studio:home"))
        self.assertNotContains(home, self.url)
        self.assertContains(self.client.get(reverse("studio:home")), self.url)

    def test_post_uses_prg_and_refresh_does_not_duplicate_change_evidence(self) -> None:
        entries = [entry.as_dict() for entry in default_navigation_entries()]
        entries[0] = {**entries[0], "label": "  Gatherings  "}
        entries[1] = {**entries[1], "visible": False}
        entries.append(
            {
                "key": "home",
                "label": "Home",
                "target": "home",
                "position": 10,
                "visible": True,
            }
        )
        response = self.post_menu(self.client, entries, revision=0)
        self.assert_private(response, 302)
        self.assertEqual(response.headers["Location"], f"{self.url}?saved=1")
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "Gatherings")
        self.assertFalse(SiteNavigationEntry.objects.get(key="courses").visible)
        self.assertTrue(SiteNavigationEntry.objects.filter(key="home").exists())
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.site_navigation.updated").count(),
            1,
        )

        success = self.client.get(response.headers["Location"])
        self.assert_private(success, 200)
        self.assertContains(success, "Site navigation saved.")
        refresh = self.client.get(response.headers["Location"])
        self.assert_private(refresh, 200)
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)

    def test_validation_and_stale_conflict_preserve_safe_form_state(self) -> None:
        entries = [entry.as_dict() for entry in default_navigation_entries()]
        invalid_key = str(uuid.uuid4())
        invalid = self.post_menu(
            self.client,
            [{**entries[0], "label": "unsafe\nmessage"}, *entries[1:]],
            revision=0,
            idempotency_key=invalid_key,
        )
        self.assert_private(invalid, 400)
        self.assertContains(invalid, "There is a problem", status_code=400)
        self.assertContains(invalid, 'id="navigation-errors"', status_code=400)
        self.assertContains(invalid, 'tabindex="-1"', status_code=400)
        self.assertContains(invalid, invalid_key, status_code=400)
        self.assertContains(invalid, "unsafe\nmessage", status_code=400)
        self.assertFalse(SiteNavigationMenu.objects.exists())

        first_client = authenticated_studio_client(self.admin)
        second_client = authenticated_studio_client(self.admin)
        self.assert_private(first_client.get(self.url), 200)
        self.assert_private(second_client.get(self.url), 200)
        first = self.post_menu(
            first_client,
            [{**entries[0], "label": "First save"}, *entries[1:]],
            revision=0,
        )
        self.assert_private(first, 302)
        stale_key = str(uuid.uuid4())
        stale = self.post_menu(
            second_client,
            [{**entries[0], "label": "Second proposed save"}, *entries[1:]],
            revision=0,
            idempotency_key=stale_key,
        )
        self.assert_private(stale, 409)
        self.assertContains(stale, "changed in another session", status_code=409)
        self.assertContains(stale, "Second proposed save", status_code=409)
        self.assertContains(stale, stale_key, status_code=409)
        self.assertContains(stale, 'href="#site-navigation-fields"', status_code=409)
        self.assertContains(stale, "Studio", status_code=409)
        self.assertContains(stale, "Revision 1", status_code=409)
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "First save")
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)

    def test_csrf_method_anonymous_and_permission_removal_fail_before_mutation(self) -> None:
        entries = [entry.as_dict() for entry in default_navigation_entries()]
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        missing_csrf = self.post_menu(
            csrf_client,
            [{**entries[0], "label": "CSRF denied"}, *entries[1:]],
            revision=0,
        )
        self.assert_private(missing_csrf, 403)
        self.assertFalse(SiteNavigationMenu.objects.exists())

        page = csrf_client.get(self.url)
        token = page.cookies["csrftoken"].value
        valid = csrf_client.post(
            self.url,
            {
                "csrfmiddlewaretoken": token,
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "0",
                "entry-0-key": "events",
                "entry-0-label": "CSRF accepted",
                "entry-0-target": "events",
                "entry-0-position": "1",
                "entry-0-visible": "true",
                **{
                    key: value
                    for index, entry in enumerate(entries[1:], start=1)
                    for key, value in {
                        f"entry-{index}-key": entry["key"],
                        f"entry-{index}-label": entry["label"],
                        f"entry-{index}-target": entry["target"],
                        f"entry-{index}-position": str(entry["position"]),
                        f"entry-{index}-visible": "true",
                    }.items()
                },
            },
        )
        self.assert_private(valid, 302)

        self.assert_private(self.client.put(self.url), 405)
        anonymous = Client().get(self.url)
        self.assert_private(anonymous, 302)

        write_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="change_site_navigation",
        )
        Group.objects.get(name="site_admin").permissions.remove(write_permission)
        removed = self.post_menu(
            self.client,
            [{**entries[0], "label": "Must not apply"}, *entries[1:]],
            revision=1,
        )
        self.assert_private(removed, 403)
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "CSRF accepted")
