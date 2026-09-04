from core.models import AuditEvent
from courses.models import Cohort, CourseRegistration, RegistrationCampaign

from .registration_campaign_base import RegistrationCampaignAPITestBase


class RegistrationCampaignAPITestCase(RegistrationCampaignAPITestBase):
    def test_create_and_patch_registration_campaign(self):
        create_payload = {
            "slug": "llm-zoomcamp",
            "title": "LLM Zoomcamp",
            "edition_label": "2026 cohort",
            "current_course": self.course.slug,
            "marketing_markdown": "Register now",
        }
        response = self.post_campaign(self.client, create_payload)

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["slug"], "llm-zoomcamp")
        self.assertEqual(data["current_course"], self.course.slug)

        patch_payload = {
            "current_course": None,
        }
        response = self.patch_campaign(self.client, patch_payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["current_course"])

    def test_create_with_current_course_goes_through_open_new_cohort_and_is_audited(self):
        create_payload = {
            "slug": "llm-zoomcamp",
            "title": "LLM Zoomcamp",
            "current_course": self.course.slug,
        }
        response = self.post_campaign(self.client, create_payload)

        self.assertEqual(response.status_code, 201)
        event = AuditEvent.objects.get(
            action="courses.registration_campaign.cohort_opened"
        )
        self.assertEqual(event.target_label, "llm-zoomcamp")
        self.assertEqual(
            event.changes["current_course_id"],
            {"before": None, "after": self.course.pk},
        )

    def test_patch_current_course_null_goes_through_stop_registration_and_is_audited(self):
        campaign = self.create_campaign()

        response = self.patch_campaign(self.client, {"current_course": None})

        self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.get(
            action="courses.registration_campaign.registration_stopped"
        )
        self.assertEqual(event.target_label, campaign.slug)
        self.assertEqual(
            event.changes["current_course_id"],
            {"before": self.course.pk, "after": None},
        )

    def test_patch_current_course_to_new_cohort_goes_through_open_new_cohort(self):
        campaign = self.create_campaign()
        campaign.current_course = None
        campaign.save(update_fields=("current_course", "updated_at"))
        next_cohort = Cohort.objects.create(
            slug="llm-zoomcamp-2027",
            title="LLM Zoomcamp 2027",
            description="Next edition",
        )

        response = self.patch_campaign(
            self.client, {"current_course": next_cohort.slug}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["current_course"], next_cohort.slug)
        event = AuditEvent.objects.get(
            action="courses.registration_campaign.cohort_opened"
        )
        self.assertEqual(
            event.changes["current_course_id"],
            {"before": None, "after": next_cohort.pk},
        )

    def test_patch_cannot_repoint_an_open_campaign_directly_to_another_cohort(self):
        self.create_campaign()
        other_cohort = Cohort.objects.create(
            slug="llm-zoomcamp-2027",
            title="LLM Zoomcamp 2027",
            description="Next edition",
        )

        response = self.patch_campaign(
            self.client, {"current_course": other_cohort.slug}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["code"], "invalid_registration_campaign_state"
        )
        campaign = RegistrationCampaign.objects.get(slug="llm-zoomcamp")
        self.assertEqual(campaign.current_course, self.course)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="courses.registration_campaign.cohort_opened"
            ).count(),
            0,
        )

    def test_patch_current_course_to_its_own_value_is_a_no_op(self):
        campaign = self.create_campaign()

        response = self.patch_campaign(
            self.client, {"current_course": self.course.slug}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["current_course"], self.course.slug)
        campaign.refresh_from_db()
        self.assertEqual(campaign.current_course, self.course)
        self.assertFalse(
            AuditEvent.objects.filter(
                action__startswith="courses.registration_campaign."
            ).exists()
        )

    def test_patch_current_course_null_on_already_stopped_campaign_is_a_no_op(self):
        campaign = self.create_campaign()
        campaign.current_course = None
        campaign.save(update_fields=("current_course", "updated_at"))

        # CMP's PATCH is an upsert-style resync, so repeating "already stopped" must
        # succeed silently rather than fail closed -- only a genuine state transition
        # goes through the guarded stop_registration/open_new_cohort services.
        response = self.patch_campaign(self.client, {"current_course": None})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["current_course"])
        self.assertFalse(
            AuditEvent.objects.filter(
                action__startswith="courses.registration_campaign."
            ).exists()
        )

    def test_registration_campaign_registrations_stats(self):
        campaign = self.create_campaign()
        registration = self.create_registration(campaign)
        registration.role = CourseRegistration.Role.DATA_ENGINEER
        registration.save()

        url = self.campaign_registrations_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stats"]["total"], 1)
        self.assertEqual(data["stats"]["by_region"][0]["value"], "Europe")
        self.assertEqual(
            data["registrations"][0]["email"],
            "student@example.com",
        )
        self.assertEqual(
            data["registrations"][0]["company_name"],
            "Acme Data",
        )
