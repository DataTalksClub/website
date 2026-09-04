from __future__ import annotations

import importlib.util

from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import TestCase
from django.urls import resolve, reverse

from content.public_views import permanent_public_redirect
from website.urls import MEDIA_KIT_URL

#: Pinned separately from the module under test.  Asserting only against the
#: imported constant would pass for any value it happened to hold.
PUBLISHED_MEDIA_KIT = "https://datatalksclub.github.io/mediakit/"


class MediaKitRedirectTests(TestCase):
    """The media kit is published from DataTalksClub/mediakit, not from here.

    It used to be a page in this project, and it drifted: it advertised a course
    start month the database contradicted, listed an edition that does not
    exist, and omitted a course entirely.  That repository holds the one copy
    now, so both spellings of the path lead there.
    """

    def test_the_constant_names_the_published_media_kit(self) -> None:
        self.assertEqual(MEDIA_KIT_URL, PUBLISHED_MEDIA_KIT)

    def test_the_canonical_path_redirects_to_the_published_media_kit(self) -> None:
        self.assertEqual(reverse("media-kit"), "/mediakit/")

        response = self.client.get("/mediakit/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], MEDIA_KIT_URL)

    def test_the_slashless_path_leads_to_the_same_place(self) -> None:
        response = self.client.get("/mediakit")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], MEDIA_KIT_URL)

    def test_a_query_string_is_preserved(self) -> None:
        response = self.client.get("/mediakit/?utm_source=newsletter")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], f"{MEDIA_KIT_URL}?utm_source=newsletter")

    def test_an_unsafe_method_is_refused_rather_than_redirected(self) -> None:
        response = self.client.post("/mediakit/")

        self.assertEqual(response.status_code, 405)

    def test_no_media_kit_page_is_served_from_this_project(self) -> None:
        """The figures that drifted cannot be rendered here any more.

        The old page carried a sponsorship stat block and a course calendar that
        the database contradicted.  An empty ``response.templates`` only says
        this one request rendered nothing, so the page's absence is asserted
        structurally as well: the route dispatches to the shared redirect view,
        and neither the view module nor its template is still loadable.
        """

        match = resolve("/mediakit/")

        self.assertIs(match.func, permanent_public_redirect)
        self.assertEqual(match.kwargs, {"target": PUBLISHED_MEDIA_KIT})
        self.assertIsNone(importlib.util.find_spec("core.mediakit"))
        with self.assertRaises(TemplateDoesNotExist):
            get_template("core/mediakit.html")

        response = self.client.get("/mediakit/")

        self.assertEqual(response.templates, [])
