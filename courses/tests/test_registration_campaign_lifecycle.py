from django.test import TestCase

from core.models import AuditEvent
from courses.models import Cohort, RegistrationCampaign, User
from courses.services.registration_campaigns import (
    RegistrationCampaignStateError,
    open_new_cohort,
    stop_registration,
)


class RegistrationCampaignLifecycleServiceTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="operator@test.com",
            email="operator@test.com",
            password="12345",
        )
        self.cohort = Cohort.objects.create(
            slug="llm-zoomcamp-2026",
            title="LLM Zoomcamp 2026",
            description="LLM course",
        )
        self.next_cohort = Cohort.objects.create(
            slug="llm-zoomcamp-2027",
            title="LLM Zoomcamp 2027",
            description="Next LLM course",
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
            current_course=self.cohort,
        )

    def test_stop_registration_clears_current_course(self):
        updated = stop_registration(self.campaign, actor_ref="user:1")

        self.assertIsNone(updated.current_course)
        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.current_course)

    def test_stop_registration_records_audit_event(self):
        stop_registration(
            self.campaign, actor_ref="user:1", actor_id=self.operator.pk
        )

        event = AuditEvent.objects.get(
            action="courses.registration_campaign.registration_stopped"
        )
        self.assertEqual(event.target_type, "courses.registration_campaign")
        self.assertEqual(event.target_label, "llm-zoomcamp")
        self.assertEqual(event.actor_ref, "user:1")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCEEDED)
        self.assertEqual(
            event.changes["current_course_id"],
            {"before": self.cohort.pk, "after": None},
        )

    def test_stop_registration_fails_closed_when_already_stopped(self):
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))

        with self.assertRaises(RegistrationCampaignStateError):
            stop_registration(self.campaign, actor_ref="user:1")

        self.assertEqual(
            AuditEvent.objects.filter(
                action="courses.registration_campaign.registration_stopped"
            ).count(),
            0,
        )

    def test_open_new_cohort_sets_current_course(self):
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))

        updated = open_new_cohort(self.campaign, self.next_cohort, actor_ref="user:1")

        self.assertEqual(updated.current_course, self.next_cohort)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.current_course, self.next_cohort)

    def test_open_new_cohort_records_audit_event(self):
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))

        open_new_cohort(
            self.campaign,
            self.next_cohort,
            actor_ref="user:1",
            actor_id=self.operator.pk,
        )

        event = AuditEvent.objects.get(
            action="courses.registration_campaign.cohort_opened"
        )
        self.assertEqual(event.target_label, "llm-zoomcamp")
        self.assertEqual(
            event.changes["current_course_id"],
            {"before": None, "after": self.next_cohort.pk},
        )

    def test_open_new_cohort_guard_rejects_a_still_open_campaign(self):
        # self.campaign already has current_course set in setUp.
        with self.assertRaises(RegistrationCampaignStateError):
            open_new_cohort(self.campaign, self.next_cohort, actor_ref="user:1")

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.current_course, self.cohort)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="courses.registration_campaign.cohort_opened"
            ).count(),
            0,
        )

    def test_stop_then_open_round_trip(self):
        stop_registration(self.campaign, actor_ref="user:1")
        updated = open_new_cohort(self.campaign, self.next_cohort, actor_ref="user:1")

        self.assertEqual(updated.current_course, self.next_cohort)

    def test_open_new_cohort_requires_a_cohort_instance(self):
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))

        with self.assertRaises(RegistrationCampaignStateError):
            open_new_cohort(self.campaign, Cohort(), actor_ref="user:1")
