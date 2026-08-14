from django.urls import reverse

from courses.models import RegistrationCampaign
from courses.tests.registration_campaign_base import RegistrationCampaignBase


class RegistrationCanonicalTests(RegistrationCampaignBase):
    def test_registration_pages_emit_exact_production_canonical_for_each_slug(self):
        for campaign_slug in ("ml-zoomcamp", "ai-dev-tools"):
            campaign = RegistrationCampaign.objects.create(
                slug=campaign_slug,
                title=f"{campaign_slug} registration",
                current_course=self.course,
            )
            url = reverse(
                "registration_campaign",
                kwargs={"campaign_slug": campaign.slug},
            )

            for query in ("", "?utm_source=canonical-test"):
                with self.subTest(campaign_slug=campaign_slug, query=query):
                    response = self.client.get(f"{url}{query}")
                    expected = (
                        '<link rel="canonical" '
                        f'href="https://datatalks.club/register/{campaign_slug}">'
                    )

                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, expected, count=1)
                    self.assertEqual(
                        response.content.count(b'<link rel="canonical"'),
                        1,
                    )
                    self.assertNotContains(
                        response,
                        f"https://datatalks.club/register/{campaign_slug}/",
                    )
                    self.assertNotContains(
                        response,
                        'href="https://web.dtcdev.click/register/',
                    )
                    self.assertEqual(
                        response.headers["X-Robots-Tag"],
                        "noindex, nofollow",
                    )
