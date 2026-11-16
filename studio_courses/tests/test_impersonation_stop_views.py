from django.test import Client
from django.urls import reverse

from .impersonation_base import (
    ImpersonationStudioCoursesViewTestBase,
    admin_credentials,
)


class ImpersonationBannerStudioCoursesViewTests(ImpersonationStudioCoursesViewTestBase):
    def test_impersonation_banner_shown_when_logged_in_as_student(self):
        login_url = self.login_as_user_url()
        course_list_url = reverse("course_list")
        stop_url = reverse("stop_impersonating")

        self.client.login(**admin_credentials)
        response = self.client.post(login_url)

        self.assertEqual(response.status_code, 302)
        response = self.client.get(course_list_url)

        self.assertContains(response, "impersonation-banner")
        self.assertContains(
            response,
            f"You are logged in as <strong>{self.user.email}</strong>",
            html=True,
        )
        self.assertContains(response, "Return to admin account")
        self.assertContains(response, stop_url)


class StopImpersonatingStudioCoursesViewTests(ImpersonationStudioCoursesViewTestBase):
    def test_stop_impersonating_restores_admin_account(self):
        login_url = self.login_as_user_url()
        course_list_url = reverse("course_list")
        stop_url = reverse("stop_impersonating")
        studio_courses_course_list_url = reverse("studio_courses_course_list")

        self.client.login(**admin_credentials)
        self.client.post(login_url)

        response = self.client.get(course_list_url)
        self.assertEqual(response.wsgi_request.user, self.user)

        response = self.client.post(stop_url)

        self.assertRedirects(response, studio_courses_course_list_url)
        response = self.client.get(course_list_url)
        self.assertEqual(response.wsgi_request.user, self.admin_user)
        # The courses index carries its own stylesheet since the design system rebuild
        # (issue #179), so the banner's class name is always in the page as a CSS
        # selector.  Only the banner's own markup says it is being shown.
        self.assertNotContains(response, 'id="impersonation-banner"')
        self.assertNotContains(response, "Return to admin account")


class StopImpersonatingCsrfStudioCoursesViewTests(ImpersonationStudioCoursesViewTestBase):
    def impersonated_csrf_client(self):
        csrf_client = Client(enforce_csrf_checks=True)
        login_url = self.login_as_user_url()
        course_list_url = reverse("course_list")
        csrf_client.login(**admin_credentials)
        response = csrf_client.get(course_list_url)
        self.assertEqual(response.status_code, 200)
        admin_csrf_token = csrf_client.cookies["csrftoken"].value

        csrf_payload = {"csrfmiddlewaretoken": admin_csrf_token}
        response = csrf_client.post(login_url, csrf_payload)
        self.assertEqual(response.status_code, 302)

        response = csrf_client.get(course_list_url)
        self.assertContains(response, "impersonation-banner")
        self.assertEqual(response.wsgi_request.user, self.user)

        return csrf_client, admin_csrf_token

    def assert_still_impersonating(self, client):
        response = client.get(reverse("course_list"))
        self.assertEqual(response.wsgi_request.user, self.user)

    def test_stop_rejects_missing_token(self):
        client, _ = self.impersonated_csrf_client()

        response = client.post(reverse("stop_impersonating"))

        self.assertEqual(response.status_code, 403)
        self.assert_still_impersonating(client)

    def test_stop_rejects_pre_switch_token(self):
        client, stale_token = self.impersonated_csrf_client()

        response = client.post(
            reverse("stop_impersonating"),
            {"csrfmiddlewaretoken": stale_token},
        )

        self.assertEqual(response.status_code, 403)
        self.assert_still_impersonating(client)

    def test_stop_accepts_fresh_token(self):
        client, stale_token = self.impersonated_csrf_client()
        fresh_token = client.cookies["csrftoken"].value
        self.assertNotEqual(fresh_token, stale_token)

        response = client.post(
            reverse("stop_impersonating"),
            {"csrfmiddlewaretoken": fresh_token},
        )

        self.assertRedirects(response, reverse("studio_courses_course_list"))
        response = client.get(reverse("course_list"))
        self.assertEqual(response.wsgi_request.user, self.admin_user)

    def test_stop_get_does_not_restore_identity(self):
        client, _ = self.impersonated_csrf_client()

        response = client.get(reverse("stop_impersonating"))

        self.assertEqual(response.status_code, 405)
        self.assert_still_impersonating(client)
