"""A member's email address is visible to admins in Studio and nowhere else.

That is the whole invariant, and these tests are the thing that keeps it true.
They exist because it was not: an anonymous visitor could read any member's
address off `/courses/wrapped/<year>/<member id>/`, and four learner-data API
endpoints handed addresses to any account's token.  Both defects were live, and
both were the kind that a passing suite does not notice, because nothing here
had ever asked "does this response contain somebody else's address?".

So that is what these ask, on the surfaces a non-admin can reach:

* the wrapped page, which is where it went wrong, from every viewer angle;
* the shared page shell, whose account menu renders an address and which a view
  could aim at the wrong person by putting a member under the `user` key;
* every token-authenticated API route, driven from the URLconf rather than from
  a list written by hand, so a new route joins this test by existing;
* a walk of the public and member-facing pages, checked against a victim
  member's address and username.

The victim's username is checked alongside their address on purpose: every
script-created account sets `username == email`, so leaking the username is
leaking the address.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import get_resolver, reverse
from django.utils import timezone

from accounts.models import Token
from courses.models import (
    Cohort,
    Course,
    Enrollment,
    Homework,
    Project,
    ProjectSubmission,
    Question,
    RegistrationCampaign,
    Submission,
    UserWrappedStatistics,
    WrappedStatistics,
)
from courses.models.homework import HomeworkState, QuestionTypes
from courses.models.project import ProjectState

# The API schema document describes the operator API; it answers with route
# shapes, never with anyone's records, so a member's token may read it.
SCHEMA_ONLY_TOKEN_ROUTES = frozenset({"api_openapi_json"})

# Course, homework, project and campaign reads are catalogue metadata — titles,
# slugs, dates, deadlines, counts.  They carry no member record and no answer
# key, and the platform deliberately gates their writes rather than their
# reads, so a member's token may read them.  They are named here rather than
# quietly skipped, and the test below still holds them to carrying no address:
# an exception has to keep earning itself.
CATALOGUE_READ_TOKEN_ROUTES = frozenset(
    {
        "api_courses_list",
        "api_course_detail",
        "api_homeworks",
        "api_homework_detail",
        "api_homework_detail_by_slug",
        "api_projects",
        "api_project_detail",
        "api_project_detail_by_slug",
        "api_registration_campaigns",
        "api_registration_campaign_detail",
    }
)

VICTIM_EMAIL = "victim.member@example.invalid"
VICTIM_USERNAME = VICTIM_EMAIL

User = get_user_model()


def _member(email: str, **extra):
    """An account whose username is its address, as scripts create them."""

    return User.objects.create_user(username=email, email=email, password="invariant-test", **extra)


class WrappedPageDisclosureTests(TestCase):
    """One member's Wrapped is for that member and for staff."""

    def setUp(self) -> None:
        self.victim = _member(VICTIM_EMAIL)
        self.other = _member("other.member@example.invalid")
        self.staff = _member("staff.member@example.invalid", is_staff=True)

        self.cohort = Cohort.objects.create(slug="wrapped-course", title="Wrapped Course")
        self.wrapped = WrappedStatistics.objects.create(
            year=2025,
            is_visible=True,
            total_participants=1,
            total_enrollments=1,
            total_hours=1.0,
            total_certificates=0,
            total_points=10,
            course_stats=[],
            leaderboard=[],
        )
        # The stored display name is the account's address, which is what the
        # generator used to write for a member with no enrollment.  Rows like
        # this exist already, so the page has to clean them at render time.
        self.user_wrapped = UserWrappedStatistics.objects.create(
            wrapped=self.wrapped,
            user=self.victim,
            total_points=10,
            total_hours=1.0,
            homework_count=1,
            project_count=0,
            peer_reviews_given=0,
            learning_in_public_count=0,
            faq_contributions_count=0,
            certificates_earned=0,
            courses=[],
            rank=1,
            display_name=VICTIM_EMAIL,
        )
        self.url = reverse("user_wrapped", args=[2025, self.victim.id])

    def assert_no_victim_identity(self, response) -> None:
        body = response.content.decode()
        self.assertNotIn(VICTIM_EMAIL, body)
        self.assertNotIn(VICTIM_USERNAME, body)

    def test_anonymous_visitor_cannot_read_another_members_wrapped(self) -> None:
        response = self.client.get(self.url)

        self.assertNotEqual(response.status_code, 200)
        self.assert_no_victim_identity(response)

    def test_signed_in_member_cannot_read_another_members_wrapped(self) -> None:
        self.client.force_login(self.other)

        response = self.client.get(self.url)

        # A 404 rather than a 403: `student_id` is the sequential account
        # primary key, so a distinguishable denial would confirm which ids are
        # real.  A missing member and a member who is not you look the same.
        self.assertEqual(response.status_code, 404)
        self.assert_no_victim_identity(response)

    def test_a_missing_member_and_a_forbidden_member_look_the_same(self) -> None:
        self.client.force_login(self.other)
        missing = reverse("user_wrapped", args=[2025, self.victim.id + 10_000])

        forbidden_response = self.client.get(self.url)
        missing_response = self.client.get(missing)

        self.assertEqual(forbidden_response.status_code, missing_response.status_code)

    def test_owner_reads_their_own_wrapped_without_seeing_an_address(self) -> None:
        self.client.force_login(self.victim)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        # The owner's own account menu is allowed to show their address; the
        # page's own identity is not, so the stored address-shaped display name
        # must not have reached the heading or the social-card meta tags.
        body = response.content.decode()
        head = body.split("</head>", 1)[0]
        self.assertNotIn(VICTIM_EMAIL, head)
        self.assertNotIn(VICTIM_EMAIL, response.context["display_name"])
        self.assertNotIn("viewed_user", response.context)

    def test_staff_reads_a_members_wrapped(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assert_no_victim_identity(response)

    def test_the_page_is_never_stored_by_a_shared_cache(self) -> None:
        self.client.force_login(self.victim)

        response = self.client.get(self.url)

        cache_control = response.headers.get("Cache-Control", "")
        self.assertIn("private", cache_control)
        self.assertIn("no-store", cache_control)

    def test_the_public_wrapped_page_links_only_the_viewers_own_row(self) -> None:
        # The year page lists every member on the leaderboard.  Linking each
        # row to that member's Wrapped published a walkable index of member
        # ids, and every link but the viewer's own now leads to a 404 anyway.
        self.wrapped.leaderboard = [
            {
                "student_id": self.victim.id,
                "display_name": "Quiet Otter",
                "rank": 1,
                "total_score": 10,
            },
            {
                "student_id": self.other.id,
                "display_name": "Bright Heron",
                "rank": 2,
                "total_score": 5,
            },
        ]
        self.wrapped.save(update_fields=["leaderboard"])
        victim_link = reverse("user_wrapped", args=[2025, self.victim.id])
        other_link = reverse("user_wrapped", args=[2025, self.other.id])

        self.client.force_login(self.other)
        body = self.client.get(reverse("wrapped", args=[2025])).content.decode()

        self.assertIn(f'href="{other_link}"', body)
        self.assertNotIn(f'href="{victim_link}"', body)
        # The other member is still on the leaderboard, just not linked.
        self.assertIn("Quiet Otter", body)

    def test_an_anonymous_visitor_gets_no_wrapped_links_at_all(self) -> None:
        self.wrapped.leaderboard = [
            {
                "student_id": self.victim.id,
                "display_name": "Quiet Otter",
                "rank": 1,
                "total_score": 10,
            }
        ]
        self.wrapped.save(update_fields=["leaderboard"])

        body = self.client.get(reverse("wrapped", args=[2025])).content.decode()

        self.assertNotIn("/courses/wrapped/2025/", body)
        self.assertIn("Quiet Otter", body)

    def test_an_enrollment_pseudonym_is_the_name_the_page_shows(self) -> None:
        Enrollment.objects.create(
            student=self.victim, course=self.cohort, display_name="Quiet Otter"
        )
        self.user_wrapped.display_name = ""
        self.user_wrapped.save(update_fields=["display_name"])
        self.client.force_login(self.victim)

        response = self.client.get(self.url)

        self.assertEqual(response.context["display_name"], "Quiet Otter")


class TokenAuthenticatedApiDisclosureTests(TestCase):
    """Every token-authenticated API route is an operator route.

    The routes are read out of the URLconf rather than listed here, so a new
    token-authenticated endpoint is covered the moment it is added.  A member's
    token proving nothing but "this is an account" was how learner addresses
    became readable, and a hand-written list would not have caught a fifth
    endpoint after the audit named four.

    Every object the routes address is seeded under the placeholder identifiers
    the paths use, so each request reaches its authorization check instead of
    stopping at a 404.  A missing check is invisible behind a missing object.
    """

    SLUG = "placeholder"

    def setUp(self) -> None:
        self.member = _member(VICTIM_EMAIL)
        self.member_token = Token.objects.create(user=self.member)
        self._seed_addressed_objects()

    def _seed_addressed_objects(self) -> None:
        self.course = Cohort.objects.create(
            slug=self.SLUG, title="Placeholder", description="Placeholder"
        )
        self.homework = Homework.objects.create(
            course=self.course,
            title="Placeholder homework",
            slug=self.SLUG,
            description="Placeholder",
            instructions_url="https://github.com/DataTalksClub/test/blob/main/hw.md",
            due_date=timezone.now(),
            state=HomeworkState.CLOSED.value,
        )
        self.question = Question.objects.create(
            homework=self.homework,
            text="Placeholder question",
            question_type=QuestionTypes.FREE_FORM.value,
            scores_for_correct_answer=1,
        )
        self.project = Project.objects.create(
            course=self.course,
            title="Placeholder project",
            slug=self.SLUG,
            submission_due_date=timezone.now(),
            peer_review_due_date=timezone.now(),
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug=self.SLUG, title="Placeholder", current_course=self.course
        )
        # The graduates and export routes read a member's own submissions, so
        # there is a real learner record behind the address being protected.
        self.enrollment = Enrollment.objects.create(
            student=self.member, course=self.course, display_name="Quiet Otter"
        )

    def token_authenticated_routes(self) -> list:
        resolver = get_resolver()
        routes = []
        for pattern in resolver.url_patterns:
            nested = getattr(pattern, "url_patterns", None)
            if nested is None:
                continue
            for entry in nested:
                callback = getattr(entry, "callback", None)
                if getattr(callback, "requires_token_auth", False):
                    routes.append(entry)
        return routes

    def example_path(self, route) -> str:
        """A path for one route, addressing the objects seeded above."""

        kwargs = {}
        for name, converter in route.pattern.converters.items():
            numeric = converter.regex == "[0-9]+"
            if name.endswith("_id"):
                kwargs[name] = self._seeded_id(name) if numeric else self.SLUG
            else:
                kwargs[name] = 1 if numeric else self.SLUG
        return reverse(route.name, kwargs=kwargs)

    def _seeded_id(self, name: str) -> int:
        seeded = {
            "homework_id": self.homework.pk,
            "question_id": self.question.pk,
            "project_id": self.project.pk,
        }
        return seeded.get(name, 1)

    def test_the_urlconf_still_has_token_authenticated_routes(self) -> None:
        # Guards the discovery above: an empty sweep must fail loudly rather
        # than pass by finding nothing.
        self.assertGreater(len(self.token_authenticated_routes()), 10)

    def _request(self, method: str, path: str, token: str = ""):
        # `generic` for every method, including GET: `Client.get` reads its
        # second positional argument as a query dict, and these requests carry
        # only a token header.
        headers = {"Authorization": f"Token {token}"} if token else None
        body = "" if method == "get" else "{}"
        return self.client.generic(
            method.upper(),
            path,
            data=body,
            content_type="application/json",
            headers=headers,
        )

    def test_no_member_token_is_accepted_by_a_learner_data_route(self) -> None:
        for route in self.token_authenticated_routes():
            if route.name in SCHEMA_ONLY_TOKEN_ROUTES:
                continue
            catalogue_read = route.name in CATALOGUE_READ_TOKEN_ROUTES
            path = self.example_path(route)
            for method in ("get", "post", "patch", "put", "delete"):
                with self.subTest(route=route.name, method=method):
                    response = self._request(method, path, token=self.member_token.key)
                    if response.status_code == 405:
                        continue
                    if catalogue_read and method == "get":
                        # The named exception, still held to the invariant.
                        self.assertNotIn(VICTIM_EMAIL, response.content.decode())
                        continue
                    self.assertEqual(
                        response.status_code,
                        403,
                        f"{method.upper()} {route.name} accepted a non-staff token",
                    )
                    self.assertNotIn(VICTIM_EMAIL, response.content.decode())

    def test_every_named_exception_is_a_route_that_still_exists(self) -> None:
        # An exception list that outlives its routes silently stops meaning
        # anything.  Every name in it has to be a live token-authenticated
        # route.
        live = {route.name for route in self.token_authenticated_routes()}

        self.assertLessEqual(SCHEMA_ONLY_TOKEN_ROUTES, live)
        self.assertLessEqual(CATALOGUE_READ_TOKEN_ROUTES, live)

    def test_the_schema_route_still_discloses_no_member_data(self) -> None:
        # The one token-authenticated route that is not staff-only describes
        # the API rather than answering with anyone's records.  It is named
        # here rather than skipped silently, and it still has to be free of a
        # member's address for the exception to hold.
        for name in SCHEMA_ONLY_TOKEN_ROUTES:
            with self.subTest(route=name):
                response = self._request("get", reverse(name), token=self.member_token.key)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(VICTIM_EMAIL, response.content.decode())

    def test_a_missing_token_is_rejected_before_anything_else(self) -> None:
        for route in self.token_authenticated_routes():
            path = self.example_path(route)
            for method in ("get", "post", "patch", "put", "delete"):
                with self.subTest(route=route.name, method=method):
                    response = self._request(method, path)
                    if response.status_code == 405:
                        continue
                    self.assertEqual(response.status_code, 401)


class MemberFacingPageWalkTests(TestCase):
    """No page a non-admin can reach carries another member's address.

    The wrapped page is fixed above; this is the sweep that says the rest of
    the site does not have the same defect somewhere else.  One member is
    seeded with the records that make them appear on a learner surface — an
    enrollment, a homework submission, a project submission, a leaderboard
    position and a Wrapped row — and every course-platform page that renders
    those records is fetched twice: once by nobody, and once by a different
    signed-in member.  Neither response may contain the victim's address or
    their username, which for a script-created account is the same string.
    """

    def setUp(self) -> None:
        self.victim = _member(VICTIM_EMAIL)
        self.observer = _member("observer.member@example.invalid")
        self._seed_member_records()

    def _seed_member_records(self) -> None:
        self.course = Course.objects.create(slug="sweep-course", title="Sweep Course")
        self.cohort = Cohort.objects.create(
            slug="sweep-course-2026",
            course=self.course,
            identifier="2026",
            year=2026,
            title="Sweep Course 2026",
            description="Sweep",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.victim,
            course=self.cohort,
            display_name="Quiet Otter",
            display_on_leaderboard=True,
            position_on_leaderboard=1,
            total_score=10,
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="sweep-homework",
            title="Sweep homework",
            description="Sweep",
            due_date=timezone.now() - timedelta(days=1),
            state=HomeworkState.SCORED.value,
        )
        Submission.objects.create(
            homework=self.homework,
            student=self.victim,
            enrollment=self.enrollment,
            total_score=10,
        )
        self.project = Project.objects.create(
            course=self.cohort,
            slug="sweep-project",
            title="Sweep project",
            state=ProjectState.COMPLETED.value,
            submission_due_date=timezone.now() - timedelta(days=2),
            peer_review_due_date=timezone.now() - timedelta(days=1),
        )
        ProjectSubmission.objects.create(
            project=self.project,
            student=self.victim,
            enrollment=self.enrollment,
            github_link="https://github.com/example/sweep",
            total_score=10,
            passed=True,
        )
        wrapped = WrappedStatistics.objects.create(
            year=2026,
            is_visible=True,
            total_participants=1,
            total_enrollments=1,
            total_hours=1.0,
            total_certificates=1,
            total_points=10,
            course_stats=[],
            leaderboard=[
                {
                    "student_id": self.victim.id,
                    "display_name": "Quiet Otter",
                    "rank": 1,
                    "total_score": 10,
                }
            ],
        )
        UserWrappedStatistics.objects.create(
            wrapped=wrapped,
            user=self.victim,
            total_points=10,
            total_hours=1.0,
            homework_count=1,
            project_count=1,
            peer_reviews_given=0,
            learning_in_public_count=0,
            faq_contributions_count=0,
            certificates_earned=1,
            courses=[],
            rank=1,
            display_name="Quiet Otter",
        )

    def tearDown(self) -> None:
        # The walk fetches the cached leaderboard export, which is keyed by
        # cohort primary key.  Leaving that entry warm would let this test
        # answer a later one's request for a different cohort with the same id.
        cache.clear()

    def member_facing_paths(self) -> list[str]:
        route_kwargs = {"course_slug": self.course.slug, "cohort_year": "2026"}
        return [
            reverse("home"),
            reverse("course_list"),
            reverse("course", kwargs=route_kwargs),
            reverse("leaderboard", kwargs=route_kwargs),
            reverse("dashboard", kwargs=route_kwargs),
            reverse(
                "leaderboard_score_breakdown",
                kwargs={**route_kwargs, "enrollment_id": self.enrollment.id},
            ),
            reverse("homework", kwargs={**route_kwargs, "homework_slug": self.homework.slug}),
            reverse(
                "homework_statistics",
                kwargs={**route_kwargs, "homework_slug": self.homework.slug},
            ),
            reverse("project", kwargs={**route_kwargs, "project_slug": self.project.slug}),
            reverse("project_list", kwargs={**route_kwargs, "project_slug": self.project.slug}),
            reverse("list_all_project_submissions", kwargs=route_kwargs),
            reverse(
                "project_statistics",
                kwargs={**route_kwargs, "project_slug": self.project.slug},
            ),
            reverse("wrapped", args=[2026]),
            reverse("user_wrapped", args=[2026, self.victim.id]),
            # The year the member has no Wrapped row for.  That branch built
            # its own context, and is where the shell was handed the viewed
            # member under the `user` key, so the walk has to cover it.
            reverse("user_wrapped", args=[2025, self.victim.id]),
            f"/api/courses/{self.cohort.slug}/leaderboard.yaml",
        ]

    def assert_walk_is_clean(self, label: str) -> None:
        for path in self.member_facing_paths():
            with self.subTest(viewer=label, path=path):
                response = self.client.get(path, follow=True)
                body = response.content.decode(errors="replace")
                self.assertNotIn(VICTIM_EMAIL, body)
                self.assertNotIn(VICTIM_USERNAME, body)

    def test_an_anonymous_visitor_sees_no_members_address(self) -> None:
        self.assert_walk_is_clean("anonymous")

    def test_another_signed_in_member_sees_no_members_address(self) -> None:
        self.client.force_login(self.observer)

        self.assert_walk_is_clean("signed-in member")

    def test_the_walk_actually_reaches_the_seeded_records(self) -> None:
        # Guards the sweep: a walk that 404s everywhere would pass by seeing
        # nothing.  The leaderboard must render the pseudonym the member has.
        response = self.client.get(
            reverse(
                "leaderboard",
                kwargs={"course_slug": self.course.slug, "cohort_year": "2026"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Quiet Otter", response.content.decode())
