from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    Enrollment,
    Project,
    ProjectState,
    ProjectSubmission,
)
from courses.views.course_leaderboard_data import (
    project_positions,
    project_star_class,
)

User = get_user_model()


class ProjectStarClassTests(TestCase):
    """`project_star_class` picks the leaderboard's color-modifier class."""

    def test_first_position_uses_the_default_color(self):
        # No modifier class: the base `.leaderboard-passed` color is the
        # palette's first color, the same convention `.step-number` uses.
        self.assertEqual(project_star_class(1), "")

    def test_second_third_and_fourth_positions_get_distinct_classes(self):
        self.assertEqual(project_star_class(2), "leaderboard-passed-2")
        self.assertEqual(project_star_class(3), "leaderboard-passed-3")
        self.assertEqual(project_star_class(4), "leaderboard-passed-4")

    def test_positions_beyond_the_palette_cycle_back_through_it(self):
        self.assertEqual(project_star_class(5), "")
        self.assertEqual(project_star_class(6), "leaderboard-passed-2")
        self.assertEqual(project_star_class(8), "leaderboard-passed-4")
        self.assertEqual(project_star_class(9), "")


class ProjectPositionsTests(TestCase):
    """`project_positions` gives every project a stable cohort position."""

    def setUp(self):
        self.course = Cohort.objects.create(
            slug="star-positions-course", title="Star Positions Course"
        )

    def create_project(self, slug, title, state=ProjectState.COMPLETED.value):
        return Project.objects.create(
            course=self.course,
            slug=slug,
            title=title,
            state=state,
            submission_due_date=timezone.now() + timedelta(days=7),
            peer_review_due_date=timezone.now() + timedelta(days=14),
        )

    def test_positions_follow_cohort_creation_order(self):
        first = self.create_project("first", "First Project")
        second = self.create_project("second", "Second Project")
        third = self.create_project("third", "Third Project")

        positions = project_positions(self.course)

        self.assertEqual(
            positions,
            {first.id: 1, second.id: 2, third.id: 3},
        )

    def test_positions_are_stable_regardless_of_project_state(self):
        # A project's position names the same project everywhere on the
        # leaderboard even while it is still collecting submissions or under
        # peer review — position is cohort order, not completion state.
        first = self.create_project("first", "First Project")
        second = self.create_project(
            "second",
            "Second Project",
            state=ProjectState.COLLECTING_SUBMISSIONS.value,
        )
        third = self.create_project("third", "Third Project")

        positions = project_positions(self.course)

        self.assertEqual(positions[first.id], 1)
        self.assertEqual(positions[second.id], 2)
        self.assertEqual(positions[third.id], 3)


class LeaderboardProjectStarColorTests(TestCase):
    """The leaderboard view colors each passed-project star by cohort position."""

    def setUp(self):
        # The leaderboard is cached under `leaderboard:<cohort id>` with an
        # hour's TTL, and a rolled-back test hands the next one in the same
        # worker the same cohort id. Without this the view can answer from a
        # previous case's rows, which is how this class failed only when the
        # full suite ran (`KeyError: 'Alice'`). Every other leaderboard suite
        # clears the cache the same way.
        cache.clear()
        self.addCleanup(cache.clear)
        self.course = Cohort.objects.create(slug="star-colors-course", title="Star Colors Course")
        self.project1 = self.create_project("project-1", "Project One")
        self.project2 = self.create_project("project-2", "Project Two")
        self.project3 = self.create_project("project-3", "Project Three")

        self.alice = User.objects.create_user(username="alice-stars@test.com")
        self.bob = User.objects.create_user(username="bob-stars@test.com")

        self.alice_enrollment = Enrollment.objects.create(
            course=self.course,
            student=self.alice,
            display_name="Alice",
            total_score=100,
            position_on_leaderboard=1,
        )
        self.bob_enrollment = Enrollment.objects.create(
            course=self.course,
            student=self.bob,
            display_name="Bob",
            total_score=90,
            position_on_leaderboard=2,
        )

        # Alice passes all three projects, in order.
        for project in (self.project1, self.project2, self.project3):
            self.create_passed_submission(project, self.alice, self.alice_enrollment)

        # Bob skips project 1 and only passes project 2, so its color has to
        # come from the project's own cohort position, not from where it
        # falls in any one learner's own list of passed submissions.
        self.create_passed_submission(self.project2, self.bob, self.bob_enrollment)

    def create_project(self, slug, title):
        return Project.objects.create(
            course=self.course,
            slug=slug,
            title=title,
            state=ProjectState.COMPLETED.value,
            submission_due_date=timezone.now() + timedelta(days=7),
            peer_review_due_date=timezone.now() + timedelta(days=14),
        )

    def create_passed_submission(self, project, user, enrollment):
        return ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/test/repo",
            passed=True,
        )

    def leaderboard_url(self):
        return reverse(
            "cohort_leaderboard",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def passed_projects_by_slug(self, enrollment_data):
        return {project["slug"]: project for project in enrollment_data["passed_projects"]}

    def test_star_color_is_keyed_by_project_position_not_submission_order(self):
        response = self.client.get(self.leaderboard_url())

        self.assertEqual(response.status_code, 200)
        enrollments = {
            enrollment["display_name"]: enrollment for enrollment in response.context["enrollments"]
        }

        alice_projects = self.passed_projects_by_slug(enrollments["Alice"])
        bob_projects = self.passed_projects_by_slug(enrollments["Bob"])

        self.assertEqual(alice_projects["project-1"]["star_class"], "")
        self.assertEqual(alice_projects["project-2"]["star_class"], "leaderboard-passed-2")
        self.assertEqual(alice_projects["project-3"]["star_class"], "leaderboard-passed-3")
        # Bob only passed project 2, but it still carries project 2's color —
        # the same color Alice's project-2 star carries above.
        self.assertEqual(bob_projects["project-2"]["star_class"], "leaderboard-passed-2")
        self.assertNotIn("project-1", bob_projects)
        self.assertNotIn("project-3", bob_projects)

    def test_leaderboard_renders_the_color_modifier_classes(self):
        response = self.client.get(self.leaderboard_url())
        content = response.content.decode()

        self.assertIn('class="leaderboard-passed"', content)
        self.assertIn('class="leaderboard-passed leaderboard-passed-2"', content)
        self.assertIn('class="leaderboard-passed leaderboard-passed-3"', content)

    def test_star_accessibility_markers_are_preserved(self):
        response = self.client.get(self.leaderboard_url())

        self.assertContains(response, 'title="Passed Project One"')
        self.assertContains(response, 'title="Passed Project Two"')
        self.assertContains(response, 'title="Passed Project Three"')
        self.assertContains(response, '<span class="sr-only">Passed Project One</span>')
