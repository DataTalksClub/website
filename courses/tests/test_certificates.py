from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from courses.models.certificate import CertificateTemplate
from courses.models.cohort import Cohort, Course, Enrollment, User
from courses.models.project import Project, ProjectSubmission
from courses.services import certificates as svc

RENDER = override_settings(
    BANNER_GENERATOR_URL="https://renderer.invalid/", BANNER_GENERATOR_TOKEN="t"
)


def _response(content=b"%PDF-1.7 fake", status=200):
    response = MagicMock()
    response.status_code = status
    response.content = content
    return response


class CertificateFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(slug="ai-dev-tools", title="AI Dev Tools Zoomcamp")
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            identifier="2026",
            slug="ai-dev-tools-2026",
            title="AI Dev Tools Zoomcamp 2026",
            year=2026,
            min_projects_to_pass=1,
        )
        CertificateTemplate.objects.create(course=cls.course)
        projects = []
        for slug, due in (
            ("project1", datetime(2026, 10, 26, 23, tzinfo=dt_timezone.utc)),
            ("project2", datetime(2026, 11, 16, 23, tzinfo=dt_timezone.utc)),
        ):
            projects.append(
                Project.objects.create(
                    course=cls.cohort,
                    slug=slug,
                    title=slug,
                    submission_due_date=due,
                    peer_review_due_date=due + timedelta(days=8),
                    evaluation_criteria=[{"id": 1}],
                )
            )
        cls.graduate = cls._enroll("grad@example.com", "Ada Lovelace", projects[0], passed=True)
        cls._enroll("fail@example.com", "Not Passed", projects[0], passed=False)

    @classmethod
    def _enroll(cls, email, name, project, *, passed):
        user = User.objects.create_user(username=email, email=email)
        enrollment = Enrollment.objects.create(
            student=user, course=cls.cohort, certificate_name=name
        )
        ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/x/y",
            commit_id="abc",
            passed=passed,
        )
        return enrollment


class CertificateServiceTests(CertificateFixture):
    def test_date_is_last_project_deadline_in_template_timezone(self):
        # 2026-11-16 23:00 UTC is already 17 November in Berlin.
        self.assertEqual(
            svc.certificate_date(self.cohort, svc.get_template(self.cohort)), "17 November 2026"
        )

    def test_graduates_are_only_those_who_passed(self):
        people = svc.graduates(self.cohort)
        self.assertEqual([p.name for p in people], ["Ada Lovelace"])
        # Legacy scheme: sha1(email + "_")
        self.assertEqual(people[0].certificate_id, svc.certificate_hash("grad@example.com"))

    @RENDER
    def test_preview_posts_template_size_and_date_and_publishes_nothing(self):
        with patch.object(svc.requests, "post", return_value=_response()) as post:
            content = svc.preview_certificate(self.cohort, "Rick Astley")
        self.assertEqual(content, b"%PDF-1.7 fake")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["template"], "dtc-zoomcamp-certificate")
        self.assertEqual((payload["width"], payload["height"]), (1920, 1080))
        self.assertEqual(payload["data"]["name"], "Rick Astley")
        self.assertEqual(payload["data"]["dates"], "17 November 2026")
        self.assertEqual(payload["data"]["certificate_url"], svc.PREVIEW_URL)
        self.assertTrue(payload["data"]["qr"].startswith("data:image/svg+xml;base64,"))
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer t"})
        self.assertFalse(Enrollment.objects.exclude(certificate_url=None).exists())

    def test_preview_without_renderer_config_is_a_clear_error(self):
        with self.assertRaises(svc.CertificateError) as caught:
            svc.preview_certificate(self.cohort, "Rick Astley")
        self.assertEqual(caught.exception.code, "renderer_not_configured")

    @RENDER
    def test_renderer_failure_is_reported(self):
        with patch.object(svc.requests, "post", return_value=_response(b"x", 500)):
            with self.assertRaises(svc.CertificateError) as caught:
                svc.preview_certificate(self.cohort, "Rick Astley")
        self.assertEqual(caught.exception.code, "renderer_error")

    @RENDER
    def test_issue_without_confirm_is_a_dry_run(self):
        with patch.object(svc.requests, "post") as post:
            result = svc.issue_service({}, cohort_slug=self.cohort.slug)
        post.assert_not_called()
        self.assertFalse(result["executed"])
        self.assertEqual(result["graduate_count"], 1)

    @RENDER
    def test_issue_with_confirm_publishes_pdf_and_png_and_records_url(self):
        s3 = MagicMock()
        with patch.object(svc.requests, "post", return_value=_response()):
            with patch.object(svc, "_s3_client", return_value=s3):
                result = svc.issue_service(
                    {"confirm": self.cohort.slug}, cohort_slug=self.cohort.slug
                )
        self.assertTrue(result["executed"])
        keys = sorted(call.kwargs["Key"] for call in s3.put_object.call_args_list)
        cert_id = svc.certificate_hash("grad@example.com")
        self.assertEqual(
            keys, [f"ai-dev-tools/2026/{cert_id}.pdf", f"ai-dev-tools/2026/{cert_id}.png"]
        )
        self.assertEqual(s3.put_object.call_args.kwargs["Bucket"], "certificate.datatalks.club")
        self.graduate.refresh_from_db()
        self.assertEqual(
            self.graduate.certificate_url,
            f"https://certificate.datatalks.club/ai-dev-tools/2026/{cert_id}.pdf",
        )

    @override_settings(CMP_STAFF_API_TOKEN="cmp", CMP_BASE_URL="https://cmp.invalid")
    def test_cmp_writeback_uses_legacy_payload(self):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200)
        issued = [{"enrollment_id": self.graduate.pk, "name": "Ada", "url": "https://c/x.pdf"}]
        svc.write_urls_to_cmp(self.cohort, issued, session=session)
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://cmp.invalid/api/courses/ai-dev-tools-2026/certificates")
        self.assertEqual(
            kwargs["json"],
            {
                "certificates": [
                    {"email": "grad@example.com", "certificate_path": "https://c/x.pdf"}
                ]
            },
        )


class CertificateStudioTests(CertificateFixture):
    def setUp(self):
        staff = User.objects.create_user(
            username="cert-staff", email="s@example.com", password="pw", is_staff=True
        )
        staff.user_permissions.set(
            Permission.objects.filter(
                codename__in=["view_certificatetemplate", "change_certificatetemplate"]
            )
        )
        self.client.force_login(staff)

    def test_page_lists_cohort_with_date_and_preview_button(self):
        response = self.client.get("/studio/certificates/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "17 November 2026")
        self.assertContains(response, "Preview certificate")
        self.assertContains(response, "Rick Astley")

    @RENDER
    def test_preview_button_returns_a_pdf(self):
        with patch.object(svc.requests, "post", return_value=_response()):
            response = self.client.post(
                "/studio/certificates/ai-dev-tools-2026/preview/", {"name": "Rick Astley"}
            )
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_generate_without_confirmation_issues_nothing(self):
        with patch.object(svc.requests, "post") as post:
            response = self.client.post("/studio/certificates/ai-dev-tools-2026/issue/", {})
        self.assertEqual(response.status_code, 302)
        post.assert_not_called()

    def test_non_staff_is_denied(self):
        self.client.logout()
        self.assertNotEqual(self.client.get("/studio/certificates/").status_code, 200)
