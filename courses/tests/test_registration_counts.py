from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.test import TestCase

from courses.models import Cohort, CourseRegistration, RegistrationCampaign
from courses.services.registration_counts import (
    public_course_registration_count,
    public_family_registration_count,
)

NATIVE_START = datetime(2026, 2, 1, tzinfo=UTC)


class PublicCourseRegistrationCountTests(TestCase):
    def setUp(self) -> None:
        self.cohort = Cohort.objects.create(
            slug="synthetic-cohort-2026",
            title="Synthetic cohort",
            description="Deterministic aggregate fixture.",
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug="synthetic-campaign",
            title="Synthetic campaign",
            edition_label="2026 cohort",
            current_course=self.cohort,
        )

    def test_no_current_cohort_returns_none(self) -> None:
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))

        self.assertIsNone(public_course_registration_count(self.campaign))

    def test_plain_count_of_native_rows_for_current_cohort(self) -> None:
        self.assertEqual(public_course_registration_count(self.campaign).count, 0)

        CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="one@example.com"
        )
        CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="two@example.com"
        )

        self.assertEqual(public_course_registration_count(self.campaign).count, 2)

    def test_count_is_specific_to_campaign_and_cohort(self) -> None:
        CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="one@example.com"
        )
        other_cohort = Cohort.objects.create(
            slug="synthetic-other-cohort",
            title="Other cohort",
            description="Other deterministic cohort.",
        )
        other_campaign = RegistrationCampaign.objects.create(
            slug="synthetic-other-campaign",
            title="Other campaign",
            current_course=other_cohort,
        )
        CourseRegistration.objects.create(
            campaign=other_campaign, course=other_cohort, email="two@example.com"
        )
        CourseRegistration.objects.create(
            campaign=other_campaign, course=other_cohort, email="three@example.com"
        )

        self.assertEqual(public_course_registration_count(self.campaign).count, 1)
        self.assertEqual(public_course_registration_count(other_campaign).count, 2)

    def test_baseline_plus_native_combines_once(self) -> None:
        self.campaign.registration_baseline_cohort = self.cohort
        self.campaign.registration_baseline_count = 5
        self.campaign.registration_native_start_at = NATIVE_START
        self.campaign.save(
            update_fields=(
                "registration_baseline_cohort",
                "registration_baseline_count",
                "registration_native_start_at",
                "updated_at",
            )
        )
        native_one = CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="native-one@example.com"
        )
        native_two = CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="native-two@example.com"
        )
        CourseRegistration.objects.filter(pk=native_one.pk).update(
            created_at=NATIVE_START + timedelta(days=1)
        )
        CourseRegistration.objects.filter(pk=native_two.pk).update(
            created_at=NATIVE_START + timedelta(days=2)
        )

        self.assertEqual(public_course_registration_count(self.campaign).count, 7)

    def test_rows_before_the_native_cutover_are_not_double_counted(self) -> None:
        self.campaign.registration_baseline_cohort = self.cohort
        self.campaign.registration_baseline_count = 5
        self.campaign.registration_native_start_at = NATIVE_START
        self.campaign.save(
            update_fields=(
                "registration_baseline_cohort",
                "registration_baseline_count",
                "registration_native_start_at",
                "updated_at",
            )
        )
        # A row that was itself part of what the recorded baseline already
        # covers (for example a backfilled historical row) must not be
        # counted a second time.
        pre_cutover = CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="pre-cutover@example.com"
        )
        CourseRegistration.objects.filter(pk=pre_cutover.pk).update(
            created_at=NATIVE_START - timedelta(days=1)
        )

        self.assertEqual(public_course_registration_count(self.campaign).count, 5)

    def test_baseline_does_not_carry_onto_a_rotated_cohort(self) -> None:
        self.campaign.registration_baseline_cohort = self.cohort
        self.campaign.registration_baseline_count = 5
        self.campaign.registration_native_start_at = NATIVE_START
        self.campaign.save(
            update_fields=(
                "registration_baseline_cohort",
                "registration_baseline_count",
                "registration_native_start_at",
                "updated_at",
            )
        )
        next_cohort = Cohort.objects.create(
            slug="synthetic-cohort-2027",
            title="Synthetic cohort 2027",
            description="The next edition.",
        )
        self.campaign.current_course = next_cohort
        self.campaign.save(update_fields=("current_course", "updated_at"))
        CourseRegistration.objects.create(
            campaign=self.campaign, course=next_cohort, email="next-edition@example.com"
        )

        # Only the new edition's own native row counts -- the old baseline
        # was recorded for the previous cohort and stays with it.
        self.assertEqual(public_course_registration_count(self.campaign).count, 1)

    def test_closed_campaign_counts_only_visible_rows_attributable_to_the_family(self) -> None:
        family = self.cohort.course
        hidden = Cohort.objects.create(
            course=family,
            slug="synthetic-hidden-2025",
            identifier="2025",
            year=2025,
            title="Hidden cohort",
            visible=False,
        )
        other = Cohort.objects.create(
            slug="synthetic-other-family-2026",
            identifier="2026",
            title="Other family cohort",
        )
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))
        for email, cohort in (
            ("visible@example.com", self.cohort),
            ("hidden@example.com", hidden),
            ("other@example.com", other),
            ("unattributed@example.com", None),
        ):
            CourseRegistration.objects.create(
                campaign=self.campaign,
                course=cohort,
                email=email,
            )

        count = public_family_registration_count(self.campaign, family)

        self.assertIsNotNone(count)
        self.assertEqual(count.count, 1)

    def test_closed_campaign_applies_family_baseline_cutover_without_double_counting(self) -> None:
        family = self.cohort.course
        other_visible = Cohort.objects.create(
            course=family,
            slug="synthetic-cohort-2025",
            identifier="2025",
            year=2025,
            title="Earlier visible cohort",
        )
        self.campaign.current_course = None
        self.campaign.registration_baseline_cohort = self.cohort
        self.campaign.registration_baseline_count = 5
        self.campaign.registration_native_start_at = NATIVE_START
        self.campaign.save()
        pre_cutover = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.cohort,
            email="pre@example.com",
        )
        post_cutover = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.cohort,
            email="post@example.com",
        )
        earlier_cohort = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=other_visible,
            email="earlier@example.com",
        )
        CourseRegistration.objects.filter(pk=pre_cutover.pk).update(
            created_at=NATIVE_START - timedelta(days=1)
        )
        CourseRegistration.objects.filter(pk=post_cutover.pk).update(
            created_at=NATIVE_START + timedelta(days=1)
        )
        CourseRegistration.objects.filter(pk=earlier_cohort.pk).update(
            created_at=NATIVE_START - timedelta(days=1)
        )

        count = public_family_registration_count(self.campaign, family)

        self.assertIsNotNone(count)
        self.assertEqual(count.count, 7)

    def test_closed_campaign_without_attributable_rows_or_baseline_returns_none(self) -> None:
        self.campaign.current_course = None
        self.campaign.save(update_fields=("current_course", "updated_at"))

        self.assertIsNone(
            public_family_registration_count(self.campaign, self.cohort.course)
        )

    def test_active_campaign_sums_every_visible_cohort_not_just_the_current_one(self) -> None:
        """A campaign currently promoting one edition still reports family-wide.

        Regression test: an earlier version of this function returned the
        current cohort's own count alone whenever ``current_course`` was set,
        undercounting a family whose registration campaign is open.
        """

        family = self.cohort.course
        earlier_cohort = Cohort.objects.create(
            course=family,
            slug="synthetic-cohort-2025",
            identifier="2025",
            year=2025,
            title="Earlier visible cohort",
        )
        hidden_cohort = Cohort.objects.create(
            course=family,
            slug="synthetic-hidden-2024",
            identifier="2024",
            year=2024,
            title="Hidden cohort",
            visible=False,
        )
        # self.campaign.current_course is self.cohort -- an open campaign.
        CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="current@example.com"
        )
        CourseRegistration.objects.create(
            campaign=self.campaign, course=earlier_cohort, email="earlier-one@example.com"
        )
        CourseRegistration.objects.create(
            campaign=self.campaign, course=earlier_cohort, email="earlier-two@example.com"
        )
        CourseRegistration.objects.create(
            campaign=self.campaign, course=hidden_cohort, email="hidden@example.com"
        )

        count = public_family_registration_count(self.campaign, family)

        self.assertIsNotNone(count)
        self.assertEqual(count.count, 3)
        # The single-cohort count stays scoped to the current edition alone.
        self.assertEqual(public_course_registration_count(self.campaign).count, 1)

    def test_active_campaign_still_applies_the_current_cohorts_own_baseline(self) -> None:
        family = self.cohort.course
        self.campaign.registration_baseline_cohort = self.cohort
        self.campaign.registration_baseline_count = 5
        self.campaign.registration_native_start_at = NATIVE_START
        self.campaign.save()
        native = CourseRegistration.objects.create(
            campaign=self.campaign, course=self.cohort, email="native@example.com"
        )
        CourseRegistration.objects.filter(pk=native.pk).update(
            created_at=NATIVE_START + timedelta(days=1)
        )

        count = public_family_registration_count(self.campaign, family)

        self.assertIsNotNone(count)
        self.assertEqual(count.count, 6)
