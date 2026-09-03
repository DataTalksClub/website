"""`/` branches on authentication: marketing for a visitor, the member home for a member.

The contracts asserted here are the ones `_docs/design/specs/signed-in-home.md`
names normatively: §3 (one URL, two branches, and the caching boundary between
them), §5 (the four data-driven states and the hide-don't-explain rule), and
§7.2 (the checklist's server-persisted, allowlisted dismissals).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models.cohort import Cohort, Enrollment
from courses.models.curriculum import Unit, UnitReadState
from courses.models.homework import Homework, HomeworkState
from test_support.course_catalog import make_cohort, make_family

MARKETING_HEADING = "Learn the fundamentals. Build real projects. Share your work."


def _member(email: str = "member@example.invalid"):
    return get_user_model().objects.create_user(username=email, email=email)


def _module_cohort(*, slug: str, year: int, title: str) -> Cohort:
    family = make_family(slug, title)
    cohort = make_cohort(
        family,
        year,
        start_date=date(year, 1, 6),
        homework_count=2,
        module_titles=("Module one", "Module two"),
    )
    cohort.curriculum_format = "modules"
    cohort.save(update_fields=["curriculum_format"])
    for index, module in enumerate(cohort.modules.order_by("position")):
        Unit.objects.create(
            module=module,
            position=index,
            slug=f"unit-{index + 1}",
            title=f"Unit {index + 1}",
        )
    return cohort


class HomeBranchTests(TestCase):
    """§3: one URL, two branches, and no leakage between their caches."""

    def test_a_visitor_still_gets_the_marketing_home_with_no_cookie_or_csrf_token(self):
        response = self.client.get(reverse("home"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(MARKETING_HEADING, body)
        self.assertNotIn("Getting started", body)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertNotIn("csrf-token", body)

    def test_a_member_gets_the_member_home_and_it_is_never_cached(self):
        self.client.force_login(_member())

        response = self.client.get(reverse("home"))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/member_home.html")
        self.assertNotIn(MARKETING_HEADING, body)
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_the_member_home_leads_with_onboarding_when_there_are_no_courses(self):
        self.client.force_login(_member())

        body = self.client.get(reverse("home")).content.decode()

        self.assertIn("Welcome to DataTalks.Club", body)
        self.assertIn("Getting started", body)
        # §2/§5: an empty section is omitted, never rendered as "nothing here yet".
        self.assertNotIn("Your courses", body)
        self.assertNotIn("Due soon", body)
        self.assertNotIn("Your finished courses", body)


class MemberHomeStateTests(TestCase):
    """§5: what the page says about the courses a member is actually in."""

    def setUp(self):
        self.member = _member()
        self.client.force_login(self.member)

    def test_an_active_enrolment_becomes_a_card_with_a_resume_target(self):
        cohort = _module_cohort(slug="llm-zoomcamp", year=2026, title="LLM Zoomcamp")
        Enrollment.objects.create(student=self.member, course=cohort)

        body = self.client.get(reverse("home")).content.decode()

        self.assertIn("Welcome back", body)
        self.assertIn("Your courses", body)
        self.assertIn(cohort.title, body)
        self.assertIn("enrolled", body)
        # No unit read yet, so the first unit is a start rather than a resume.
        self.assertIn("Start course", body)

        first_unit = Unit.objects.filter(module__cohort=cohort).order_by(
            "module__position", "position"
        )[0]
        UnitReadState.objects.create(user=self.member, unit=first_unit)

        body = self.client.get(reverse("home")).content.decode()
        self.assertIn("Continue", body)

    def test_an_open_homework_deadline_reaches_the_card_and_a_submitted_one_does_not(self):
        cohort = _module_cohort(slug="ml-zoomcamp", year=2026, title="ML Zoomcamp")
        Enrollment.objects.create(student=self.member, course=cohort)
        due = timezone.now() + timedelta(days=3)
        Homework.objects.filter(course=cohort).update(
            state=HomeworkState.CLOSED.value, due_date=due
        )
        homework = Homework.objects.filter(course=cohort).order_by("id").first()
        assert homework is not None
        homework.state = HomeworkState.OPEN.value
        homework.save(update_fields=["state"])

        body = self.client.get(reverse("home")).content.decode()

        self.assertIn("Next deadline", body)

        homework.state = HomeworkState.CLOSED.value
        homework.save(update_fields=["state"])
        body = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("Next deadline", body)

    def test_a_cohort_that_has_not_started_reads_as_registered_and_not_yet_open(self):
        family = make_family("upcoming-zoomcamp", "Upcoming Zoomcamp")
        cohort = make_cohort(
            family,
            2027,
            start_date=timezone.localdate() + timedelta(days=30),
        )
        cohort.registration_url = "https://example.invalid/register"
        cohort.save(update_fields=["registration_url"])
        Enrollment.objects.create(student=self.member, course=cohort)

        body = self.client.get(reverse("home")).content.decode()

        self.assertIn("Materials aren't published yet.", body)
        self.assertNotIn("Next deadline", body)

    def _upcoming_cohort(self, slug: str, title: str, year: int, days: int) -> Cohort:
        family = make_family(slug, title)
        cohort = make_cohort(
            family,
            year,
            start_date=timezone.localdate() + timedelta(days=days),
        )
        cohort.registration_url = "https://example.invalid/register"
        cohort.save(update_fields=["registration_url"])
        Enrollment.objects.create(student=self.member, course=cohort)
        return cohort

    def test_the_hero_never_claims_progress_a_member_could_not_have_made(self):
        """§5: "pick up where you left off" is true only when something is running."""

        self._upcoming_cohort("later-zoomcamp", "Later Zoomcamp", 2027, days=60)
        self._upcoming_cohort("sooner-zoomcamp", "Sooner Zoomcamp", 2027, days=30)

        body = self.client.get(reverse("home")).content.decode()

        self.assertNotIn("Pick up where you left off", body)
        # The lede names the cohort that opens first, and says what happens next.
        self.assertIn("You're registered for Sooner Zoomcamp", body)
        self.assertIn("we'll email you when materials open", body)

    def test_an_upcoming_cohort_beside_a_finished_one_still_reads_honestly(self):
        family = make_family("done-zoomcamp", "Done Zoomcamp")
        finished = make_cohort(family, 2025, start_date=date(2025, 5, 5))
        finished.finished = True
        finished.save(update_fields=["finished"])
        Enrollment.objects.create(student=self.member, course=finished)
        self._upcoming_cohort("next-zoomcamp", "Next Zoomcamp", 2027, days=30)

        body = self.client.get(reverse("home")).content.decode()

        self.assertNotIn("Pick up where you left off", body)
        self.assertIn("You're registered for Next Zoomcamp", body)

    def test_only_finished_cohorts_show_the_score_and_certificate(self):
        family = make_family("mlops-zoomcamp", "MLOps Zoomcamp")
        cohort = make_cohort(family, 2025, start_date=date(2025, 5, 5))
        cohort.finished = True
        cohort.first_homework_scored = True
        cohort.save(update_fields=["finished", "first_homework_scored"])
        Enrollment.objects.create(
            student=self.member,
            course=cohort,
            total_score=87,
            certificate_url="https://example.invalid/cert.pdf",
        )

        body = self.client.get(reverse("home")).content.decode()

        self.assertIn("Your last cohort has finished.", body)
        self.assertIn("Your finished courses", body)
        self.assertIn("87", body)
        self.assertIn("https://example.invalid/cert.pdf", body)


class MemberHomeQueryBudgetTests(TestCase):
    """The member home reads a fixed number of queries per cohort, not per item."""

    def test_more_homework_in_a_cohort_does_not_cost_more_queries(self):
        member = _member()
        self.client.force_login(member)
        family = make_family("scaling-zoomcamp", "Scaling Zoomcamp")
        cohort = make_cohort(family, 2026, start_date=date(2026, 1, 6), homework_count=2)
        Enrollment.objects.create(student=member, course=cohort)

        small = self._queries(reverse("home"))

        due = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
        for index in range(20):
            Homework.objects.create(
                course=cohort,
                slug=f"extra-hw{index}",
                title=f"Extra homework {index}",
                due_date=due + timedelta(days=index),
                state=HomeworkState.OPEN.value,
            )

        large = self._queries(reverse("home"))

        self.assertEqual(small, large)

    def _queries(self, url: str) -> int:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(captured)


class HomeDismissalTests(TestCase):
    """§7.2: skips are remembered server-side, for this member and no other."""

    def setUp(self):
        self.member = _member()
        self.other = _member("other@example.invalid")
        self.client.force_login(self.member)
        self.url = reverse("dismiss_home_item")

    def test_a_skip_is_persisted_on_the_member_who_pressed_it(self):
        response = self.client.post(self.url, {"key": "getting_started_skip_profile"})

        self.assertRedirects(response, reverse("home"))
        self.member.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.member.home_dismissals, {"getting_started_skip_profile": True})
        self.assertEqual(self.other.home_dismissals, {})

    def test_a_key_outside_the_allowlist_is_refused(self):
        response = self.client.post(self.url, {"key": "is_superuser"})

        self.assertEqual(response.status_code, 400)
        self.member.refresh_from_db()
        self.assertEqual(self.member.home_dismissals, {})

    def test_the_allowlist_holds_only_dismissals_a_member_can_actually_make(self):
        """Every key has a control behind it; §9's event nudge has no surface yet."""

        from accounts.home_dismissals import HOME_DISMISSAL_KEYS

        self.assertEqual(
            HOME_DISMISSAL_KEYS,
            frozenset(
                {
                    "getting_started_skip_course",
                    "getting_started_slack_done",
                    "getting_started_skip_slack",
                    "getting_started_skip_profile",
                    "getting_started_checklist",
                }
            ),
        )
        refused = self.client.post(self.url, {"key": "profile_nudge"})
        self.assertEqual(refused.status_code, 400)

    def test_a_dismissal_cannot_be_aimed_at_another_member(self):
        response = self.client.post(
            self.url,
            {"key": "getting_started_skip_slack", "user": self.other.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.other.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.other.home_dismissals, {})
        self.assertEqual(self.member.home_dismissals, {"getting_started_skip_slack": True})

    def test_the_slack_row_records_the_click_and_still_goes_to_slack(self):
        response = self.client.post(
            self.url,
            {"key": "getting_started_slack_done", "redirect_to": "slack"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("slack"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.home_dismissals, {"getting_started_slack_done": True})

    def test_an_unknown_destination_falls_back_to_the_home_page(self):
        response = self.client.post(
            self.url,
            {"key": "getting_started_skip_slack", "redirect_to": "https://example.invalid/"},
        )

        self.assertRedirects(response, reverse("home"))

    def test_the_endpoint_needs_a_session_and_a_post(self):
        self.client.logout()
        anonymous = self.client.post(self.url, {"key": "getting_started_skip_slack"})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("login"), anonymous.headers["Location"])

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 405)


class AboutYouPageTests(TestCase):
    """§7.3: the slim onboarding page saves what it is given and requires nothing."""

    def setUp(self):
        self.member = _member()
        self.client.force_login(self.member)
        self.url = reverse("account_welcome")

    def test_the_page_requires_a_session(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.headers["Location"])

    def test_country_and_role_use_the_widgets_registration_uses(self):
        """§7.3 names both: a plain text pair would store what registration rejects."""

        self.member.registration_role = "data_engineer"
        self.member.save(update_fields=["registration_role"])

        body = self.client.get(self.url).content.decode()

        # Country is the registration form's combobox, hook for hook.
        self.assertIn("data-country-combobox-input", body)
        self.assertIn('id="country-options-json"', body)
        self.assertIn("data-country-combobox-panel", body)
        self.assertIn("country_combobox.js", body)
        # Role is a select over the registration role vocabulary, so the page
        # reads "Data Engineer" rather than the stored "data_engineer".
        self.assertIn('<option value="data_engineer" selected>Data Engineer</option>', body)
        self.assertIn('<option value="ml_engineer">ML Engineer</option>', body)

    def test_a_country_registration_would_reject_is_refused_here_too(self):
        response = self.client.post(
            self.url,
            {"certificate_name": "Ada Lovelace", "country": "Nowhere"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid country.")
        self.member.refresh_from_db()
        self.assertEqual(self.member.country, "")

    def test_saving_a_country_derives_the_region_registration_would_derive(self):
        self.client.post(self.url, {"country": "Germany"})

        self.member.refresh_from_db()
        self.assertEqual(self.member.country, "Germany")
        self.assertEqual(self.member.region, "Europe")

    def test_saving_the_three_core_fields_completes_the_checklist_item(self):
        response = self.client.post(
            self.url,
            {
                "certificate_name": "Ada Lovelace",
                "country": "United Kingdom",
                "registration_role": "data_engineer",
            },
        )

        self.assertRedirects(response, reverse("home"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.certificate_name, "Ada Lovelace")
        self.assertEqual(self.member.country, "United Kingdom")
        self.assertEqual(self.member.registration_role, "data_engineer")

        body = self.client.get(reverse("home")).content.decode()
        # The row stays on the checklist; it is marked done and offers no action.
        self.assertIn("Tell us about you", body)
        self.assertNotIn("Add your details", body)

    def test_an_empty_submission_is_accepted_and_writes_nothing(self):
        response = self.client.post(self.url, {})

        self.assertRedirects(response, reverse("home"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.certificate_name or "", "")

    def test_account_settings_keeps_what_onboarding_collected(self):
        """The two surfaces share one model; saving one must not blank the other."""

        self.client.post(
            self.url,
            {
                "certificate_name": "Ada Lovelace",
                "country": "United Kingdom",
                "registration_role": "data_engineer",
            },
        )
        self.member.refresh_from_db()

        settings_page = self.client.get(reverse("account_settings")).content.decode()
        self.assertIn('id="id_country"', settings_page)
        self.assertIn('id="id_registration_role"', settings_page)

        self.client.post(
            reverse("account_settings"),
            {
                "certificate_name": self.member.certificate_name,
                "country": self.member.country,
                "registration_role": self.member.registration_role,
                "github_url": "",
                "linkedin_url": "",
                "personal_website_url": "",
                "about_me": "",
                "preferred_timezone": "",
            },
        )

        self.member.refresh_from_db()
        self.assertEqual(self.member.country, "United Kingdom")
        self.assertEqual(self.member.registration_role, "data_engineer")
