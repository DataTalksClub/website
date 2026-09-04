from __future__ import annotations

import json
import uuid
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.development_owner import (
    DEVELOPMENT_AUTOMATION_PRINCIPAL,
    DEVELOPMENT_OWNER_PRINCIPAL,
    DevelopmentOwnerBootstrapDenied,
    bootstrap_development_owner,
)
from accounts.forms import DevelopmentOwnerLoginForm
from accounts.models import Token
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent, StaffSession
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import issue_credential_once

OWNER_EMAIL = "synthetic-owner@example.test"
OWNER_PASSWORD = "safe-development-password-107"
NEW_PASSWORD = "safe-development-password-107-new"


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class DevelopmentOwnerBootstrapTests(TestCase):
    def bootstrap(self, **kwargs):
        return bootstrap_development_owner(
            email=kwargs.pop("email", OWNER_EMAIL),
            password=kwargs.pop("password", OWNER_PASSWORD),
            reset_password=kwargs.pop("reset_password", False),
            allow_test=True,
            **kwargs,
        )

    def test_create_and_reconcile_are_exact_idempotent_and_secret_safe(self) -> None:
        created = self.bootstrap()
        reconciled = self.bootstrap(password=None)

        self.assertEqual((created.category, reconciled.category), ("created", "reconciled"))
        self.assertEqual(get_user_model().objects.count(), 1)
        user = get_user_model().objects.get()
        self.assertTrue(user.check_password(OWNER_PASSWORD))
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {"site_admin"})
        self.assertEqual(
            set(
                Group.objects.get(name="site_admin").permissions.values_list(
                    "content_type__app_label",
                    "codename",
                )
            ),
            {
                ("core", "access_studio"),
                ("core", "browse_audit"),
                ("management_auth", "manage_api_credentials"),
                ("events", "historical_registration_import_manage"),
                ("events", "historical_registration_mapping_manage"),
                ("events", "view_event_qna"),
                ("events", "manage_event_qna"),
                ("core", "read_operational_settings"),
                ("core", "change_operational_settings"),
                ("core", "read_site_navigation"),
                ("core", "change_site_navigation"),
                ("core", "read_sponsors"),
                ("core", "change_sponsors"),
                ("core", "export_sponsors"),
            },
        )

        human = APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN)
        service = APIPrincipal.objects.get(kind=APIPrincipal.Kind.SERVICE)
        self.assertEqual(human.identity_snapshot, DEVELOPMENT_OWNER_PRINCIPAL)
        self.assertEqual(service.identity_snapshot, DEVELOPMENT_AUTOMATION_PRINCIPAL)
        self.assertEqual(
            set(
                human.permissions.values_list(
                    "content_type__app_label",
                    "codename",
                )
            ),
            {
                ("core", "access_studio"),
                ("core", "read_operational_settings"),
                ("core", "change_operational_settings"),
                ("core", "read_site_navigation"),
                ("core", "change_site_navigation"),
                ("core", "read_sponsors"),
                ("core", "change_sponsors"),
                ("core", "export_sponsors"),
                ("management_auth", "manage_api_credentials"),
            },
        )
        self.assertEqual(
            set(
                service.permissions.values_list(
                    "content_type__app_label",
                    "codename",
                )
            ),
            {
                ("core", "access_studio"),
                ("core", "read_operational_settings"),
                ("core", "change_operational_settings"),
                ("core", "read_site_navigation"),
                ("core", "change_site_navigation"),
                ("core", "read_sponsors"),
                ("core", "change_sponsors"),
                ("events", "historical_registration_import_manage"),
                ("events", "historical_registration_mapping_manage"),
            },
        )
        self.assertEqual(APICredential.objects.count(), 0)

        persisted = json.dumps(
            {
                "audits": list(AuditEvent.objects.values()),
                "principals": list(APIPrincipal.objects.values()),
            },
            default=str,
        )
        self.assertNotIn(OWNER_PASSWORD, persisted)
        self.assertNotIn(OWNER_EMAIL, persisted)

    def test_reset_revokes_only_owner_sessions_and_human_credentials(self) -> None:
        self.bootstrap()
        user = get_user_model().objects.get()
        human = APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN)
        service = APIPrincipal.objects.get(kind=APIPrincipal.Kind.SERVICE)
        StaffSession.objects.create(user=user, authenticated_at=user.date_joined)
        owner_credential = issue_credential_once(
            actor_principal=human,
            target_principal_id=human.id,
            name="Owner management credential",
            scopes=("management.credentials.list",),
            idempotency_key="owner-before-reset",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=user,
        )
        service_credential = issue_credential_once(
            actor_principal=human,
            target_principal_id=service.id,
            name="Service health credential",
            scopes=("studio.home.read",),
            idempotency_key="service-before-reset",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=user,
        )

        reset = self.bootstrap(password=NEW_PASSWORD, reset_password=True)

        self.assertEqual(reset.category, "reset")
        self.assertEqual(reset.revoked_staff_sessions, 1)
        self.assertEqual(reset.revoked_human_credentials, 1)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertFalse(user.check_password(OWNER_PASSWORD))
        self.assertIsNotNone(
            APICredential.objects.get(id=owner_credential.response["credential_id"]).revoked_at
        )
        self.assertIsNone(
            APICredential.objects.get(id=service_credential.response["credential_id"]).revoked_at
        )

    def test_environment_conflicts_and_second_owner_fail_closed(self) -> None:
        with override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION):
            with self.assertRaises(DevelopmentOwnerBootstrapDenied) as environment:
                bootstrap_development_owner(
                    email=OWNER_EMAIL,
                    password=OWNER_PASSWORD,
                    reset_password=False,
                )
        self.assertEqual(environment.exception.category, "environment_denied")
        self.assertEqual(get_user_model().objects.count(), 0)

        self.bootstrap()
        baseline = (
            get_user_model().objects.count(),
            APIPrincipal.objects.count(),
            APICredential.objects.count(),
        )
        with self.assertRaises(DevelopmentOwnerBootstrapDenied) as second:
            self.bootstrap(email="second-owner@example.test")
        self.assertEqual(second.exception.category, "second_owner_denied")
        self.assertEqual(
            (
                get_user_model().objects.count(),
                APIPrincipal.objects.count(),
                APICredential.objects.count(),
            ),
            baseline,
        )

    def test_command_refuses_noninteractive_input_without_reading_a_secret(self) -> None:
        stdin = Mock()
        stdin.isatty.return_value = False
        stderr = Mock()
        stderr.isatty.return_value = True
        output = StringIO()
        with (
            override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.DEVELOPMENT),
            patch(
                "accounts.management.commands.bootstrap_development_owner.sys.stdin",
                stdin,
            ),
            patch(
                "accounts.management.commands.bootstrap_development_owner.sys.stderr",
                stderr,
            ),
            self.assertRaises(CommandError) as denied,
        ):
            call_command("bootstrap_development_owner", stdout=output)
        self.assertEqual(
            str(denied.exception),
            "bootstrap_development_owner: noninteractive_denied",
        )
        self.assertNotIn(OWNER_PASSWORD, output.getvalue())
        stdin.readline.assert_not_called()

    def test_interactive_command_outputs_only_safe_fixed_counts(self) -> None:
        stdin = Mock()
        stdin.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        output = StringIO()
        with (
            override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.DEVELOPMENT),
            patch(
                "accounts.management.commands.bootstrap_development_owner.sys.stdin",
                stdin,
            ),
            patch(
                "accounts.management.commands.bootstrap_development_owner.sys.stderr",
                stderr,
            ),
            patch(
                "accounts.management.commands.bootstrap_development_owner.input",
                side_effect=(OWNER_EMAIL, OWNER_EMAIL, "y"),
            ),
            patch(
                "accounts.management.commands.bootstrap_development_owner.getpass.getpass",
                side_effect=(OWNER_PASSWORD, OWNER_PASSWORD),
            ),
        ):
            call_command("bootstrap_development_owner", stdout=output)
        rendered = output.getvalue()
        self.assertIn(
            "bootstrap_development_owner: created users=1 human_principals=1 "
            "service_principals=1 revoked_sessions=0 revoked_human_credentials=0",
            rendered,
        )
        self.assertNotIn(OWNER_EMAIL, rendered)
        self.assertNotIn(OWNER_PASSWORD, rendered)

    def test_command_refuses_production_before_prompting(self) -> None:
        with (
            override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION),
            patch("accounts.management.commands.bootstrap_development_owner.input") as prompt,
            patch(
                "accounts.management.commands.bootstrap_development_owner.getpass.getpass"
            ) as secret_prompt,
            self.assertRaises(CommandError) as denied,
        ):
            call_command("bootstrap_development_owner")
        self.assertEqual(
            str(denied.exception),
            "bootstrap_development_owner: environment_denied",
        )
        prompt.assert_not_called()
        secret_prompt.assert_not_called()

    def test_legacy_plaintext_token_is_not_registered_in_django_admin(self) -> None:
        self.assertFalse(admin.site.is_registered(Token))


@override_settings(
    RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST,
    DEVELOPMENT_OWNER_LOGIN_ENABLED=True,
)
class DevelopmentOwnerLoginTests(TestCase):
    def setUp(self) -> None:
        bootstrap_development_owner(
            email=OWNER_EMAIL,
            password=OWNER_PASSWORD,
            reset_password=False,
            allow_test=True,
        )

    def test_owner_login_controls_use_the_cmp_form_control_contract(self) -> None:
        form = DevelopmentOwnerLoginForm()

        for field_name in ("email", "password"):
            with self.subTest(field_name=field_name):
                classes = form.fields[field_name].widget.attrs["class"].split()
                self.assertIn("form-control", classes)

    def test_csrf_login_rotates_session_creates_staff_session_and_uses_safe_next(self) -> None:
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["fixation-canary"] = "retained-safe-value"
        session.save()
        old_session_key = session.session_key
        login_url = f"{reverse('login')}?next=%2Fstudio%2F"
        page = client.get(login_url)
        csrf = client.cookies["csrftoken"].value

        missing_csrf = Client(enforce_csrf_checks=True).post(
            login_url,
            {"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        )
        self.assertEqual(missing_csrf.status_code, 403)
        response = client.post(
            login_url,
            {
                "email": OWNER_EMAIL,
                "password": OWNER_PASSWORD,
                "csrfmiddlewaretoken": csrf,
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/studio/")
        self.assertNotEqual(client.session.session_key, old_session_key)
        self.assertEqual(client.session["fixation-canary"], "retained-safe-value")
        self.assertEqual(StaffSession.objects.filter(revoked_at=None).count(), 1)
        self.assertEqual(client.get(reverse("studio:home")).status_code, 200)
        for checked in (page, response):
            self.assertIn("private", checked.headers["Cache-Control"])
            self.assertIn("no-store", checked.headers["Cache-Control"])
            self.assertEqual(checked.headers["X-Robots-Tag"], "noindex, nofollow")

        hostile = Client().post(
            f"{reverse('login')}?next=https%3A%2F%2Fevil.invalid%2F",
            {"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        )
        self.assertEqual(hostile.status_code, 302)
        self.assertEqual(hostile.headers["Location"], "/")

    def test_invalid_duplicate_inactive_and_rate_limited_attempts_are_safe(self) -> None:
        invalid = self.client.post(
            reverse("login"),
            {"email": OWNER_EMAIL, "password": "wrong-password"},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(
            invalid,
            "Sign-in was not successful. Check your details and try again.",
        )
        self.assertNotContains(invalid, OWNER_EMAIL)

        user = get_user_model().objects.get()
        user.is_active = False
        user.save(update_fields=("is_active",))
        inactive = self.client.post(
            reverse("login"),
            {"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        )
        self.assertContains(
            inactive,
            "Sign-in was not successful. Check your details and try again.",
        )

        for attempt in range(10):
            self.client.post(
                reverse("login"),
                {
                    "email": f"rate-{attempt}@example.test",
                    "password": "wrong-password",
                },
                REMOTE_ADDR="192.0.2.107",
            )
        limited = self.client.post(
            reverse("login"),
            {"email": "rate-final@example.test", "password": "wrong-password"},
            REMOTE_ADDR="192.0.2.107",
        )
        self.assertEqual(limited.status_code, 429)
        self.assertContains(
            limited,
            "Sign-in is temporarily unavailable. Wait a minute and try again.",
            status_code=429,
        )

    def test_only_the_exact_explicit_owner_can_use_the_development_form(self) -> None:
        user_model = get_user_model()
        duplicate = user_model.objects.create_user(
            username="duplicate-owner",
            email=OWNER_EMAIL.upper(),
            password=OWNER_PASSWORD,
        )
        duplicate_response = self.client.post(
            reverse("login"),
            {"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        )
        self.assertEqual(duplicate_response.status_code, 200)
        duplicate.delete()

        unassigned = user_model.objects.create_user(
            username="unassigned-staff",
            email="unassigned-staff@example.test",
            password=OWNER_PASSWORD,
            is_staff=True,
        )
        superuser_only = user_model.objects.create_user(
            username="flag-only-superuser",
            email="flag-only-superuser@example.test",
            password=OWNER_PASSWORD,
            is_staff=True,
            is_superuser=True,
        )
        nonstaff = user_model.objects.create_user(
            username="nonstaff-site-admin",
            email="nonstaff-site-admin@example.test",
            password=OWNER_PASSWORD,
            is_staff=False,
        )
        nonstaff.groups.add(Group.objects.get(name="site_admin"))

        for candidate in (unassigned, superuser_only, nonstaff):
            with self.subTest(username=candidate.username):
                response = self.client.post(
                    reverse("login"),
                    {"email": candidate.email, "password": OWNER_PASSWORD},
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    "Sign-in was not successful. Check your details and try again.",
                )
                self.assertNotContains(response, candidate.email)

        principal = APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN)
        principal.is_active = False
        principal.save(update_fields=("is_active", "updated_at"))
        principal_denied = self.client.post(
            reverse("login"),
            {"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        )
        self.assertEqual(principal_denied.status_code, 200)
        self.assertEqual(StaffSession.objects.count(), 0)

    def test_django_admin_login_remains_distinct_and_creates_staff_session(self) -> None:
        response = self.client.post(
            "/admin/login/?next=/admin/",
            {
                "username": OWNER_EMAIL,
                "password": OWNER_PASSWORD,
                "next": "/admin/",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")
        admin_home = self.client.get("/admin/")
        self.assertEqual(admin_home.status_code, 200)
        self.assertContains(admin_home, "Django admin")
        self.assertNotContains(admin_home, "Private staff workspace")
        self.assertEqual(StaffSession.objects.filter(revoked_at=None).count(), 1)

    def test_programmatic_staff_test_fixture_does_not_enable_runtime_password_login(
        self,
    ) -> None:
        staff = get_user_model().objects.create_user(
            username="programmatic-staff-fixture",
            email="programmatic-staff-fixture@example.test",
            password=OWNER_PASSWORD,
            is_staff=True,
        )
        fixture_client = Client()

        with override_settings(DEVELOPMENT_OWNER_LOGIN_ENABLED=False):
            self.assertTrue(
                fixture_client.login(
                    username=staff.username,
                    password=OWNER_PASSWORD,
                )
            )
            fixture_client.logout()

            public_login = fixture_client.post(
                reverse("login"),
                {"email": staff.email, "password": OWNER_PASSWORD},
            )
            django_admin_login = fixture_client.post(
                "/admin/login/?next=/admin/",
                {
                    "username": staff.username,
                    "password": OWNER_PASSWORD,
                    "next": "/admin/",
                },
            )

        self.assertEqual(public_login.status_code, 200)
        self.assertNotContains(public_login, "Owner email")
        self.assertEqual(django_admin_login.status_code, 200)
        self.assertNotIn("_auth_user_id", fixture_client.session)

        with override_settings(
            DEVELOPMENT_OWNER_LOGIN_ENABLED=False,
            TEST_PROGRAMMATIC_STAFF_PASSWORD_AUTHENTICATION=False,
        ):
            self.assertFalse(
                Client().login(
                    username=staff.username,
                    password=OWNER_PASSWORD,
                )
            )


class ManagementSlashRedirectTests(TestCase):
    def test_safe_methods_redirect_once_with_query_and_unsafe_methods_do_not(self) -> None:
        for source, target in (("/admin", "/admin/"), ("/studio", "/studio/")):
            for method in ("get", "head"):
                with self.subTest(source=source, method=method):
                    response = getattr(self.client, method)(f"{source}?view=safe")
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response.headers["Location"], f"{target}?view=safe")
                    self.assertIn("private", response.headers["Cache-Control"])
                    self.assertEqual(
                        response.headers["X-Robots-Tag"],
                        "noindex, nofollow",
                    )
            unsafe = self.client.post(source, {"canary": str(uuid.uuid4())})
            self.assertEqual(unsafe.status_code, 405)
            self.assertNotIn("Location", unsafe.headers)

        admin = self.client.get("/admin/", follow=False)
        studio = self.client.get("/studio/", follow=False)
        self.assertEqual(admin.status_code, 302)
        self.assertTrue(admin.headers["Location"].startswith("/admin/login/"))
        self.assertEqual(studio.status_code, 302)
        self.assertEqual(
            studio.headers["Location"],
            "/accounts/login/?next=%2Fstudio%2F",
        )
