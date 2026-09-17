"""Tests for enrollment certificate update API views."""

import json

from accounts.models import AccountIdentityAlias, CustomUser
from courses.models import Cohort, Enrollment

from .enrollment_base import (
    CertificateUpdateExpectation,
    EnrollmentDataAPIBase,
)


def assert_bulk_certificate_error_codes(test_case, result):
    error_codes = set()
    for error in result["errors"]:
        error_codes.add(error["code"])
    test_case.assertEqual(
        error_codes,
        {"missing_fields", "not_enrolled", "user_not_found"},
    )


def assert_mixed_certificate_urls(test_case, second_enrollment):
    test_case.assert_certificate_url(test_case.enrollment, "/certificates/first.pdf")
    test_case.assert_certificate_url(second_enrollment, "/certificates/second.pdf")


def assert_certificates_reject_get(test_case):
    url = test_case.certificate_url()

    response = test_case.client.get(url)

    test_case.assertEqual(response.status_code, 405)


class EnrollmentCertificateMixedBulkUpdateAPITestCase(EnrollmentDataAPIBase):
    def test_bulk_update_enrollment_certificates_view(self):
        second_user, second_enrollment = self.create_enrolled_user(
            "seconduser", "second@example.com"
        )
        other_user = self.create_other_user()
        data = self.mixed_certificate_payload(second_user, other_user)

        response = self.post_certificates(data)

        self.assertEqual(response.status_code, 200)
        result = response.json()
        expectation = CertificateUpdateExpectation(
            result=result,
            success=False,
            updated_count=2,
            error_count=3,
        )
        self.assert_certificate_update_result(expectation)
        assert_bulk_certificate_error_codes(self, result)
        assert_mixed_certificate_urls(self, second_enrollment)
        assert_certificates_reject_get(self)


class EnrollmentCertificateArrayPayloadAPITestCase(EnrollmentDataAPIBase):
    def test_bulk_update_enrollment_certificates_accepts_array_payload(
        self,
    ):
        data = [
            {
                "email": self.user.email,
                "certificate_path": "/certificates/array.pdf",
            }
        ]

        response = self.post_certificates(data)

        self.assertEqual(response.status_code, 200)
        result = response.json()
        expectation = CertificateUpdateExpectation(
            result=result,
            success=True,
            updated_count=1,
            error_count=0,
        )
        self.assert_certificate_update_result(expectation)
        self.assert_certificate_url(self.enrollment, "/certificates/array.pdf")


class EnrollmentCertificateIdentityAPITestCase(EnrollmentDataAPIBase):
    def certificate_update(self, email, path="/certificates/identity.pdf"):
        return {"email": email, "certificate_path": path}

    def create_alias(self, source, survivor):
        return AccountIdentityAlias.objects.create(
            source_user_id=source.pk,
            survivor=survivor,
            source_snapshot_id="a" * 64,
            mapping_checksum="b" * 64,
            review_reference="issue-234-test",
        )

    def error_for(self, response):
        result = response.json()
        self.assertFalse(result["success"])
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["error_count"], 1)
        return result["errors"][0]

    def test_normalized_email_updates_requested_cohort_and_preserves_input(self):
        original_email = "  TESTUSER@Example.COM  "
        other_cohort = Cohort.objects.create(
            title="Other Course",
            slug="other-course",
        )
        other_enrollment = Enrollment.objects.create(
            student=self.user,
            course=other_cohort,
            certificate_url="/certificates/other-old.pdf",
        )

        response = self.post_certificates([self.certificate_update(original_email)])

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["success"])
        self.assertEqual(result["updated"][0]["email"], original_email)
        self.assert_certificate_url(
            self.enrollment,
            "/certificates/identity.pdf",
        )
        self.assert_certificate_url(
            other_enrollment,
            "/certificates/other-old.pdf",
        )

    def test_absorbed_identity_updates_only_survivor_enrollment(self):
        source = CustomUser.objects.create(
            username="absorbed-source",
            email="former@example.com",
            identity_state=CustomUser.IdentityState.ABSORBED,
        )
        self.user.identity_state = CustomUser.IdentityState.ACTIVE
        self.user.save(update_fields=("identity_state",))
        self.create_alias(source, self.user)

        response = self.post_certificates([self.certificate_update(" FORMER@EXAMPLE.COM ")])

        self.assertTrue(response.json()["success"])
        self.assert_certificate_url(
            self.enrollment,
            "/certificates/identity.pdf",
        )

    def test_case_collision_is_ambiguous_and_changes_nothing(self):
        collision = CustomUser.objects.create(
            username="collision",
            email="different@example.com",
        )
        CustomUser.objects.filter(pk=collision.pk).update(
            normalized_email=self.user.normalized_email
        )

        response = self.post_certificates([self.certificate_update("TESTUSER@example.com")])

        error = self.error_for(response)
        self.assertEqual(error["code"], "identity_ambiguous")
        self.assertEqual(error["email"], "TESTUSER@example.com")
        self.assertNotIn(str(self.user.pk), error["error"])
        self.assert_certificate_url(self.enrollment, None)

    def test_unavailable_and_conflicting_absorbed_identity_fail_closed(self):
        source = CustomUser.objects.create(
            username="source",
            email="former@example.com",
            identity_state=CustomUser.IdentityState.ABSORBED,
        )
        self.user.identity_state = CustomUser.IdentityState.ACTIVE
        self.user.save(update_fields=("identity_state",))
        self.create_alias(source, self.user)
        source_enrollment = Enrollment.objects.create(
            student=source,
            course=self.course,
        )

        response = self.post_certificates([self.certificate_update("former@example.com")])

        error = self.error_for(response)
        self.assertEqual(error["code"], "identity_unavailable")
        self.assertNotIn(str(source.pk), error["error"])
        self.assert_certificate_url(self.enrollment, None)
        self.assert_certificate_url(source_enrollment, None)

    def test_quarantined_and_inactive_identities_are_unavailable(self):
        scenarios = (
            ("quarantined", CustomUser.IdentityState.QUARANTINED, True),
            ("inactive", CustomUser.IdentityState.LEGACY, False),
        )
        for username, state, is_active in scenarios:
            unavailable_user = CustomUser.objects.create(
                username=username,
                email=f"{username}@example.com",
                identity_state=state,
                is_active=is_active,
            )
            unavailable_enrollment = Enrollment.objects.create(
                student=unavailable_user,
                course=self.course,
            )

            response = self.post_certificates([self.certificate_update(unavailable_user.email)])

            with self.subTest(state=state, is_active=is_active):
                error = self.error_for(response)
                self.assertEqual(error["code"], "identity_unavailable")
                self.assert_certificate_url(unavailable_enrollment, None)

    def test_mixed_batch_is_independent_correlated_and_redacted(self):
        CustomUser.objects.create(
            username="unavailable",
            email="Stored.Unavailable@example.com",
            identity_state=CustomUser.IdentityState.QUARANTINED,
        )
        CustomUser.objects.create(
            username="not-enrolled",
            email="not-enrolled@example.com",
        )
        payload = [
            self.certificate_update(
                " TESTUSER@EXAMPLE.COM ",
                "/certificates/valid.pdf",
            ),
            self.certificate_update("stored.unavailable@example.com"),
            self.certificate_update("not-enrolled@example.com"),
            {"email": "malformed@example.com"},
        ]

        response = self.post_certificates(payload)

        result = response.json()
        self.assertFalse(result["success"])
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["error_count"], 3)
        self.assertEqual(result["updated"][0]["index"], 0)
        self.assertEqual(result["updated"][0]["email"], payload[0]["email"])
        self.assertEqual(
            [(error["index"], error["code"]) for error in result["errors"]],
            [
                (3, "missing_fields"),
                (1, "identity_unavailable"),
                (2, "not_enrolled"),
            ],
        )
        serialized_errors = json.dumps(result["errors"])
        self.assertNotIn("Stored.Unavailable", serialized_errors)
        self.assertEqual(
            set(result["errors"][1]),
            {"index", "email", "code", "error"},
        )
        self.assertEqual(
            result["errors"][1]["error"],
            "Account identity is unavailable",
        )
        self.assert_certificate_url(
            self.enrollment,
            "/certificates/valid.pdf",
        )

    def test_missing_user_and_authorization_contracts_remain_safe(self):
        missing_response = self.post_certificates([self.certificate_update("missing@example.com")])
        missing_error = self.error_for(missing_response)
        self.assertEqual(missing_error["code"], "user_not_found")

        self.client.defaults.pop("HTTP_AUTHORIZATION")
        unauthorized_response = self.post_certificates([self.certificate_update(self.user.email)])

        self.assertEqual(unauthorized_response.status_code, 401)
        self.assert_certificate_url(self.enrollment, None)
