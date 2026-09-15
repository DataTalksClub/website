from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course
from courses.tests.course_list_base import CourseListViewTestBase


class CourseListOrderingTest(CourseListViewTestBase):
    """Courses classified into active / open registration / archive context tiers.

    These tiers stay real context data the view computes (``active_courses``,
    ``open_registration_courses``, ``finished_courses``) even though the catalogue
    itself renders them as one unified list rather than three page sections.
    """

    def create_course(self, slug, **kwargs):
        return Cohort.objects.create(
            title=kwargs.pop("title", slug),
            slug=slug,
            visible=True,
            **kwargs,
        )

    def test_courses_split_into_three_tiers(self):
        today = timezone.localdate()

        started = self.create_course(
            "running-course",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=30),
            registration_url="https://example.com/register",
        )
        upcoming = self.create_course(
            "upcoming-course",
            start_date=today + timedelta(days=14),
            end_date=today + timedelta(days=60),
            registration_url="https://example.com/register",
        )
        archived = self.create_course("archived-course", finished=True)

        response = self.course_list_response()
        active = {c.slug for c in response.context["active_courses"]}
        open_reg = {c.slug for c in response.context["open_registration_courses"]}
        finished = {c.slug for c in response.context["finished_courses"]}

        self.assertIn(started.slug, active)
        self.assertNotIn(started.slug, open_reg)

        self.assertIn(upcoming.slug, open_reg)
        self.assertNotIn(upcoming.slug, active)

        self.assertIn(archived.slug, finished)

    def test_future_course_without_registration_stays_active(self):
        today = timezone.localdate()
        future_no_reg = self.create_course(
            "future-no-registration",
            start_date=today + timedelta(days=14),
            end_date=today + timedelta(days=60),
        )

        response = self.course_list_response()
        active = {c.slug for c in response.context["active_courses"]}
        open_reg = {c.slug for c in response.context["open_registration_courses"]}

        self.assertIn(future_no_reg.slug, active)
        self.assertNotIn(future_no_reg.slug, open_reg)

    def test_featured_course_is_an_exact_homepage_catalog_cohort(self):
        shared_course = self.create_course(
            "de-zoomcamp-2026",
            title="Data Engineering Zoomcamp 2026",
        )
        self.create_course(
            "llm-zoomcamp-2026",
            title="LLM Zoomcamp 2026",
        )

        course_list_response = self.course_list_response()
        homepage_response = self.client.get(reverse("home"))
        shared_path = reverse(
            "cohort",
            kwargs={
                "course_slug": shared_course.course.slug,
                "cohort_identifier": shared_course.identifier,
            },
        )
        family_path = reverse(
            "course_family",
            kwargs={"course_slug": shared_course.course.slug},
        )

        self.assertEqual(
            course_list_response.context["featured_course"],
            shared_course,
        )
        self.assertEqual(
            course_list_response.context["active_courses"][0],
            shared_course,
        )
        self.assertContains(homepage_response, f'href="{shared_path}"')
        # The catalogue card links to the family page (the one place every card
        # links, whatever its state) rather than this specific cohort route --
        # the homepage's own catalogue still links the cohort route directly,
        # asserted above.
        self.assertContains(course_list_response, f'href="{family_path}"')

    def test_active_and_finished_cards_link_to_the_same_family_route(self):
        """Every card links the same way now: to its family page, no exceptions.

        The catalogue used to send an "active" card to its specific cohort route
        and a "finished" card to its own cohort route too, each carrying its own
        state-specific action.  The owner asked for one plain, consistent card:
        every family, whatever its state, now points at the one real place that
        always carries its accurate, current state -- the family page.
        """

        today = timezone.localdate()
        active = self.create_course(
            "active-family-2026",
            title="Active Family",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=30),
        )
        finished = self.create_course(
            "finished-family-2025",
            title="Finished Family",
            identifier="2025",
            year=2025,
            finished=True,
        )

        response = self.course_list_response()
        content = response.content.decode()
        active_card = self.course_card_html(content, active)
        active_family_url = reverse(
            "course_family",
            kwargs={"course_slug": active.course.slug},
        )
        finished_family_url = reverse(
            "course_family",
            kwargs={"course_slug": finished.course.slug},
        )

        self.assertIn(f"window.location.href='{active_family_url}'", active_card)
        self.assertIn(f'href="{active_family_url}">Active Family</a>', active_card)
        self.assertIn(f'href="{finished_family_url}">Finished Family</a>', content)

    def test_catalogue_has_one_family_card_for_multiple_cohorts(self):
        today = timezone.localdate()
        family = Course.objects.create(
            slug="catalogue-course",
            title="Catalogue Course",
            outcome="Build and operate reliable production systems.",
        )
        Cohort.objects.create(
            course=family,
            slug="catalogue-course-2025",
            identifier="2025",
            year=2025,
            title="Catalogue Course 2025",
            description="Older edition copy.",
            finished=True,
        )
        current = Cohort.objects.create(
            course=family,
            slug="catalogue-course-2026",
            identifier="2026",
            year=2026,
            title="Catalogue Course 2026",
            description="Current edition copy.",
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=30),
        )

        response = self.course_list_response()
        cards = [card for card in response.context["course_family_cards"] if card.family == family]
        content = response.content.decode()

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].cohort, current)
        self.assertEqual(cards[0].title, family.title)
        self.assertEqual(cards[0].outcome, family.outcome)
        self.assertContains(response, family.title)
        self.assertContains(response, family.outcome)
        self.assertNotContains(response, "Catalogue Course 2025")
        self.assertNotContains(response, "Catalogue Course 2026")
        self.assertIn('/courses/catalogue-course"', content)

    def test_empty_family_outcome_is_rendered_as_empty_without_boilerplate(self):
        family = Course.objects.create(
            slug="empty-outcome-course",
            title="Empty Outcome Course",
            outcome="",
        )
        Cohort.objects.create(
            course=family,
            slug="empty-outcome-course-2026",
            identifier="2026",
            year=2026,
            title="Empty Outcome Course 2026",
            description="Edition description must not replace family outcome.",
        )

        response = self.course_list_response()
        card = next(
            card for card in response.context["course_family_cards"] if card.family == family
        )

        self.assertEqual(card.outcome, "")
        self.assertNotContains(response, "Free course with practical lessons")
        self.assertNotContains(
            response,
            "Edition description must not replace family outcome.",
        )

    def test_open_registration_card_rendered_like_every_other_card(self):
        """A family with an open, dated registration gets no special card treatment.

        The catalogue used to badge this state ("registration open" chip, a
        different card background).  The owner removed all of that: the card
        looks exactly like every other family's, and only the underlying state
        computation (still real) decides where it sorts and what the family
        page itself offers.
        """

        today = timezone.localdate()
        upcoming = self.create_course(
            "upcoming-course",
            title="Upcoming Course",
            start_date=today + timedelta(days=14),
            registration_url="https://example.com/register",
        )

        response = self.course_list_response()
        content = response.content.decode()

        self.assertNotContains(response, "registration open")
        self.assertNotContains(response, "self-paced")
        family_url = reverse(
            "course_family",
            kwargs={"course_slug": upcoming.course.slug},
        )
        self.assertIn(f"window.location.href='{family_url}'", content)
        self.assertIn(f'href="{family_url}">Upcoming Course</a>', content)
