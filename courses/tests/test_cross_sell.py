"""Cross-sell after registration (_docs/runbooks/cross-sell.md)."""

import datetime
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, CourseRegistration, Enrollment, RegistrationCampaign
from courses.registration_confirmation import confirm_registration_by_token
from courses.services import cross_sell
from courses.tests.registration_campaign_base import RegistrationCampaignBase


class CrossSellBase(RegistrationCampaignBase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.de = self.make_campaign(
            "de-zoomcamp", "Data Engineering Zoomcamp", today + datetime.timedelta(days=60)
        )
        self.ml = self.make_campaign(
            "ml-zoomcamp", "ML Zoomcamp", today + datetime.timedelta(days=30)
        )
        self.mlops = self.make_campaign(
            "mlops-zoomcamp", "MLOps Zoomcamp", today + datetime.timedelta(days=90)
        )
        self.nodate = self.make_campaign("ai-dev-tools", "AI Dev Tools", None)

    def make_campaign(self, slug, title, start_date, **campaign_fields):
        cohort = Cohort.objects.create(
            slug=f"{slug}-2026",
            title=f"{title} 2026",
            description=f"{title} course",
            start_date=start_date,
        )
        return RegistrationCampaign.objects.create(
            slug=slug,
            title=title,
            current_course=cohort,
            **campaign_fields,
        )

    def slugs(self, items):
        return [item.campaign.slug for item in items]

    def register(self, **extra):
        payload = self.registration_payload()
        payload.update(extra)
        return self.client.post(self.campaign_url(), payload)


class SuggestionRuleTests(CrossSellBase):
    def test_fallback_orders_by_start_date_with_undated_last(self):
        items = cross_sell.suggestions(self.campaign, limit=10)
        self.assertEqual(
            self.slugs(items),
            ["ml-zoomcamp", "de-zoomcamp", "mlops-zoomcamp", "ai-dev-tools"],
        )

    def test_default_limit_is_three(self):
        self.assertEqual(len(cross_sell.suggestions(self.campaign)), 3)

    def test_curated_campaigns_come_first(self):
        self.campaign.suggested_campaigns.set([self.mlops, self.nodate])
        items = cross_sell.suggestions(self.campaign)
        self.assertEqual(self.slugs(items), ["mlops-zoomcamp", "ai-dev-tools", "ml-zoomcamp"])

    def test_curation_is_not_symmetric(self):
        self.campaign.suggested_campaigns.set([self.mlops])
        self.assertFalse(self.mlops.suggested_campaigns.exists())

    def test_excludes_finished_inactive_hidden_and_flagged(self):
        Cohort.objects.filter(pk=self.ml.current_course_id).update(finished=True)
        RegistrationCampaign.objects.filter(pk=self.de.pk).update(is_active=False)
        RegistrationCampaign.objects.filter(pk=self.nodate.pk).update(exclude_from_suggestions=True)
        hidden = self.make_campaign("hidden", "Hidden", None)
        Cohort.objects.filter(pk=hidden.current_course_id).update(visible=False)
        items = cross_sell.suggestions(self.campaign, limit=10)
        self.assertEqual(self.slugs(items), ["mlops-zoomcamp"])

    @override_settings(CROSS_SELL_EXCLUDED_CAMPAIGNS=["ml-zoomcamp"])
    def test_settings_exclusion_list(self):
        self.assertNotIn("ml-zoomcamp", self.slugs(cross_sell.suggestions(self.campaign, limit=10)))

    def test_excludes_same_course_family(self):
        sibling = RegistrationCampaign.objects.create(
            slug="llm-next",
            title="LLM next",
            current_course=Cohort.objects.create(
                slug="llm-zoomcamp-2027",
                title="LLM Zoomcamp 2027",
                identifier="2027",
                year=2027,
                course=self.course.course,
            ),
        )
        self.assertNotIn(sibling.slug, self.slugs(cross_sell.suggestions(self.campaign, limit=10)))

    def test_excludes_already_registered_and_enrolled(self):
        user = self.create_signed_user()
        CourseRegistration.objects.create(
            campaign=self.ml,
            email="SIGNED@example.com",
            name="Signed",
            country="Germany",
            region="Europe",
            role=CourseRegistration.Role.OTHER,
        )
        Enrollment.objects.create(student=user, course=self.de.current_course)
        items = cross_sell.suggestions(self.campaign, user=user, limit=10)
        self.assertEqual(self.slugs(items), ["mlops-zoomcamp", "ai-dev-tools"])

    def test_application_campaigns_are_link_only(self):
        RegistrationCampaign.objects.filter(pk=self.ml.pk).update(requires_approval=True)
        items = {item.campaign.slug: item for item in cross_sell.suggestions(self.campaign)}
        self.assertFalse(items["ml-zoomcamp"].one_click)
        self.assertTrue(items["de-zoomcamp"].one_click)
        self.assertEqual(
            items["ml-zoomcamp"].url, "/courses/register/ml-zoomcamp/?src=xsell&from=llm-zoomcamp"
        )


class SuccessPageTests(CrossSellBase):
    def test_success_page_shows_suggestions_and_records_impression(self):
        with patch("courses.services.cross_sell.record_event") as event:
            response = self.register()
        self.assertContains(response, "You might also like")
        self.assertContains(response, 'data-cross-sell-row="ml-zoomcamp"')
        self.assertContains(response, "Register for selected")
        self.assertNotContains(response, 'data-cross-sell-row="llm-zoomcamp"')
        names = [call.args[0] for call in event.call_args_list]
        self.assertIn("cross_sell.impression", names)

    def test_form_page_before_registration_has_no_suggestions(self):
        response = self.client.get(self.campaign_url())
        self.assertNotContains(response, "You might also like")

    def test_one_click_copies_registration_without_new_data(self):
        self.register()
        source = CourseRegistration.objects.get(campaign=self.campaign)
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        response = self.client.post(url, {"campaign": "ml-zoomcamp"})
        self.assertRedirects(
            response,
            f"{self.campaign_url()}#registration-form-heading",
            fetch_redirect_response=False,
        )
        copy = CourseRegistration.objects.get(campaign=self.ml)
        self.assertEqual(copy.email_normalized, source.email_normalized)
        self.assertEqual(copy.name, "Student One")
        self.assertEqual(copy.country, "Germany")
        self.assertEqual(copy.region, source.region)
        self.assertEqual(copy.role, source.role)
        self.assertTrue(copy.accepted_newsletter)
        self.assertEqual(copy.source, "xsell")
        self.assertEqual(copy.referred_by, source)
        self.assertFalse(copy.email_confirmed)
        self.assertIsNone(copy.email_confirmation_token)

    def test_multi_select_registers_several_and_flashes(self):
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        self.client.post(url, {"campaigns": ["ml-zoomcamp", "de-zoomcamp"]})
        self.assertEqual(
            set(
                CourseRegistration.objects.filter(source="xsell").values_list(
                    "campaign__slug", flat=True
                )
            ),
            {"ml-zoomcamp", "de-zoomcamp"},
        )

    def test_tampered_and_application_slugs_are_ignored(self):
        RegistrationCampaign.objects.filter(pk=self.ml.pk).update(requires_approval=True)
        hidden = self.make_campaign(
            "not-suggested", "Not suggested", None, exclude_from_suggestions=True
        )
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        self.client.post(url, {"campaigns": ["ml-zoomcamp", hidden.slug, "llm-zoomcamp", "nope"]})
        self.assertFalse(CourseRegistration.objects.filter(source="xsell").exists())

    def test_without_own_registration_nothing_is_created(self):
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        response = self.client.post(url, {"campaign": "ml-zoomcamp"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CourseRegistration.objects.exists())

    def test_repeat_post_is_idempotent(self):
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        self.client.post(url, {"campaign": "ml-zoomcamp"})
        self.client.post(url, {"campaign": "ml-zoomcamp"})
        self.assertEqual(CourseRegistration.objects.filter(campaign=self.ml).count(), 1)

    def test_flash_and_recomputed_suggestions_after_one_click(self):
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        response = self.client.post(url, {"campaign": "ml-zoomcamp"}, follow=True)
        self.assertContains(response, "Also registered for:")
        self.assertContains(response, "ML Zoomcamp")
        self.assertNotContains(response, 'data-cross-sell-row="ml-zoomcamp"')
        self.assertContains(response, 'data-cross-sell-row="de-zoomcamp"')

    @override_settings(REGISTRATION_REQUIRES_ACCOUNT=True)
    def test_signed_in_member_registers_with_account(self):
        user = self.create_signed_user()
        self.client.force_login(user)
        self.client.post(self.campaign_url(), self.registration_payload(email="signed@example.com"))
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        self.client.post(url, {"campaign": "de-zoomcamp"})
        copy = CourseRegistration.objects.get(campaign=self.de)
        self.assertEqual(copy.user, user)
        self.assertEqual(copy.email_normalized, "signed@example.com")

    @override_settings(REGISTRATION_REQUIRES_ACCOUNT=True)
    def test_gated_anonymous_post_goes_to_login(self):
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        response = self.client.post(url, {"campaign": "ml-zoomcamp"})
        self.assertIn(reverse("login"), response["Location"])


class ConfirmationTests(CrossSellBase):
    def _one_click(self):
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        self.client.post(url, {"campaigns": ["ml-zoomcamp", "de-zoomcamp"]})
        return CourseRegistration.objects.get(campaign=self.campaign)

    def test_source_confirmation_confirms_one_click_copies(self):
        source = self._one_click()
        registration, outcome = confirm_registration_by_token(
            CourseRegistration, source.email_confirmation_token
        )
        self.assertEqual(outcome, "confirmed")
        self.assertEqual(
            CourseRegistration.objects.filter(source="xsell", email_confirmed=True).count(), 2
        )

    def test_copy_of_confirmed_registration_is_confirmed(self):
        self.register()
        source = CourseRegistration.objects.get(campaign=self.campaign)
        confirm_registration_by_token(CourseRegistration, source.email_confirmation_token)
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        self.client.post(url, {"campaign": "ml-zoomcamp"})
        copy = CourseRegistration.objects.get(campaign=self.ml)
        self.assertTrue(copy.email_confirmed)
        self.assertIsNotNone(copy.email_confirmation_confirmed_at)

    def test_one_click_sends_no_confirmation_email(self):
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        with patch("course_management.package_mail.send_package_mail") as send:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"campaign": "ml-zoomcamp"})
        send.assert_not_called()

    def test_email_fix_moves_unconfirmed_copies(self):
        source = self._one_click()
        source.email = "fixed@example.com"
        source.save(update_fields=["email", "email_normalized"])
        cross_sell.follow_email_fix(source)
        self.assertEqual(
            set(
                CourseRegistration.objects.filter(source="xsell").values_list(
                    "email_normalized", flat=True
                )
            ),
            {"fixed@example.com"},
        )

    def test_deleting_source_removes_unconfirmed_copies(self):
        source = self._one_click()
        source.delete()
        self.assertFalse(CourseRegistration.objects.filter(source="xsell").exists())


class TrackingTests(CrossSellBase):
    def test_link_click_is_recorded_and_kept_as_source(self):
        ml_url = reverse("registration_campaign", kwargs={"campaign_slug": "ml-zoomcamp"})
        with patch("courses.services.cross_sell.record_event") as event:
            self.client.get(f"{ml_url}?src=xsell-email&from=llm-zoomcamp")
        self.assertEqual(event.call_args.args[0], "cross_sell.click")
        self.assertEqual(event.call_args.kwargs["properties"]["source"], "xsell-email")
        self.client.post(ml_url, self.registration_payload())
        self.assertEqual(CourseRegistration.objects.get(campaign=self.ml).source, "xsell-email")

    def test_plain_visit_has_no_source(self):
        self.register()
        self.assertEqual(CourseRegistration.objects.get(campaign=self.campaign).source, "")

    def test_unknown_src_is_ignored(self):
        with patch("courses.services.cross_sell.record_event") as event:
            self.client.get(f"{self.campaign_url()}?src=evil")
        event.assert_not_called()

    def test_one_click_records_registered_event(self):
        self.register()
        url = reverse("registration_cross_sell", kwargs={"campaign_slug": self.campaign.slug})
        with patch("courses.services.cross_sell.record_event") as event:
            self.client.post(url, {"campaign": "ml-zoomcamp"})
        registered = [c for c in event.call_args_list if c.args[0] == "cross_sell.registered"]
        self.assertEqual(len(registered), 1)
        properties = registered[0].kwargs["properties"]
        self.assertEqual(properties["campaign"], "ml-zoomcamp")
        self.assertNotIn("email", str(properties))


class EmailTests(CrossSellBase):
    def test_email_block_lists_suggestions_with_tracking_links(self):
        self.register()
        source = CourseRegistration.objects.get(campaign=self.campaign)
        text = cross_sell.email_suggestions_markdown(source)
        self.assertIn("You might also like", text)
        self.assertIn("[ML Zoomcamp](", text)
        self.assertIn("/courses/register/ml-zoomcamp/?src=xsell-email&from=llm-zoomcamp", text)
        self.assertIn("(starts ", text)

    def test_email_block_empty_without_candidates(self):
        RegistrationCampaign.objects.exclude(pk=self.campaign.pk).update(is_active=False)
        self.register()
        source = CourseRegistration.objects.get(campaign=self.campaign)
        self.assertEqual(cross_sell.email_suggestions_markdown(source), "")
