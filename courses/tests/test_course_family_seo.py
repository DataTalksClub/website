from django.test import TestCase
from django.urls import reverse

from courses.models import Course
from test_support.course_catalog import make_cohort


class CourseFamilySeoTests(TestCase):
    """F6: `course_family.html` publishes a canonical link and Open Graph/Twitter
    metadata like every other indexable page, instead of only a meta description.
    """

    def setUp(self):
        self.family = Course.objects.create(
            slug="practical-course",
            title="Practical Course",
            starting_point="You have a dataset and a question.",
            prerequisites="You can write Python and use the command line.",
            outcome="Build and publish a useful prediction service.",
            github_repo_url="https://github.com/example/practical-course",
        )
        make_cohort(self.family, 2026, project_count=1)
        self.url = reverse("course_family", args=[self.family.slug])

    def test_family_page_publishes_a_canonical_link(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{self.url}">',
            count=1,
        )

    def test_family_page_publishes_open_graph_and_twitter_metadata(self):
        response = self.client.get(self.url)
        body = response.content.decode()

        canonical = f"https://datatalks.club{self.url}"
        self.assertIn('<meta property="og:type" content="website">', body)
        self.assertIn('<meta property="og:site_name" content="DataTalks.Club">', body)
        self.assertIn(f'<meta property="og:url" content="{canonical}">', body)
        self.assertIn('property="og:title"', body)
        self.assertIn('property="og:description"', body)
        self.assertIn('name="twitter:card" content="summary"', body)
        self.assertIn('name="twitter:site" content="@DataTalksClub"', body)
        self.assertIn('name="twitter:title"', body)
        self.assertIn('name="twitter:description"', body)

    def test_family_page_title_differentiates_from_its_article_and_docs_page(self):
        # F5: the family page, the docs course index and the marketing article can
        # all rank for the same course name; the family page's <title> says what
        # makes it different (cohorts, syllabus, registration) instead of
        # repeating the bare course name.
        response = self.client.get(self.url)

        self.assertContains(response, "Practical Course: cohorts, syllabus and registration")

    def test_family_page_description_is_never_empty(self):
        # F6: `family_lede` can fall back all the way to an empty string for a
        # family with neither a written outcome nor a real description; the
        # published meta description must never render as `content=""`.
        empty_family = Course.objects.create(slug="empty-lede-course", title="Empty Lede Course")
        make_cohort(empty_family, 2026, project_count=1)

        response = self.client.get(reverse("course_family", args=[empty_family.slug]))

        self.assertNotContains(response, 'name="description" content="">')
