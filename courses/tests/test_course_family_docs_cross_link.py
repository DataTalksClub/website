from django.test import TestCase
from django.urls import reverse

from courses.models import Course
from courses.views.course_page_context import family_docs_path
from test_support.course_catalog import make_cohort


class FamilyDocsCrossLinkTests(TestCase):
    """§4.1: `Course.docs_url` is real data that the family page never rendered.

    Wiring it up is one relative on-site link, shown only when the stored
    URL still resolves to a real synced docs page.
    """

    def setUp(self):
        self.family = Course.objects.create(
            slug="ai-dev-tools-zoomcamp",
            title="AI Dev Tools Zoomcamp",
            outcome="Build and ship an AI-native application.",
            docs_url="https://datatalks.club/docs/courses/ai-dev-tools-zoomcamp/",
        )
        make_cohort(self.family, 2026, project_count=1)
        self.url = reverse("course_family", args=[self.family.slug])

    def test_family_docs_path_resolves_a_real_synced_docs_page(self):
        self.assertEqual(family_docs_path(self.family), "/docs/courses/ai-dev-tools-zoomcamp/")

    def test_family_page_renders_a_relative_same_site_docs_link(self):
        response = self.client.get(self.url)

        self.assertContains(
            response,
            '<a class="band-link" href="/docs/courses/ai-dev-tools-zoomcamp/">'
            "Course docs →</a>",
            html=True,
        )
        # Never an absolute host or a new tab for a same-site destination.
        self.assertNotContains(response, "https://datatalks.club/docs/courses")

    def test_family_with_no_docs_url_renders_no_docs_link(self):
        empty_family = Course.objects.create(slug="ml-zoomcamp", title="Machine Learning Zoomcamp")
        make_cohort(empty_family, 2026, project_count=1)

        self.assertEqual(family_docs_path(empty_family), "")
        response = self.client.get(reverse("course_family", args=[empty_family.slug]))
        self.assertNotContains(response, "Course docs →")

    def test_a_stale_docs_url_that_no_longer_resolves_renders_no_docs_link(self):
        stale_family = Course.objects.create(
            slug="stale-docs-course",
            title="Stale Docs Course",
            docs_url="https://datatalks.club/docs/courses/does-not-exist/",
        )
        make_cohort(stale_family, 2026, project_count=1)

        self.assertEqual(family_docs_path(stale_family), "")
        response = self.client.get(reverse("course_family", args=[stale_family.slug]))
        self.assertNotContains(response, "Course docs →")
