from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.core.paginator import Paginator
from django.db import connection
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.accessibility_registry import template_readability_issues
from courses.models import (
    Cohort,
    Course,
    Enrollment,
    Project,
    ProjectState,
    ProjectSubmission,
    ProjectVote,
    User,
)


class SiteProjectGalleryTestBase(TestCase):
    due: datetime
    de_family: Course
    de_2023: Cohort
    de_2024: Cohort
    de_project_2023: Project
    de_project_2024: Project
    ml_family: Course
    ml_2025: Cohort
    ml_project_2025: Project
    empty_family: Course
    hidden_family: Course
    hidden_family_cohort: Cohort
    hidden_family_project: Project
    hidden_cohort: Cohort
    hidden_cohort_project: Project
    submission_de_2023: ProjectSubmission
    submission_de_2024: ProjectSubmission
    submission_ml_2025: ProjectSubmission
    volunteer_only_submission: ProjectSubmission
    hidden_family_submission: ProjectSubmission
    hidden_cohort_submission: ProjectSubmission

    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timedelta(days=7)

        cls.de_family = Course.objects.create(slug="de-zoomcamp", title="Data Engineering Zoomcamp")
        cls.de_2023 = cls._cohort(cls.de_family, 2023)
        cls.de_2024 = cls._cohort(cls.de_family, 2024)
        cls.de_project_2023 = cls._project(cls.de_2023, "pipeline-2023")
        cls.de_project_2024 = cls._project(cls.de_2024, "pipeline-2024")

        cls.ml_family = Course.objects.create(slug="ml-zoomcamp", title="ML Zoomcamp")
        cls.ml_2025 = cls._cohort(cls.ml_family, 2025)
        cls.ml_project_2025 = cls._project(cls.ml_2025, "capstone-2025")

        cls.empty_family = Course.objects.create(slug="empty-zoomcamp", title="Empty Zoomcamp")
        cls._cohort(cls.empty_family, 2025)

        cls.hidden_family = Course.objects.create(
            slug="secret-zoomcamp", title="Secret Zoomcamp", visible=False
        )
        cls.hidden_family_cohort = cls._cohort(cls.hidden_family, 2025)
        cls.hidden_family_project = cls._project(cls.hidden_family_cohort, "hidden-family-capstone")

        cls.hidden_cohort = cls._cohort(cls.ml_family, 2026, visible=False)
        cls.hidden_cohort_project = cls._project(cls.hidden_cohort, "hidden-capstone")

        cls.submission_de_2024 = cls._submission(
            cls.de_project_2024, cls.de_2024, "https://github.com/example/pipeline-2024"
        )
        cls.submission_de_2023 = cls._submission(
            cls.de_project_2023, cls.de_2023, "https://github.com/example/pipeline-2023"
        )
        cls.submission_ml_2025 = cls._submission(
            cls.ml_project_2025, cls.ml_2025, "https://github.com/example/capstone-2025"
        )
        cls.volunteer_only_submission = cls._submission(
            cls.ml_project_2025,
            cls.ml_2025,
            "https://github.com/example/volunteer",
            volunteer_review_only=True,
        )
        cls.hidden_family_submission = cls._submission(
            cls.hidden_family_project,
            cls.hidden_family_cohort,
            "https://github.com/example/hidden-family",
        )
        cls.hidden_cohort_submission = cls._submission(
            cls.hidden_cohort_project,
            cls.hidden_cohort,
            "https://github.com/example/hidden-cohort",
        )

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

    @classmethod
    def _submission(cls, project, cohort, github_link, **kwargs):
        user = User.objects.create_user(
            username=f"learner-{ProjectSubmission.objects.count()}-{project.slug}",
            email=f"learner-{ProjectSubmission.objects.count()}-{project.slug}@example.com",
            password="x",
        )
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        return ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link=github_link,
            **kwargs,
        )

    def gallery_url(self):
        return reverse("all_projects")


class SiteProjectGallerySubmissionListTests(SiteProjectGalleryTestBase):
    def test_route_renders_the_gallery_template(self):
        response = self.client.get(self.gallery_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/site_gallery.html")

    def test_retired_legacy_listing_routes_have_no_compatibility_names(self):
        for route_name in ("list_all_project_submissions", "project_list"):
            with self.subTest(route_name=route_name), self.assertRaises(NoReverseMatch):
                reverse(route_name, args=["legacy"])

    def test_lists_individual_submissions_newest_cohort_first(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertEqual(
            submission_ids,
            [
                self.submission_ml_2025.id,
                self.submission_de_2024.id,
                self.submission_de_2023.id,
            ],
        )

    def test_each_submission_is_tagged_with_its_own_family_and_cohort(self):
        response = self.client.get(self.gallery_url())

        tags_by_id = {
            submission.id: (submission.family.slug, submission.cohort.identifier)
            for submission in response.context["submissions"]
        }

        self.assertEqual(tags_by_id[self.submission_ml_2025.id], ("ml-zoomcamp", "2025"))
        self.assertEqual(tags_by_id[self.submission_de_2024.id], ("de-zoomcamp", "2024"))
        self.assertContains(response, "ML Zoomcamp")
        self.assertContains(response, "Data Engineering Zoomcamp")

    def test_excludes_a_family_with_no_submissions_anywhere(self):
        response = self.client.get(self.gallery_url())
        families = {submission.family.slug for submission in response.context["submissions"]}

        self.assertNotIn("empty-zoomcamp", families)

    def test_excludes_submissions_from_a_hidden_family(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.hidden_family_submission.id, submission_ids)
        self.assertNotContains(response, "hidden-family")

    def test_excludes_submissions_from_a_hidden_cohort(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.hidden_cohort_submission.id, submission_ids)
        self.assertNotContains(response, "hidden-cohort")

    def test_excludes_volunteer_review_only_submissions(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.volunteer_only_submission.id, submission_ids)
        self.assertNotContains(response, "volunteer")


class SiteProjectGalleryLinkTests(SiteProjectGalleryTestBase):
    def test_links_to_the_submitters_leaderboard_breakdown(self):
        response = self.client.get(self.gallery_url())
        breakdown_url = reverse(
            "cohort_leaderboard_score_breakdown",
            kwargs={
                "course_slug": "ml-zoomcamp",
                "cohort_identifier": "2025",
                "enrollment_id": self.submission_ml_2025.enrollment_id,
            },
        )

        self.assertContains(response, breakdown_url)

    def test_courses_list_page_links_here(self):
        response = self.client.get(reverse("course_list"))

        self.assertContains(response, self.gallery_url())


class SiteProjectGalleryPaginationTests(SiteProjectGalleryTestBase):
    def test_paginates_submissions_like_the_family_gallery(self):
        bulk_family = Course.objects.create(slug="bulk-zoomcamp", title="Bulk Zoomcamp")
        bulk_cohort = self._cohort(bulk_family, 2030)
        bulk_project = self._project(bulk_cohort, "capstone-2030")
        for index in range(30):
            self._submission(
                bulk_project,
                bulk_cohort,
                f"https://github.com/example/bulk-{index}",
            )

        response = self.client.get(self.gallery_url())

        self.assertEqual(len(response.context["submissions"]), 25)
        self.assertGreaterEqual(response.context["submissions_page"].paginator.count, 33)
        self.assertContains(response, "Project submission pages")


class SiteProjectGalleryEmptyCaseTests(TestCase):
    def test_no_submissions_anywhere_shows_the_empty_state(self):
        Course.objects.create(slug="quiet-zoomcamp", title="Quiet Zoomcamp")

        response = self.client.get(reverse("all_projects"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["submissions"]), [])
        self.assertContains(response, "No project submissions yet")


class SiteProjectGalleryDiscoveryTests(SiteProjectGalleryTestBase):
    def test_facets_and_totals_use_only_public_submissions_without_vote_join_duplicates(self):
        for index in range(2):
            voter = User.objects.create_user(username=f"voter-{index}")
            ProjectVote.objects.create(submission=self.submission_ml_2025, voter=voter)

        response = self.client.get(self.gallery_url())
        filters = response.context["gallery_filters"]

        self.assertEqual(response.context["gallery_total"], 3)
        self.assertEqual(response.context["gallery_course_count"], 2)
        self.assertEqual(
            list(filters.fields["course"].choices),
            [
                ("", "All courses"),
                ("de-zoomcamp", "Data Engineering Zoomcamp"),
                ("ml-zoomcamp", "ML Zoomcamp"),
            ],
        )
        self.assertEqual(
            list(filters.fields["cohort"].choices),
            [
                ("", "All cohorts"),
                (str(self.ml_2025.pk), "ML Zoomcamp · 2025"),
                (str(self.de_2024.pk), "Data Engineering Zoomcamp · 2024"),
                (str(self.de_2023.pk), "Data Engineering Zoomcamp · 2023"),
            ],
        )
        self.assertEqual(len(filters.fields["project"].choices), 4)

    def test_course_cohort_and_assignment_filters_combine(self):
        response = self.client.get(
            self.gallery_url(),
            {
                "course": "de-zoomcamp",
                "cohort": str(self.de_2024.pk),
                "project": str(self.de_project_2024.pk),
            },
        )

        self.assertEqual(
            [row.id for row in response.context["submissions"]], [self.submission_de_2024.id]
        )
        self.assertContains(response, '<option value="de-zoomcamp" selected>')
        self.assertContains(response, f'<option value="{self.de_2024.pk}" selected>')
        self.assertContains(response, f'<option value="{self.de_project_2024.pk}" selected>')
        self.assertContains(response, "Clear filters")

    def test_free_text_repository_search_is_removed(self):
        response = self.client.get(self.gallery_url())

        self.assertNotIn("q", response.context["gallery_filters"].fields)
        self.assertNotContains(response, "Repository or assignment")

    def test_invalid_filters_do_not_widen_results_or_reveal_hidden_facet_names(self):
        for query in (
            {"course": "secret-zoomcamp"},
            {"course": "unknown"},
            {"cohort": "999999"},
            {"cohort": "not-a-cohort"},
            {"project": "999999"},
            {"sort": "score"},
        ):
            with self.subTest(query=query):
                response = self.client.get(self.gallery_url(), query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context["submissions"]), [])
                self.assertTrue(response.context["gallery_filters"].errors)
                self.assertContains(response, 'role="alert"')
                self.assertNotContains(response, "Secret Zoomcamp")

    def test_recent_and_most_voted_orders_are_explicit_and_stable(self):
        ProjectSubmission.objects.filter(pk=self.submission_de_2023.pk).update(
            submitted_at=timezone.now() + timedelta(days=1)
        )
        voter = User.objects.create_user(username="discovery-voter")
        ProjectVote.objects.create(submission=self.submission_de_2024, voter=voter)

        recent = self.client.get(self.gallery_url(), {"sort": "recent"})
        votes = self.client.get(self.gallery_url(), {"sort": "votes"})

        self.assertEqual(recent.context["submissions"][0].pk, self.submission_de_2023.pk)
        self.assertEqual(votes.context["submissions"][0].pk, self.submission_de_2024.pk)
        self.assertEqual(votes.context["submissions"][1].pk, self.submission_de_2023.pk)

    def test_default_order_has_a_submission_identity_tiebreaker(self):
        other = self._submission(
            self.ml_project_2025, self.ml_2025, "https://github.com/example/another-capstone"
        )
        ProjectSubmission.objects.filter(pk=other.pk).update(
            submitted_at=self.submission_ml_2025.submitted_at
        )

        response = self.client.get(self.gallery_url(), {"course": "ml-zoomcamp"})

        self.assertEqual(
            [row.pk for row in response.context["submissions"]],
            [self.submission_ml_2025.pk, other.pk],
        )

    def test_repository_address_is_not_an_invented_title_and_original_destination_survives(self):
        destination = "https://github.com/example/repository/tree/main/capstone#readme"
        self.submission_ml_2025.github_link = destination
        self.submission_ml_2025.save(update_fields=["github_link"])

        response = self.client.get(self.gallery_url())

        self.assertContains(response, "github.com/example/repository/tree/main/capstone")
        self.assertContains(
            response, f'href="{destination}" target="_blank" rel="noopener noreferrer"'
        )
        self.assertContains(response, "Submitted repository")
        self.assertContains(response, reverse("cohort", args=["ml-zoomcamp", "2025"]))

    def test_missing_or_unsafe_repository_link_is_not_clickable(self):
        for destination in (
            "",
            "javascript:alert(1)",
            "https://secret:password@example.com/repo",
            "http://[invalid",
        ):
            with self.subTest(destination=destination):
                self.submission_ml_2025.github_link = destination
                self.submission_ml_2025.save(update_fields=["github_link"])

                response = self.client.get(self.gallery_url())

                self.assertContains(response, "Repository link unavailable")
                self.assertNotContains(response, 'href="javascript:')
                self.assertNotContains(response, "secret:password")

    def test_ungraded_and_completed_project_states_remain_honest(self):
        self.submission_ml_2025.project_score = 99
        self.submission_ml_2025.passed = True
        self.submission_ml_2025.save(update_fields=["project_score", "passed"])
        ungraded = self.client.get(self.gallery_url(), {"course": "ml-zoomcamp"})
        self.assertContains(ungraded, "Not graded yet")
        self.assertNotContains(ungraded, "99 score")
        self.ml_project_2025.state = ProjectState.COMPLETED.value
        self.ml_project_2025.save(update_fields=["state"])
        completed = self.client.get(self.gallery_url(), {"course": "ml-zoomcamp"})
        self.assertContains(completed, "99 score")
        self.assertContains(completed, "Passed")

    def test_page_links_preserve_only_valid_gallery_filters(self):
        for index in range(27):
            self._submission(
                self.ml_project_2025, self.ml_2025, f"https://github.com/example/capstone-{index}"
            )
        response = self.client.get(
            self.gallery_url(),
            {
                "course": "ml-zoomcamp",
                "cohort": str(self.ml_2025.pk),
                "project": str(self.ml_project_2025.pk),
                "sort": "votes",
                "unrelated": "discard-me",
            },
        )
        querystring = response.context["pagination_querystring"]
        self.assertEqual(
            parse_qs(urlsplit("?page=2" + unescape(querystring)).query),
            {
                "page": ["2"],
                "course": ["ml-zoomcamp"],
                "cohort": [str(self.ml_2025.pk)],
                "project": [str(self.ml_project_2025.pk)],
                "sort": ["votes"],
            },
        )
        self.assertNotContains(response, "discard-me")
        page_two = self.client.get(self.gallery_url() + "?page=2" + querystring)
        self.assertEqual(len(page_two.context["submissions"]), 3)

    def test_card_count_does_not_add_per_submission_database_queries(self):
        self.client.get(self.gallery_url())
        with CaptureQueriesContext(connection) as small:
            self.client.get(self.gallery_url())
        for index in range(24):
            self._submission(
                self.ml_project_2025, self.ml_2025, f"https://github.com/example/project-{index}"
            )
        with CaptureQueriesContext(connection) as full:
            self.client.get(self.gallery_url())
        self.assertLessEqual(len(full), len(small))

    def test_gallery_template_keeps_the_shared_readability_contract(self):
        template = Path(__file__).resolve().parents[1] / "templates/projects/site_gallery.html"
        self.assertEqual(template_readability_issues(template.read_text()), [])


class SharedGalleryPaginationSemanticsTests(SimpleTestCase):
    def test_first_middle_and_last_pages_keep_live_links_and_disabled_semantics(self):
        paginator = Paginator(range(51), 25)
        for number in (1, 2, 3):
            with self.subTest(page=number):
                page = paginator.page(number)
                markup = render_to_string(
                    "include/pagination.html",
                    {
                        "pagination_page": page,
                        "pagination_range": paginator.page_range,
                        "pagination_querystring": "&course=example",
                        "pagination_label": "Project submission pages",
                    },
                )
                self.assertIn('aria-label="Project submission pages"', markup)
                for label, arrow, destination, relation in (
                    ("Previous page", "←", number - 1, "prev"),
                    ("Next page", "→", number + 1, "next"),
                ):
                    if destination in paginator.page_range:
                        self.assertInHTML(
                            '<a class="filter-pill pagination-step" '
                            f'href="?page={destination}&amp;course=example" rel="{relation}" '
                            f'aria-label="{label}"><span aria-hidden="true">{arrow}</span></a>',
                            markup,
                        )
                    else:
                        self.assertInHTML(
                            '<span class="status-pill pagination-step" role="link" '
                            f'aria-disabled="true" aria-label="{label}">'
                            f'<span aria-hidden="true">{arrow}</span></span>',
                            markup,
                        )
                self.assertNotIn('<span class="status-pill pagination-step" aria-label=', markup)
