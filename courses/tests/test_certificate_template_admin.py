from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from courses.models.certificate import CertificateTemplate
from courses.models.cohort import Cohort, Course, User
from courses.models.project import Project
from management_auth.constants import DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token


class CertificateTemplateAdminApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(slug="ai-dev-tools", title="AI Dev Tools Zoomcamp")
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            identifier="2026",
            slug="ai-dev-tools-2026",
            title="AI Dev Tools Zoomcamp 2026",
            year=2026,
        )
        for slug, due in (
            ("project1", datetime(2026, 10, 26, 23, tzinfo=dt_timezone.utc)),
            ("project2", datetime(2026, 11, 16, 23, tzinfo=dt_timezone.utc)),
        ):
            Project.objects.create(
                course=cls.cohort,
                slug=slug,
                title=slug,
                submission_due_date=due,
                peer_review_due_date=due + timedelta(days=8),
                evaluation_criteria=[{"id": 1}],
            )
        staff = User.objects.create_user(
            username="cert-staff", email="cert-staff@example.com", password="pw", is_staff=True
        )
        perms = Permission.objects.filter(
            codename__in=["view_certificatetemplate", "change_certificatetemplate"]
        )
        staff.user_permissions.set(perms)
        principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="cert-cli",
            identity_snapshot="cert-cli",
            user=staff,
            created_by=staff,
        )
        principal.permissions.set(perms)
        generated = generate_token()
        APICredential.objects.create(
            principal=principal,
            name="cert-token",
            prefix=generated.prefix,
            digest_algorithm=DIGEST_ALGORITHM,
            digest_version=DIGEST_VERSION,
            secret_digest=encode_secret(generated.secret),
            scopes=["courses.certificate_template.read", "courses.certificate_template.write"],
            expires_at=timezone.now() + timedelta(days=1),
            created_by=staff,
        )
        cls.headers = {"Authorization": f"Bearer {generated.raw}"}

    def url(self, slug="ai-dev-tools-2026"):
        return f"/api/v1/admin/courses/{slug}/certificate-template"

    def test_read_without_template_is_404(self):
        response = self.client.get(self.url(), headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "no_certificate_template")

    def test_unknown_course_is_404(self):
        response = self.client.get(self.url("nope"), headers=self.headers)
        self.assertEqual(response.json()["error"]["code"], "unknown_course")

    def test_write_then_read_reports_last_project_deadline(self):
        write = self.client.put(
            self.url("ai-dev-tools"),
            data='{"banner_template": "dtc-zoomcamp-certificate"}',
            content_type="application/json",
            headers=self.headers,
        )
        self.assertEqual(write.status_code, 200, write.content)
        self.assertTrue(CertificateTemplate.objects.filter(course=self.course).exists())

        body = self.client.get(self.url(), headers=self.headers).json()
        self.assertEqual(body["banner_size"], "zoomcamp-certificate")
        self.assertEqual(body["cohort"]["title"], "AI Dev Tools Zoomcamp 2026")
        self.assertEqual(body["cohort"]["s3_prefix"], "ai-dev-tools/2026")
        self.assertEqual(body["last_project_deadline"], "2026-11-16T23:00:00+00:00")

    def test_family_slug_read_has_no_cohort_fields(self):
        CertificateTemplate.objects.create(course=self.course)
        body = self.client.get(self.url("ai-dev-tools"), headers=self.headers).json()
        self.assertIsNone(body["cohort"])
        self.assertIsNone(body["last_project_deadline"])

    def test_write_rejects_unknown_field_and_bad_timezone(self):
        for payload, code in (
            ('{"nope": "x"}', "unknown_field"),
            ('{"date_timezone": "Mars/Base"}', "validation_error"),
        ):
            response = self.client.put(
                self.url(), data=payload, content_type="application/json", headers=self.headers
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], code)

    def test_missing_scope_is_denied(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)
