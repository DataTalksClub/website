"""D2.1b: every registered destination has a resolvable href."""

from community_base.studio.registry import sections
from django.test import SimpleTestCase
from django.urls import reverse


class D21bStudioRegistrationTests(SimpleTestCase):
    def test_site_owned_destinations_reverse(self) -> None:
        keys = {
            "dashboard",
            "site_settings",
            "navigation",
            "sponsors",
            "credentials",
            "audit",
            "event_identities",
            "studio_courses",
        }
        found = {}
        for section in sections():
            for destination in (
                *section.destinations,
                *(d for group in section.groups for d in group.destinations),
            ):
                if destination.key in keys:
                    found[destination.key] = reverse(destination.url_name)
        self.assertEqual(set(found), keys)
        for key, href in found.items():
            with self.subTest(key=key):
                self.assertTrue(href.startswith("/studio"), href)
                self.assertNotEqual(href, "")
