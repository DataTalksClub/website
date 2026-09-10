"""BE-02 boundary tests for compatibility-API staff authority.

Staff operations must derive from a scoped, expiring, revocable
``management_auth`` credential -- never from a legacy raw ``Token`` row or
the linked user's ``is_staff`` flag.  Learner-level compatibility access is
unchanged.
"""

from datetime import timedelta

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import CustomUser, Token
from accounts.studio_roles import set_single_studio_role
from api.tests.staff_credentials import issue_staff_bearer
from courses.models import Cohort
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token

STAFF_EXPORT_URL = "/api/courses/test-course/graduates"
LEARNER_LIST_URL = "/api/courses/test-course/homeworks/"


def make_credential(principal, scopes):
    token = generate_token()
    APICredential.objects.create(
        principal=principal,
        name="boundary test credential",
        prefix=token.prefix,
        secret_digest=encode_secret(token.secret),
        digest_algorithm="pbkdf2_sha256",
        digest_version=1,
        scopes=list(scopes),
        expires_at=timezone.now() + timedelta(days=1),
    )
    return f"Bearer {token.raw}"


class StaffAuthorityBoundaryTest(TestCase):
    def setUp(self):
        self.staff = CustomUser.objects.create(
            username="legacy-staff",
            email="legacy-staff@example.com",
            is_staff=True,
        )
        self.legacy_token = Token.objects.create(user=self.staff)
        self.course = Cohort.objects.create(
            title="Test Course",
            slug="test-course",
            description="Test",
        )

    def graduates(self, auth):
        return Client().get(STAFF_EXPORT_URL, HTTP_AUTHORIZATION=auth)

    def test_staff_flag_no_longer_authorizes_legacy_token(self):
        response = self.graduates(f"Token {self.legacy_token.key}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "staff_token_required")

    def test_legacy_token_keeps_learner_level_access(self):
        response = Client().get(
            LEARNER_LIST_URL,
            HTTP_AUTHORIZATION=f"Token {self.legacy_token.key}",
        )

        self.assertEqual(response.status_code, 200)

    def test_scoped_bearer_with_course_operator_role_grants_access(self):
        response = self.graduates(issue_staff_bearer(self.staff))

        self.assertEqual(response.status_code, 200)

    def test_bearer_without_course_operations_scope_denied(self):
        principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="boundary:no-scope",
            identity_snapshot="boundary:no-scope",
            user=self.staff,
        )
        set_single_studio_role(self.staff, "course_operator")

        auth = make_credential(principal, scopes=["studio.home.read"])

        response = self.graduates(auth)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "staff_token_required")

    def test_staff_flagged_human_without_studio_role_denied(self):
        principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="boundary:no-role",
            identity_snapshot="boundary:no-role",
            user=self.staff,
        )

        auth = make_credential(principal, scopes=["compatibility.course_operations"])

        response = self.graduates(auth)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "staff_token_required")

    def test_service_principal_with_permission_and_scope_grants_access(self):
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="boundary:service",
            identity_snapshot="service:boundary",
            permissions=(permission,),
        )

        auth = make_credential(principal, scopes=["compatibility.course_operations"])

        response = self.graduates(auth)
        self.assertEqual(response.status_code, 200)

    def test_revoked_credential_denied_with_generic_invalid_token(self):
        auth = issue_staff_bearer(self.staff)
        APICredential.objects.update(revoked_at=timezone.now())

        response = self.graduates(auth)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})
