from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, Project


class SiteProjectGalleryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timezone.timedelta(days=7)

        cls.de_family = Course.objects.create(
            slug="de-zoomcamp", title="Data Engineering Zoomcamp"
        )
        cls.de_2023 = cls._cohort(cls.de_family, 2023)
        cls.de_2024 = cls._cohort(cls.de_family, 2024)
        cls._project(cls.de_2023, "pipeline-2023")
        cls._project(cls.de_2024, "pipeline-2024")

        cls.ml_family = Course.objects.create(slug="ml-zoomcamp", title="ML Zoomcamp")
        cls.ml_2025 = cls._cohort(cls.ml_family, 2025)
        cls._project(cls.ml_2025, "capstone-2025")

        cls.empty_family = Course.objects.create(
            slug="empty-zoomcamp", title="Empty Zoomcamp"
        )
        cls._cohort(cls.empty_family, 2025)

    @classmethod
    def _cohort(cls, family, year, visible=True):
        return Cohort.objects.create(
            course=family,
            slug=f"{family.slug}-{year}",
            identifier=str(year),
            year=year,
            title=f"{family.title} {year}",
            description="",
            visible=visible,
        )

    @classmethod
    def _project(cls, cohort, slug):
        return Project.objects.create(
            course=cohort,
            slug=slug,
            title=slug.replace("-", " ").title(),
            submission_due_date=cls.due,
            peer_review_due_date=cls.due,
        )

    def gallery_url(self):
        return reverse("all_projects")


class SiteProjectGalleryGroupingTests(SiteProjectGalleryTestBase):
    def test_route_renders_the_gallery_template(self):
        response = self.client.get(self.gallery_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/site_gallery.html")

    def test_groups_by_family_then_by_cohort(self):
        response = self.client.get(self.gallery_url())
        family_groups = {
            group.family.slug: group for group in response.context["family_groups"]
        }

        self.assertEqual(set(family_groups), {"de-zoomcamp", "ml-zoomcamp"})
        self.assertEqual(
            [g.cohort.identifier for g in family_groups["de-zoomcamp"].cohort_groups],
            ["2024", "2023"],
        )
        self.assertEqual(
            [g.cohort.identifier for g in family_groups["ml-zoomcamp"].cohort_groups],
            ["2025"],
        )

    def test_excludes_a_family_with_no_projects_anywhere(self):
        response = self.client.get(self.gallery_url())
        family_slugs = [group.family.slug for group in response.context["family_groups"]]

        self.assertNotIn("empty-zoomcamp", family_slugs)

    def test_orders_families_by_most_recent_project_activity(self):
        response = self.client.get(self.gallery_url())
        family_slugs = [group.family.slug for group in response.context["family_groups"]]

        # ml-zoomcamp's only cohort is 2025; de-zoomcamp's newest is 2024.
        self.assertEqual(family_slugs, ["ml-zoomcamp", "de-zoomcamp"])


class SiteProjectGalleryLinkTests(SiteProjectGalleryTestBase):
    def test_links_to_each_familys_own_full_gallery(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(
            response,
            reverse("family_projects", kwargs={"course_slug": "de-zoomcamp"}),
        )
        self.assertContains(
            response,
            reverse("family_projects", kwargs={"course_slug": "ml-zoomcamp"}),
        )

    def test_links_to_each_projects_cohort_scoped_list(self):
        response = self.client.get(self.gallery_url())
        project_list_url = reverse(
            "cohort_project_list",
            kwargs={
                "course_slug": "de-zoomcamp",
                "cohort_identifier": "2024",
                "project_slug": "pipeline-2024",
            },
        )

        self.assertContains(response, project_list_url)

    def test_courses_list_page_links_here(self):
        response = self.client.get(reverse("course_list"))

        self.assertContains(response, self.gallery_url())


class SiteProjectGalleryDisclosureTests(SiteProjectGalleryTestBase):
    def test_family_folds_are_closed_by_default(self):
        response = self.client.get(self.gallery_url())

        self.assertNotContains(response, 'data-gallery-family="de-zoomcamp" open>')
        self.assertNotContains(response, 'data-gallery-family="ml-zoomcamp" open>')
        self.assertContains(response, 'data-gallery-family="de-zoomcamp">')
        self.assertContains(response, 'data-gallery-family="ml-zoomcamp">')

    def test_newest_cohorts_within_a_family_default_open(self):
        response = self.client.get(self.gallery_url())

        # de-zoomcamp holds two cohorts; both fit under the default-open count.
        self.assertContains(response, 'data-gallery-cohort="2024" open>')
        self.assertContains(response, 'data-gallery-cohort="2023" open>')


class SiteProjectGalleryEmptyCaseTests(TestCase):
    def test_no_projects_anywhere_shows_the_empty_state(self):
        Course.objects.create(slug="quiet-zoomcamp", title="Quiet Zoomcamp")

        response = self.client.get(reverse("all_projects"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["family_groups"], [])
        self.assertContains(response, "No project submissions yet")
