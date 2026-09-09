"""BE-01 containment: impersonation policy, break-glass admin, deployment checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from django.urls import reverse

from accounts.models import CustomUser
from accounts.studio_sessions import (
    create_staff_session,
    revoke_staff_session,
)
from accounts.studio_test_support import make_studio_user
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent
from website.admin_gate import BreakGlassAdminGateMiddleware
from website.checks import admin_route_names, check_production_admin_exposure
from website.loginas_policy import can_login_as

PRODUCTION = override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION)


def staff_requester(**overrides):
    principal = dict(
        is_authenticated=True,
        is_active=True,
        is_staff=True,
        is_superuser=False,
    )
    principal.update(overrides)
    return SimpleNamespace(user=SimpleNamespace(**principal))


def learner(**overrides):
    fields = dict(
        is_active=True,
        is_staff=False,
        is_superuser=False,
        identity_state=CustomUser.IdentityState.LEGACY,
    )
    fields.update(overrides)
    return CustomUser(**fields)


class CanLoginAsPolicyTests(SimpleTestCase):
    def test_active_staff_can_impersonate_an_active_learner(self):
        self.assertTrue(can_login_as(staff_requester(), learner()))

    def test_non_staff_inactive_or_unauthenticated_requesters_are_denied(self):
        for requester in (
            staff_requester(is_staff=False),
            staff_requester(is_active=False),
            staff_requester(is_authenticated=False),
        ):
            with self.subTest(requester=requester):
                self.assertFalse(can_login_as(requester, learner()))

    def test_staff_superuser_inactive_and_quarantined_targets_are_denied(self):
        for target in (
            learner(is_staff=True),
            learner(is_superuser=True),
            learner(is_active=False),
            learner(identity_state=CustomUser.IdentityState.QUARANTINED),
        ):
            with self.subTest(target=target):
                self.assertFalse(can_login_as(staff_requester(), target))


class AdminUrlMountingTests(SimpleTestCase):
    @staticmethod
    def _urlpatterns_for(break_glass: bool):
        with override_settings(ADMIN_BREAK_GLASS=break_glass):
            spec = importlib.util.spec_from_file_location(
                f"website.urls_break_glass_{break_glass}",
                str(Path("website/urls.py")),
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module.urlpatterns

    def test_break_glass_mode_mounts_admin_and_loginas(self):
        mounted = admin_route_names(self._urlpatterns_for(True))

        self.assertEqual(mounted, {"admin", "loginas"})

    def test_default_mode_does_not_mount_admin_or_loginas(self):
        mounted = admin_route_names(self._urlpatterns_for(False))

        self.assertEqual(mounted, set())


class BreakGlassGateTests(TestCase):
    def setUp(self):
        self.marker = HttpResponse("through", status=200)
        self.middleware = BreakGlassAdminGateMiddleware(lambda request: self.marker)

    def request(self, path="/admin/login/", user=None, session_store=None):
        factory_request = RequestFactory().get(path)
        if user is not None:
            factory_request.user = user
        if session_store is not None:
            factory_request.session = session_store
        return factory_request

    def make_superuser(self, username="break-glass-super"):
        user = get_user_model().objects.create_user(
            username=username,
            email="",
            password="s3cret-value!",
            is_staff=True,
            is_superuser=True,
        )
        return user

    def staff_session(self, user):
        store = SessionStore()
        store.create()
        # create_staff_session only reads request.session; a bare factory
        # request carries it without involving session middleware.
        request = RequestFactory().get("/")
        request.session = store
        session = create_staff_session(user=user, request=request)
        return store, session

    @PRODUCTION
    def test_anonymous_requests_are_denied_without_audit_noise(self):
        with override_settings(ADMIN_BREAK_GLASS=True):
            response = self.middleware(self.request())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            AuditEvent.objects.filter(action="admin.break_glass_denied").count(),
            0,
        )

    @PRODUCTION
    def test_staff_without_superuser_is_denied_and_audited(self):
        user = make_studio_user(username="break-glass-staff")
        store, _session = self.staff_session(user)

        with override_settings(ADMIN_BREAK_GLASS=True):
            response = self.middleware(self.request(user=user, session_store=store))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="admin.break_glass_denied",
                outcome=AuditEvent.Outcome.DENIED,
                actor_id=user.pk,
            ).count(),
            1,
        )

    @PRODUCTION
    def test_superuser_without_staff_evidence_is_denied_and_audited(self):
        user = self.make_superuser()

        with override_settings(ADMIN_BREAK_GLASS=True):
            response = self.middleware(self.request(user=user, session_store=SessionStore()))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            AuditEvent.objects.filter(action="admin.break_glass_denied").count(),
            1,
        )

    @PRODUCTION
    def test_superuser_with_live_staff_evidence_passes(self):
        user = self.make_superuser()
        store, _session = self.staff_session(user)

        with override_settings(ADMIN_BREAK_GLASS=True):
            response = self.middleware(self.request(user=user, session_store=store))

        self.assertIs(response, self.marker)
        self.assertEqual(
            AuditEvent.objects.filter(action="admin.break_glass_denied").count(),
            0,
        )

    @PRODUCTION
    def test_revoked_staff_evidence_is_denied_and_audited(self):
        user = self.make_superuser()
        store, session = self.staff_session(user)
        revoke_staff_session(session.id, user=user)

        with override_settings(ADMIN_BREAK_GLASS=True):
            response = self.middleware(self.request(user=user, session_store=store))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            AuditEvent.objects.filter(action="admin.break_glass_denied").count(),
            1,
        )

    def test_gate_is_inert_outside_production_break_glass(self):
        user = self.make_superuser()

        response = self.middleware(self.request(user=user))

        self.assertIs(response, self.marker)


class ProductionAdminExposureCheckTests(TestCase):
    def test_non_production_is_not_policed(self):
        self.assertEqual(check_production_admin_exposure(None), [])

    @PRODUCTION
    def test_glass_enabled_with_gate_passes(self):
        with override_settings(ADMIN_BREAK_GLASS=True):
            errors = check_production_admin_exposure(None)

        self.assertEqual(errors, [])

    @PRODUCTION
    def test_mounted_admin_without_break_glass_fails(self):
        # The test-process urlconf mounted admin at import time; the check
        # must reject that combination exactly as it would in a deployment.
        with override_settings(ADMIN_BREAK_GLASS=False):
            errors = check_production_admin_exposure(None)

        self.assertEqual([error.id for error in errors], ["website.E001"])

    @PRODUCTION
    def test_glass_enabled_without_gate_fails(self):
        without_gate = [
            middleware
            for middleware in settings.MIDDLEWARE
            if middleware != "website.admin_gate.BreakGlassAdminGateMiddleware"
        ]
        with override_settings(ADMIN_BREAK_GLASS=True, MIDDLEWARE=without_gate):
            errors = check_production_admin_exposure(None)

        self.assertEqual([error.id for error in errors], ["website.E002"])


class ImpersonationExitContractTests(TestCase):
    def setUp(self):
        self.user = make_studio_user(username="impersonation-staff")

    def test_stop_impersonating_rejects_get(self):
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("stop_impersonating"))

        self.assertEqual(response.status_code, 405)

    # The stop view's stale-CSRF acceptance is an adopted constraint pinned by
    # studio_courses.tests.test_impersonation_stop_views, so only the method
    # contract is re-checked here; the package's /admin/logout/ exit is the
    # surface this batch closes.
    def test_admin_logout_exit_rejects_get(self):
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("loginas-logout"))

        self.assertEqual(response.status_code, 405)

    def test_admin_logout_exit_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(reverse("loginas-logout"))

        self.assertEqual(response.status_code, 403)
