"""Public reading and approval-only participation parity with CMP f663f70a."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    CourseRegistration,
    Enrollment,
    Project,
    ProjectState,
    Question,
    SharedCurriculum,
    SharedLesson,
    SharedModule,
    Submission,
)
from courses.services.enrollment import enroll_learner
from courses.tests.registration_campaign_base import RegistrationCampaignBase


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PublicCourseAuditTests(RegistrationCampaignBase):
    def setUp(self):
        super().setUp()
        self.course.requires_registration_approval = True
        self.course.github_repo_url = "https://github.com/DataTalksClub/course-materials"
        self.course.save()
        self.campaign.requires_approval = True
        self.campaign.save()

    def test_empty_course_stays_public_for_anonymous_and_pending(self):
        response = self.client.get(self.course_url())
        self.assertContains(response, "Apply for this course")
        self.assertContains(response, self.course.github_repo_url)
        user = self.create_registered_course_user()
        self.client.force_login(user)
        response = self.client.get(self.course_url())
        self.assertContains(response, "Application pending review")
        self.assertNotContains(response, "Apply for this course")
        self.assertContains(response, self.course.github_repo_url)
        self.assertFalse(Enrollment.objects.exists())

    def test_family_page_apply_and_pending_states(self):
        url = reverse("course_family", args=[self.course.course.slug])
        self.assertContains(self.client.get(url), "Apply for this course")
        user = self.create_registered_course_user()
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertContains(response, "Application pending review")
        self.assertNotContains(response, "Apply for this course")

    def test_slash_aliases_preserve_query_and_resolve_to_public_canonical(self):
        family_url = reverse("course_family", args=[self.course.course.slug])
        for alias, destination in (
            (f"/courses/{self.course.course.slug}/", family_url),
            (f"/courses/{self.course.slug}/", self.course_url()),
            (f"/{self.course.slug}/", self.course_url()),
            ("/courses/", reverse("course_list")),
        ):
            with self.subTest(alias=alias):
                self.assertRedirects(
                    self.client.get(alias + "?ref=audit"),
                    destination + "?ref=audit",
                    status_code=301,
                )
        self.assertEqual(self.client.get("/courses/unknown-course/").status_code, 404)

    def create_materials(self):
        homework = self.create_intro_homework()
        homework.homework_url_field = False
        homework.due_date = timezone.now() + timedelta(days=7)
        homework.description = "Public homework instructions"
        homework.save()
        Question.objects.create(homework=homework, text="Public audit question", question_type="FF")
        project = Project.objects.create(
            course=self.course,
            slug="project",
            title="Public project instructions",
            state=ProjectState.COLLECTING_SUBMISSIONS.value,
            submission_due_date=timezone.now() + timedelta(days=7),
            peer_review_due_date=timezone.now() + timedelta(days=14),
        )
        return homework, project

    def child_url(self, name, slug):
        return reverse(name, args=[self.course.course.slug, self.course.identifier, slug])

    def test_anonymous_pending_and_rejected_read_materials_without_participating(self):
        homework, project = self.create_materials()
        user = self.create_registered_course_user()
        homework_url = self.child_url("cohort_homework", homework.slug)
        project_url = self.child_url("cohort_project", project.slug)
        enrollment_url = reverse(
            "cohort_enrollment", args=[self.course.course.slug, self.course.identifier]
        )
        for status in (None, "pending", "rejected"):
            with self.subTest(status=status):
                if status:
                    CourseRegistration.objects.filter(user=user).update(status=status)
                    self.client.force_login(user)
                self.assertContains(self.client.get(self.course_url()), self.course.title)
                response = self.client.get(homework_url)
                self.assertContains(response, "Public homework instructions")
                self.assertContains(response, "Public audit question")
                self.assertTrue(response.context["disabled"])
                self.assertContains(self.client.get(project_url), "Public project instructions")
                self.assertEqual(self.client.post(homework_url, {}).status_code, 403)
                self.client.post(project_url, {})
                self.client.post(enrollment_url, {})
                self.assertFalse(Enrollment.objects.exists())
                self.assertFalse(Submission.objects.exists())
                self.assertFalse(project.projectsubmission_set.exists())

    def test_shared_module_and_lesson_are_public_for_anonymous_and_pending(self):
        curriculum = SharedCurriculum.objects.create(
            course=self.course.course, parser_version="audit-test"
        )
        module = SharedModule.objects.create(
            curriculum=curriculum, position=1, slug="01-intro", title="Audit module"
        )
        lesson = SharedLesson.objects.create(
            module=module,
            position=1,
            slug="01-lesson",
            title="Audit lesson",
            rendered_html="<p>Public lesson body</p>",
        )
        user = self.create_registered_course_user()
        for pending in (False, True):
            if pending:
                self.client.force_login(user)
            self.assertContains(
                self.client.get(
                    reverse("shared_module", args=[self.course.course.slug, module.slug])
                ),
                "Audit module",
            )
            self.assertContains(
                self.client.get(
                    reverse(
                        "shared_lesson", args=[self.course.course.slug, module.slug, lesson.slug]
                    )
                ),
                "Public lesson body",
            )
        self.assertFalse(Enrollment.objects.exists())

    def test_approved_can_submit_classic_homework(self):
        homework, _ = self.create_materials()
        user = self.create_registered_course_user()
        CourseRegistration.objects.filter(user=user).update(status="approved")
        self.client.force_login(user)
        response = self.client.post(
            self.child_url("cohort_homework", homework.slug),
            {"time_spent_lectures": "1", "time_spent_homework": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Submission.objects.filter(student=user, homework=homework).exists())
        self.assertTrue(Enrollment.objects.filter(student=user, course=self.course).exists())

    def test_existing_enrollment_does_not_bypass_pending_or_repointed_campaign(self):
        user = self.create_registered_course_user()
        Enrollment.objects.create(student=user, course=self.course)
        self.campaign.current_course = None
        self.campaign.is_active = False
        self.campaign.save()
        with self.assertRaises(PermissionDenied):
            enroll_learner(user, self.course)

    def test_existing_submission_cannot_bypass_approval_for_optional_review(self):
        from accounts.models import User
        from courses.models import PeerReview, ProjectSubmission
        from courses.views.project_eval_actions import _create_optional_peer_review_if_allowed

        _, project = self.create_materials()
        user = self.create_registered_course_user()
        enrollment = Enrollment.objects.create(student=user, course=self.course)
        own = ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/example/own",
            commit_id="abc",
        )
        other = User.objects.create_user(username="review-target", email="target@example.invalid")
        target_enrollment = Enrollment.objects.create(student=other, course=self.course)
        target = ProjectSubmission.objects.create(
            project=project,
            student=other,
            enrollment=target_enrollment,
            github_link="https://github.com/example/target",
            commit_id="abc",
        )
        for status in ("pending", "rejected"):
            CourseRegistration.objects.filter(user=user).update(status=status)
            with self.assertRaises(PermissionDenied):
                _create_optional_peer_review_if_allowed(self.course, project, user, target.pk)
            self.assertFalse(PeerReview.objects.filter(reviewer=own).exists())
        CourseRegistration.objects.filter(user=user).update(status="approved")
        review, created = _create_optional_peer_review_if_allowed(
            self.course, project, user, target.pk
        )
        self.assertTrue(created)
        self.assertEqual(review.reviewer, own)

    def test_pending_homework_preview_has_no_save_controls_or_guidance(self):
        homework, _ = self.create_materials()
        user = self.create_registered_course_user()
        self.client.force_login(user)
        response = self.client.get(self.child_url("cohort_homework", homework.slug))
        self.assertContains(response, "Public audit question")
        self.assertContains(response, "Application pending review")
        self.assertNotContains(response, "Save submission")
        self.assertNotContains(response, "Partial answers are fine")
        self.assertNotContains(response, 'name="time_spent_lectures"')

    def test_pending_open_project_does_not_claim_submission_window_is_closed(self):
        _, project = self.create_materials()
        user = self.create_registered_course_user()
        self.client.force_login(user)
        url = self.child_url("cohort_project", project.slug)
        response = self.client.get(url)
        self.assertContains(response, "Application pending review")
        self.assertNotContains(response, "submission window is closed")
        project.state = ProjectState.PEER_REVIEWING.value
        project.save()
        self.assertContains(self.client.get(url), "submission window is closed")
