from datetime import timedelta

from django.contrib import admin
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Course,
    DeliveryMode,
    Enrollment,
    ProjectSubmission,
    RegistrationCampaign,
    SharedCurriculum,
    SharedModule,
    User,
)
from test_support.course_catalog import make_cohort


class CourseFamilyLandingTests(TestCase):
    def setUp(self):
        self.family = Course.objects.create(
            slug="practical-course",
            title="Practical Course",
            starting_point="You have a dataset and a question.",
            outcome="Build and publish a useful prediction service.",
            github_repo_url="https://github.com/example/practical-course",
        )
        self.cohort = make_cohort(self.family, 2026, project_count=1)
        self.project = self.cohort.project_set.get()
        self.project.title = "Attempt 1"
        self.project.save(update_fields=["title"])
        self.curriculum = SharedCurriculum.objects.create(course=self.family)
        for position, title in enumerate(
            ["Prepare the data", "Train a model", "Evaluate predictions", "Deploy the service"]
        ):
            SharedModule.objects.create(
                curriculum=self.curriculum,
                position=position,
                slug=f"skill-{position}",
                title=title,
            )
        self.campaign = RegistrationCampaign.objects.create(
            slug="practical-course",
            title=self.family.title,
            current_course=self.cohort,
        )
        self.url = reverse("course_family", args=[self.family.slug])

    def add_submission(self, cohort=None, *, volunteer=False):
        cohort = cohort or self.cohort
        user = User.objects.create_user(username=f"learner-{cohort.pk}")
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        return ProjectSubmission.objects.create(
            project=cohort.project_set.first(),
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/example/learner-project",
            volunteer_review_only=volunteer,
        )

    def test_registration_opens_the_page_and_real_evidence_closes_it(self):
        self.add_submission()

        response = self.client.get(self.url)
        body = response.content.decode()

        self.assertContains(response, self.family.starting_point)
        self.assertContains(response, self.family.outcome)
        # 2026-09: the cohort's own registration card opens the content column
        # because it's the fact a visitor lands on the page to check. The
        # "From learning to building" preview was dropped -- it only repeated
        # the full Syllabus section further down -- so the "what people have
        # built" proof now closes the page right after that syllabus.
        self.assertLess(body.index("Your starting point:"), body.index('id="register-heading"'))
        self.assertLess(body.index('id="register-heading"'), body.index("Explore learner projects"))
        self.assertContains(response, reverse("family_projects", args=[self.family.slug]))
        self.assertContains(response, reverse("registration_campaign", args=[self.campaign.slug]))
        self.assertNotContains(response, "What people build")
        self.assertNotContains(response, 'class="family-built-grid"')

    def test_unpublished_retired_and_other_course_skills_are_not_promoted(self):
        modules = list(self.curriculum.modules.order_by("position"))
        modules[0].published = False
        modules[0].save(update_fields=["published"])
        modules[1].retired_at = timezone.now()
        modules[1].save(update_fields=["retired_at"])
        other = Course.objects.create(slug="other", title="Other")
        other_curriculum = SharedCurriculum.objects.create(course=other)
        SharedModule.objects.create(
            curriculum=other_curriculum, position=0, slug="other-skill", title="Other skill"
        )

        response = self.client.get(self.url)

        self.assertNotContains(response, "Prepare the data")
        self.assertNotContains(response, "Train a model")
        self.assertNotContains(response, "Other skill")
        self.assertContains(response, "Evaluate predictions")

    def test_learner_proof_requires_a_real_public_nonvolunteer_submission(self):
        self.add_submission(volunteer=True)
        hidden = make_cohort(self.family, 2027, visible=False, project_count=1)
        self.add_submission(hidden)
        other = Course.objects.create(slug="other", title="Other")
        self.add_submission(make_cohort(other, 2026, project_count=1))

        response = self.client.get(self.url)

        self.assertNotContains(response, "Explore learner projects")
        self.assertNotContains(response, "Put it into practice")

    def test_project_brief_uses_the_stored_instructions_destination(self):
        self.project.instructions_url = "https://github.com/example/course/blob/main/project.md"
        self.project.save(update_fields=["instructions_url"])

        response = self.client.get(self.url)

        self.assertContains(response, self.project.instructions_url)
        self.assertContains(response, "Read the 2026 project brief")
        self.assertNotContains(response, "Explore learner projects")

    def test_empty_family_omits_unsupported_narrative_and_proof(self):
        family = Course.objects.create(slug="empty", title="Empty course")
        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertEqual(response.status_code, 200)
        for marker in (
            "Your starting point",
            'id="register-heading"',
            'id="certificate-heading"',
            'id="stories-heading"',
        ):
            with self.subTest(marker=marker):
                self.assertNotContains(response, marker)

    def test_authored_starting_point_and_outcome_are_escaped(self):
        self.family.starting_point = "<script>before()</script>"
        self.family.outcome = "<script>after()</script>"
        self.family.save(update_fields=["starting_point", "outcome"])
        response = self.client.get(self.url)

        self.assertContains(response, "&lt;script&gt;before()&lt;/script&gt;")
        self.assertContains(response, "&lt;script&gt;after()&lt;/script&gt;")
        self.assertNotContains(response, "<script>before()")

    def test_course_description_preserves_learning_notes_and_escapes_markup(self):
        self.family.description = (
            "A practical course.\n\nThis course is educational; results are not guaranteed."
            "\n\n<script>unsafe()</script>"
        )
        self.family.save(update_fields=["description"])
        response = self.client.get(self.url)

        self.assertContains(response, "About this course and learning notes")
        self.assertContains(response, "This course is educational; results are not guaranteed.")
        self.assertNotContains(response, "<script>unsafe()")

    def test_whitespace_starting_point_does_not_leave_an_empty_label(self):
        self.family.starting_point = " \n "
        self.family.save(update_fields=["starting_point"])
        response = self.client.get(self.url)

        self.assertNotContains(response, "Your starting point:")

    def test_self_paced_route_omits_cohort_certificate_promise(self):
        self.cohort.delivery_mode = DeliveryMode.SELF_PACED
        self.cohort.save(update_fields=["delivery_mode"])
        self.campaign.delete()
        response = self.client.get(self.url)

        self.assertContains(response, "Open the self-paced course")
        self.assertContains(response, "no deadlines, grading, or certificate")
        self.assertNotContains(response, 'id="certificate-heading"')

    def test_current_live_cohort_has_conditional_certificate_explanation(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'id="certificate-heading"')
        self.assertContains(response, "Certificate eligibility depends on the 2026")
        self.assertContains(response, "does not earn a certificate")

    def test_finished_cohort_does_not_promise_a_new_certificate(self):
        self.cohort.finished = True
        self.cohort.save(update_fields=["finished"])
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="certificate-heading"')

    def test_materials_only_shared_curriculum_family_links_to_the_platform(self):
        # This family's setUp already imported a SharedCurriculum, so once its
        # only campaign is gone the materials card should keep the visitor on
        # the platform (the first published module) instead of sending them
        # to the source repository -- even though ``github_repo_url`` is set.
        self.campaign.delete()
        response = self.client.get(self.url)

        self.assertContains(response, "Learn the curriculum on the platform")
        self.assertContains(
            response,
            reverse("shared_module", args=[self.family.slug, "skill-0"]),
        )
        self.assertNotContains(response, "Course materials on GitHub")
        self.assertNotContains(response, "Register interest")
        self.assertNotContains(response, "Self-paced · start now")

    def test_materials_only_legacy_family_has_an_honest_github_action(self):
        # A family with no imported SharedCurriculum has no module pages to
        # send a visitor to, so the repository stays the only real materials
        # destination.
        family = Course.objects.create(
            slug="legacy-course",
            title="Legacy Course",
            github_repo_url="https://github.com/example/legacy-course",
        )
        make_cohort(family, 2025)

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertContains(response, "Course materials on GitHub")
        self.assertContains(response, 'href="https://github.com/example/legacy-course"')
        self.assertNotContains(response, "Register interest")
        self.assertNotContains(response, "Self-paced · start now")

    def test_past_cohort_end_date_does_not_promise_a_new_certificate(self):
        self.cohort.end_date = timezone.localdate() - timedelta(days=1)
        self.cohort.save(update_fields=["end_date"])
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="certificate-heading"')

    def test_starting_point_is_editable_through_the_family_admin(self):
        family_admin = admin.site._registry[Course]
        assert family_admin.fields is not None
        self.assertIn("starting_point", family_admin.fields)
