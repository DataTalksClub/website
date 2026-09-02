"""Registration always happens on this site, and never names a closed edition.

Three states, one definition, four surfaces.  A campaign promoting an edition means that
edition is open; a closed edition offers its course's next one; a course with no campaign
offers nothing at all rather than a link to the platform this site replaces.
"""

from __future__ import annotations

from django.test import TestCase

from courses.models.cohort import RegistrationCampaign
from courses.services.registration_campaigns import (
    active_campaign_for_cohort,
    campaign_slug_in_registration_url,
    family_registration,
    next_edition_campaign_for_cohort,
)
from courses.views.course_page_context import family_lede
from test_support.course_catalog import make_cohort, make_family

CMP_REGISTER_URL = "https://courses.datatalks.club/register/de-zoomcamp/"


class CampaignSlugReadingTests(TestCase):
    def test_reads_the_campaign_slug_a_cmp_registration_url_names(self) -> None:
        self.assertEqual(campaign_slug_in_registration_url(CMP_REGISTER_URL), "de-zoomcamp")
        self.assertEqual(
            campaign_slug_in_registration_url("http://example.test/register/ml-zoomcamp"),
            "ml-zoomcamp",
        )

    def test_reads_nothing_from_a_url_that_is_not_a_campaign_page(self) -> None:
        for value in (
            "",
            "https://courses.datatalks.club/de-zoomcamp-2026/",
            "https://example.test/register/",
            "https://example.test/register/a/b/",
            "not a url",
        ):
            with self.subTest(value=value):
                self.assertEqual(campaign_slug_in_registration_url(value), "")


class CohortCampaignSelectionTests(TestCase):
    def setUp(self) -> None:
        self.family = make_family("de-zoomcamp", "Data Engineering Zoomcamp")
        self.cohort = make_cohort(self.family, 2026, homework_count=1)
        self.cohort.registration_url = CMP_REGISTER_URL
        self.cohort.save(update_fields=["registration_url"])

    def _campaign(self, **kwargs) -> RegistrationCampaign:
        return RegistrationCampaign.objects.create(
            slug=kwargs.pop("slug", "de-zoomcamp"),
            title=kwargs.pop("title", "Data Engineering Zoomcamp"),
            **kwargs,
        )

    def test_a_campaign_promoting_this_edition_is_the_edition_campaign(self) -> None:
        campaign = self._campaign(current_course=self.cohort)

        self.assertEqual(active_campaign_for_cohort(self.cohort), campaign)
        self.assertIsNone(next_edition_campaign_for_cohort(self.cohort))

    def test_a_closed_edition_offers_the_campaign_its_own_url_names(self) -> None:
        campaign = self._campaign()

        self.assertIsNone(active_campaign_for_cohort(self.cohort))
        self.assertEqual(next_edition_campaign_for_cohort(self.cohort), campaign)

    def test_an_inactive_campaign_is_never_offered(self) -> None:
        self._campaign(is_active=False)

        self.assertIsNone(next_edition_campaign_for_cohort(self.cohort))

    def test_a_registration_url_naming_no_local_campaign_offers_nothing(self) -> None:
        self.assertIsNone(next_edition_campaign_for_cohort(self.cohort))


class FamilyRegistrationTests(TestCase):
    def test_an_open_edition_is_named_because_naming_it_is_honest(self) -> None:
        family = make_family("ai-dev-tools", "AI Dev Tools Zoomcamp")
        make_cohort(family, 2025)
        current = make_cohort(family, 2026)
        campaign = RegistrationCampaign.objects.create(
            slug="ai-dev-tools",
            title="AI Dev Tools Zoomcamp",
            current_course=current,
        )

        registration = family_registration(family)

        self.assertTrue(registration)
        self.assertEqual(registration.campaign, campaign)
        self.assertEqual(registration.cohort, current)

    def test_a_family_whose_editions_have_all_closed_names_no_edition(self) -> None:
        family = make_family("de-zoomcamp", "Data Engineering Zoomcamp")
        cohort = make_cohort(family, 2026)
        cohort.registration_url = CMP_REGISTER_URL
        cohort.save(update_fields=["registration_url"])
        campaign = RegistrationCampaign.objects.create(
            slug="de-zoomcamp",
            title="Data Engineering Zoomcamp",
        )

        registration = family_registration(family)

        self.assertEqual(registration.campaign, campaign)
        self.assertIsNone(registration.cohort)

    def test_a_family_with_no_successor_and_no_campaign_offers_nothing(self) -> None:
        family = make_family("mlops-zoomcamp", "MLOps Zoomcamp")
        make_cohort(family, 2024)
        make_cohort(family, 2025)

        registration = family_registration(family)

        self.assertFalse(registration)
        self.assertIsNone(registration.campaign)


class RegistrationSurfaceTests(TestCase):
    """The rendered pages, because four surfaces drifting apart is the actual defect."""

    def setUp(self) -> None:
        self.open_family = make_family("ai-dev-tools", "AI Dev Tools Zoomcamp")
        self.open_cohort = make_cohort(self.open_family, 2026, homework_count=1)
        self.open_campaign = RegistrationCampaign.objects.create(
            slug="ai-dev-tools",
            title="AI Dev Tools Zoomcamp",
            current_course=self.open_cohort,
        )

        self.closed_family = make_family("de-zoomcamp", "Data Engineering Zoomcamp")
        self.closed_cohort = make_cohort(self.closed_family, 2026, homework_count=1)
        self.closed_cohort.registration_url = CMP_REGISTER_URL
        self.closed_cohort.finished = True
        self.closed_cohort.save(update_fields=["registration_url", "finished"])
        RegistrationCampaign.objects.create(
            slug="de-zoomcamp",
            title="Data Engineering Zoomcamp",
        )

        self.silent_family = make_family("mlops-zoomcamp", "MLOps Zoomcamp")
        self.silent_cohort = make_cohort(self.silent_family, 2025, homework_count=1)
        self.silent_cohort.finished = True
        self.silent_cohort.save(update_fields=["finished"])

    def test_an_open_cohort_page_registers_for_that_cohort_on_this_site(self) -> None:
        response = self.client.get("/courses/ai-dev-tools/2026")

        self.assertContains(response, 'href="/courses/register/ai-dev-tools/"')
        self.assertContains(response, "Register for the cohort")
        self.assertNotContains(response, "Register for the next edition")

    def test_a_closed_cohort_page_offers_the_next_edition_not_this_one(self) -> None:
        response = self.client.get("/courses/de-zoomcamp/2026")

        self.assertContains(response, 'href="/courses/register/de-zoomcamp/"')
        self.assertContains(response, "Register for the next edition")
        self.assertNotContains(response, "Register for Data Engineering Zoomcamp 2026")

    def test_a_closed_cohort_with_no_successor_offers_no_registration(self) -> None:
        response = self.client.get("/courses/mlops-zoomcamp/2025")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/courses/register/")
        self.assertNotContains(response, "Register for")

    def test_no_public_course_surface_links_registration_off_this_site(self) -> None:
        for path in (
            "/courses",
            "/courses/ai-dev-tools",
            "/courses/ai-dev-tools/2026",
            "/courses/de-zoomcamp",
            "/courses/de-zoomcamp/2026",
            "/courses/mlops-zoomcamp",
            "/courses/mlops-zoomcamp/2025",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "courses.datatalks.club/register/")

    def test_the_family_page_registers_for_the_open_edition(self) -> None:
        response = self.client.get("/courses/ai-dev-tools")

        self.assertContains(response, 'href="/courses/register/ai-dev-tools/"')
        self.assertContains(response, "Register for the 2026 cohort")

    def test_the_family_page_of_a_closed_course_offers_the_next_edition(self) -> None:
        response = self.client.get("/courses/de-zoomcamp")

        self.assertContains(response, 'href="/courses/register/de-zoomcamp/"')
        self.assertContains(response, "Register for the next edition")

    def test_the_family_page_with_no_successor_offers_no_registration(self) -> None:
        response = self.client.get("/courses/mlops-zoomcamp")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/courses/register/")
        self.assertNotContains(response, "Register for")


class FamilyHeadingRepetitionTests(TestCase):
    """The trail lists ancestors, the h1 says where you are, and neither repeats."""

    def test_the_family_trail_stops_before_the_family_it_is_on(self) -> None:
        make_family("de-zoomcamp", "Data Engineering Zoomcamp")

        response = self.client.get("/courses/de-zoomcamp")

        self.assertContains(response, '<a href="/courses">Courses</a>')
        self.assertNotContains(response, 'aria-current="page">Data Engineering Zoomcamp')
        self.assertContains(response, "Data Engineering Zoomcamp</h1>")

    def test_a_description_that_is_only_the_title_is_not_rendered_as_a_lede(self) -> None:
        family = make_family("de-zoomcamp", "Data Engineering Zoomcamp")
        family.description = "Data Engineering Zoomcamp"
        family.save(update_fields=["description"])

        self.assertEqual(family_lede(family), "")
        self.assertNotContains(self.client.get("/courses/de-zoomcamp"), "family-lede\">")

    def test_the_written_outcome_wins_over_a_readme_description(self) -> None:
        family = make_family("ai-dev-tools", "AI Dev Tools Zoomcamp")
        family.description = "# AI Dev Tools Zoomcamp\n\nA long README body."
        family.outcome = "Build disciplined software workflows with AI assistants."
        family.save(update_fields=["description", "outcome"])

        response = self.client.get("/courses/ai-dev-tools")

        self.assertEqual(family_lede(family), family.outcome)
        self.assertNotContains(response, "# AI Dev Tools Zoomcamp")

    def test_raw_readme_markdown_is_never_rendered_as_a_lede(self) -> None:
        family = make_family("llm-zoomcamp", "LLM Zoomcamp")
        family.description = "# LLM Zoomcamp\n\nA long README body."
        family.save(update_fields=["description"])

        self.assertEqual(family_lede(family), "")

    def test_a_written_one_line_description_is_still_rendered(self) -> None:
        family = make_family("sma-zoomcamp", "Stock Markets Analytics Zoomcamp")
        family.description = "Analyze stock market data with Python and SQL."
        family.save(update_fields=["description"])

        self.assertEqual(family_lede(family), family.description)
        self.assertContains(self.client.get("/courses/sma-zoomcamp"), family.description)


class NewsletterConsentDefaultTests(TestCase):
    """Consent is a deliberate act, so the box may never arrive already ticked.

    Whether consent is *required* to register is a separate, stated product decision
    pinned by ``test_registration_campaigns.test_registration_requires_newsletter_consent``
    and is not decided here.  What this guards is the default: a pre-ticked box is not
    consent at all, and a browser restoring a previously ticked value on reload can make a
    correct form look like a pre-ticked one, so the rendered markup is asserted directly.
    """

    def test_the_newsletter_consent_box_renders_unchecked(self) -> None:
        family = make_family("ml-zoomcamp", "Machine Learning Zoomcamp")
        cohort = make_cohort(family, 2026, homework_count=1)
        RegistrationCampaign.objects.create(
            slug="ml-zoomcamp",
            title="Machine Learning Zoomcamp",
            current_course=cohort,
        )

        response = self.client.get("/courses/register/ml-zoomcamp/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="accepted_newsletter"')
        body = response.content.decode()
        consent = body[body.index('name="accepted_newsletter"') - 40 :][:200]
        self.assertNotIn("checked", consent.split(">")[0])
