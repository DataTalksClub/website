from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
    has_explicit_permission,
)
from accounts.studio_roles import AUDIT_BROWSE, ROLE_PERMISSIONS, synchronize_studio_roles
from accounts.studio_sessions import (
    SESSION_REFERENCE_KEY,
    DatabaseStaffSessionAdapter,
    DeterministicStaffSessionAdapter,
    StaffSessionEvidence,
    revoke_all_staff_sessions,
    revoke_staff_session,
)
from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import StaffSession
from studio.registry import STUDIO_HOME


class StudioRoleTests(TestCase):
    def test_manifest_synchronizes_idempotently_with_exact_permissions(self) -> None:
        first = synchronize_studio_roles()
        second = synchronize_studio_roles()

        self.assertEqual([group.name for group in first], list(ROLE_PERMISSIONS))
        self.assertEqual([group.pk for group in first], [group.pk for group in second])
        for name, expected in ROLE_PERMISSIONS.items():
            actual = {
                f"{permission.content_type.app_label}.{permission.codename}"
                for permission in Group.objects.get(name=name).permissions.select_related(
                    "content_type"
                )
            }
            self.assertEqual(actual, expected)

    def test_roles_are_additive_but_staff_and_superuser_are_not_permissions(self) -> None:
        user = make_studio_user(
            username="composed-staff",
            roles=("content_operator", "auditor"),
        )
        self.assertTrue(has_explicit_permission(user, "core.access_studio"))
        self.assertTrue(has_explicit_permission(user, AUDIT_BROWSE))

        unassigned = make_studio_user(username="unassigned")
        unassigned.is_superuser = True
        unassigned.save(update_fields=("is_superuser",))
        self.assertFalse(has_explicit_permission(unassigned, "core.access_studio"))
        self.assertFalse(has_explicit_permission(unassigned, "core.unknown_permission"))

    def test_management_command_is_idempotent(self) -> None:
        output = StringIO()
        call_command("sync_studio_roles", stdout=output)
        call_command("sync_studio_roles", stdout=output)
        self.assertEqual(Group.objects.filter(name__in=ROLE_PERMISSIONS).count(), 7)
        self.assertIn("Synchronized 7 Studio roles", output.getvalue())


class StudioAuthorizationTests(TestCase):
    def _evidence(self, user_id: int) -> StaffSessionEvidence:
        return StaffSessionEvidence(uuid.uuid4(), user_id, timezone.now())

    def test_anonymous_inactive_nonstaff_and_unassigned_staff_deny(self) -> None:
        anonymous = type("Anonymous", (), {"is_authenticated": False})()
        with self.assertRaises(StudioAuthenticationRequired):
            authorize_studio_request(
                request_user=anonymous,
                session_reference=None,
                capability=STUDIO_HOME,
            )

        for name, attributes in (
            ("inactive", {"is_active": False, "is_staff": True}),
            ("nonstaff", {"is_active": True, "is_staff": False}),
            ("unassigned", {"is_active": True, "is_staff": True}),
            (
                "superuser-only",
                {"is_active": True, "is_staff": True, "is_superuser": True},
            ),
        ):
            user = get_user_model().objects.create_user(username=name, **attributes)
            evidence = self._evidence(user.pk)
            with self.subTest(name=name), self.assertRaises(StudioAuthorizationDenied):
                authorize_studio_request(
                    request_user=user,
                    session_reference=evidence.session_id,
                    capability=STUDIO_HOME,
                    adapter=DeterministicStaffSessionAdapter(evidence),
                )

    def test_missing_denied_object_and_hidden_field_fail_closed(self) -> None:
        user = make_studio_user(username="policy-user", roles=("content_operator",))
        evidence = self._evidence(user.pk)
        adapter = DeterministicStaffSessionAdapter(evidence)

        with self.assertRaises(StudioAuthorizationDenied):
            authorize_studio_request(
                request_user=user,
                session_reference=evidence.session_id,
                capability=STUDIO_HOME,
                adapter=adapter,
                target=object(),
            )

        denied_object = replace(
            STUDIO_HOME,
            object_policy=lambda actor, target: False,
            object_scope=lambda actor, queryset: queryset,
        )
        with self.assertRaises(StudioAuthorizationDenied):
            authorize_studio_request(
                request_user=user,
                session_reference=evidence.session_id,
                capability=denied_object,
                adapter=adapter,
                target=object(),
            )

        hidden_field = replace(STUDIO_HOME, field_policy=lambda actor, field: field == "safe")
        with self.assertRaises(StudioAuthorizationDenied):
            authorize_studio_request(
                request_user=user,
                session_reference=evidence.session_id,
                capability=hidden_field,
                adapter=adapter,
                fields=("hidden",),
            )

    def test_function_object_and_field_hooks_allow_or_fail_closed(self) -> None:
        user = make_studio_user(username="hook-user", roles=("content_operator",))
        evidence = self._evidence(user.pk)
        adapter = DeterministicStaffSessionAdapter(evidence)
        allowed = replace(
            STUDIO_HOME,
            function_policy=lambda actor, session: True,
            object_policy=lambda actor, target: True,
            object_scope=lambda actor, queryset: queryset,
            field_policy=lambda actor, field: field == "visible",
        )
        principal = authorize_studio_request(
            request_user=user,
            session_reference=evidence.session_id,
            capability=allowed,
            adapter=adapter,
            target=object(),
            fields=("visible",),
        )
        self.assertEqual(principal.user.pk, user.pk)

        def raises(*args):
            del args
            raise RuntimeError("policy unavailable")

        denied = (
            replace(allowed, function_policy=lambda actor, session: False),
            replace(allowed, function_policy=raises),
            replace(allowed, object_policy=raises),
            replace(allowed, field_policy=raises),
        )
        for capability in denied:
            with self.subTest(capability=capability), self.assertRaises(StudioAuthorizationDenied):
                authorize_studio_request(
                    request_user=user,
                    session_reference=evidence.session_id,
                    capability=capability,
                    adapter=adapter,
                    target=object(),
                    fields=("visible",),
                )


class StaffSessionSchemaTests(TestCase):
    def test_revocation_constraint_and_named_index_exist(self) -> None:
        user = make_studio_user(username="schema-staff", roles=("content_operator",))
        authenticated_at = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffSession.objects.create(
                    user=user,
                    authenticated_at=authenticated_at,
                    revoked_at=authenticated_at - timedelta(seconds=1),
                )
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                StaffSession._meta.db_table,
            )
        self.assertIn("core_staff_session_revoked_after_auth", constraints)
        self.assertTrue(constraints["core_staff_session_user"]["index"])


@override_settings(NOINDEX=False)
class StudioSessionRequestTests(TestCase):
    def test_safe_path_only_login_redirect_and_exact_private_headers(self) -> None:
        response = self.client.get(f"{reverse('studio:home')}?request_id=do-not-reflect")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            f"{reverse('login')}?next=%2Fstudio%2F",
        )
        self.assertNotContains(response, "do-not-reflect", status_code=302)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_session_permission_and_user_state_are_rechecked_next_request(self) -> None:
        user = make_studio_user(username="fresh-state", roles=("content_operator",))
        client = authenticated_studio_client(user)
        self.assertEqual(client.get(reverse("studio:home")).status_code, 200)
        self.assertEqual(StaffSession.objects.filter(user=user, revoked_at=None).count(), 1)

        group = Group.objects.get(name="content_operator")
        group.permissions.clear()
        self.assertEqual(client.get(reverse("studio:home")).status_code, 403)

        synchronize_studio_roles()
        self.assertEqual(client.get(reverse("studio:home")).status_code, 200)
        user.is_active = False
        user.save(update_fields=("is_active",))
        self.assertEqual(client.get(reverse("studio:home")).status_code, 403)

    def test_single_and_all_session_revocation_take_effect_next_request(self) -> None:
        user = make_studio_user(username="revoked-staff", roles=("content_operator",))
        first = authenticated_studio_client(user)
        second = authenticated_studio_client(user)
        self.assertEqual(StaffSession.objects.filter(user=user, revoked_at=None).count(), 2)

        first_id = uuid.UUID(first.session[SESSION_REFERENCE_KEY])
        self.assertTrue(revoke_staff_session(first_id, user=user))
        self.assertEqual(first.get(reverse("studio:home")).status_code, 403)
        self.assertEqual(second.get(reverse("studio:home")).status_code, 200)

        self.assertEqual(revoke_all_staff_sessions(user), 1)
        self.assertEqual(second.get(reverse("studio:home")).status_code, 403)

    def test_missing_malformed_wrong_and_adapter_error_sessions_deny(self) -> None:
        user = make_studio_user(username="adapter-staff", roles=("content_operator",))
        evidence = StaffSessionEvidence(uuid.uuid4(), user.pk, timezone.now())
        for reference, adapter in (
            (None, DatabaseStaffSessionAdapter()),
            ("malformed", DeterministicStaffSessionAdapter(evidence)),
            (uuid.uuid4(), DeterministicStaffSessionAdapter(evidence)),
            (
                evidence.session_id,
                DeterministicStaffSessionAdapter(evidence, unavailable=True),
            ),
        ):
            with self.subTest(reference=reference), self.assertRaises(StudioAuthorizationDenied):
                authorize_studio_request(
                    request_user=user,
                    session_reference=reference,
                    capability=STUDIO_HOME,
                    adapter=adapter,
                )

    def test_logout_revokes_exact_bound_session_and_secret_never_enters_model(self) -> None:
        user = make_studio_user(username="logout-staff", roles=("content_operator",))
        client = authenticated_studio_client(user)
        raw_django_session_key = client.cookies["sessionid"].value
        reference = uuid.UUID(client.session[SESSION_REFERENCE_KEY])
        record = StaffSession.objects.get(id=reference)
        self.assertNotIn(raw_django_session_key, repr(record))
        self.assertNotIn(raw_django_session_key, str(record))

        client.logout()
        record.refresh_from_db()
        self.assertIsNotNone(record.revoked_at)
        self.assertEqual(client.get(reverse("studio:home")).status_code, 302)

    def test_revocation_time_cannot_predate_authentication(self) -> None:
        user = make_studio_user(username="clock-staff", roles=("content_operator",))
        client = authenticated_studio_client(user)
        record = StaffSession.objects.get(id=client.session[SESSION_REFERENCE_KEY])
        with self.assertRaises(ValueError):
            revoke_staff_session(
                record.id,
                user=user,
                at=record.authenticated_at - timedelta(seconds=1),
            )

    def test_custom_permission_exists_without_assigning_test_fixture_to_roles(self) -> None:
        fixture_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="execute_high_risk_fixture",
        )
        self.assertFalse(
            Group.objects.filter(
                name__in=ROLE_PERMISSIONS,
                permissions=fixture_permission,
            ).exists()
        )
